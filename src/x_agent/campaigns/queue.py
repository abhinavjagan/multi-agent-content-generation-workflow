"""Persistent exact-copy approval queue with an append-only audit trail."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

from filelock import FileLock

from .models import (
    ApprovalRecord,
    CampaignBrief,
    ContentItem,
    ContentStatus,
    validate_item_against_brief,
)


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalQueue:
    """A local queue. Marking published records state; it never sends content."""

    def __init__(self, queue_path: Path, audit_path: Path, clock: Clock = _utc_now) -> None:
        self.queue_path = Path(queue_path).expanduser().resolve()
        self.audit_path = Path(audit_path).expanduser().resolve()
        self.lock_path = self.queue_path.with_suffix(f"{self.queue_path.suffix}.lock")
        self.clock = clock
        self.items: dict[str, ContentItem] = {}
        self.approvals: dict[str, ApprovalRecord] = {}
        self._load()

    def _load(self) -> None:
        self.items = {}
        self.approvals = {}
        if not self.queue_path.exists():
            return
        raw = json.loads(self.queue_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("campaign queue root must be an object")
        if raw.get("version") != 1:
            raise ValueError("unsupported campaign queue version")
        raw_items = raw.get("items", [])
        raw_approvals = raw.get("approvals", {})
        if not isinstance(raw_items, list) or not isinstance(raw_approvals, dict):
            raise ValueError("campaign queue items/approvals have invalid types")
        for payload in raw_items:
            item = ContentItem.model_validate(payload)
            if item.id in self.items:
                raise ValueError(f"duplicate content item in queue: {item.id}")
            self.items[item.id] = item
        for item_id, payload in raw_approvals.items():
            if item_id not in self.items:
                raise ValueError(f"approval references unknown content item: {item_id}")
            record = ApprovalRecord.model_validate(payload)
            if set(record.item_hashes) != {item_id}:
                raise ValueError(f"approval record scope does not match content item: {item_id}")
            if self.items[item_id].status not in {ContentStatus.APPROVED, ContentStatus.PUBLISHED}:
                raise ValueError(f"approval exists for non-approved content item: {item_id}")
            self.approvals[item_id] = record

    @staticmethod
    def _restrict(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except OSError:  # pragma: no cover - non-POSIX or restricted filesystem
            pass

    def _ensure_private_parents(self) -> None:
        for parent in {self.queue_path.parent, self.audit_path.parent}:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._restrict(parent, 0o700)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("campaign queue clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize mutations and refresh state before making a decision."""
        self._ensure_private_parents()
        with FileLock(str(self.lock_path), timeout=10):
            self._restrict(self.lock_path, 0o600)
            self._load()
            yield

    def _save(self) -> None:
        self._ensure_private_parents()
        payload = {
            "version": 1,
            "items": [item.model_dump(mode="json", by_alias=True) for item in self.items.values()],
            "approvals": {
                item_id: record.model_dump(mode="json")
                for item_id, record in self.approvals.items()
            },
        }
        temporary = self.queue_path.with_suffix(f"{self.queue_path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._restrict(temporary, 0o600)
        os.replace(temporary, self.queue_path)
        self._restrict(self.queue_path, 0o600)

    def _audit(self, item_id: str, previous: ContentStatus | None, current: ContentStatus, content_hash: str) -> None:
        self._ensure_private_parents()
        record = {
            "item_id": item_id,
            "content_hash": content_hash,
            "from": previous.value if previous else None,
            "to": current.value,
            "timestamp": self._now().isoformat(),
        }
        descriptor = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._restrict(self.audit_path, 0o600)

    def add(self, item: ContentItem, brief: CampaignBrief) -> ContentItem:
        with self._locked():
            if item.status is not ContentStatus.DRAFT:
                raise ValueError("generated content can enter the queue only as draft")
            if item.id in self.items:
                raise ValueError(f"content item already exists: {item.id}")
            blockers = validate_item_against_brief(item, brief)
            if blockers:
                raise ValueError("; ".join(blockers))
            item = ContentItem.model_validate(
                item.model_copy(update={"updated_at": self._now()}).model_dump()
            )
            self.items[item.id] = item
            self._save()
            self._audit(item.id, None, item.status, item.content_hash())
            return item

    def grouped(self) -> dict[str, list[ContentItem]]:
        with self._locked():
            self._expire_stale_unlocked()
            groups = {status.value: [] for status in ContentStatus}
            for item in self.items.values():
                groups[item.status.value].append(item)
            for values in groups.values():
                values.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
            return groups

    def submit(self, item_id: str) -> ContentItem:
        with self._locked():
            item = self._item(item_id)
            if item.status not in {ContentStatus.DRAFT, ContentStatus.REJECTED, ContentStatus.EXPIRED}:
                raise ValueError(f"cannot submit item in {item.status.value} state")
            return self._transition(item, ContentStatus.PENDING_APPROVAL, invalidate=True)

    def edit(self, item_id: str, brief: CampaignBrief, **changes: object) -> ContentItem:
        """Edit a record and re-run the same brief guardrails used on enqueue."""
        with self._locked():
            item = self._item(item_id)
            if item.status is ContentStatus.PUBLISHED:
                raise ValueError("published content is immutable; create a new item for revised copy")
            if "copy" in changes:
                if "exact_copy" in changes:
                    raise ValueError("provide only copy or exact_copy, not both")
                changes["exact_copy"] = changes.pop("copy")
            immutable = {"id", "experiment_slug", "status", "created_at", "updated_at"}
            if immutable.intersection(changes):
                raise ValueError("identity, status, and timestamps cannot be edited")
            editable = {
                "channel", "content_type", "exact_copy", "context_url", "media_url",
                "alt_text", "evidence_refs", "sponsor_related",
            }
            unknown = sorted(set(changes) - editable)
            if unknown:
                raise ValueError(f"unknown editable field(s): {', '.join(unknown)}")
            next_status = ContentStatus.DRAFT if item.status is ContentStatus.DRAFT else ContentStatus.PENDING_APPROVAL
            updated = item.model_copy(update={**changes, "status": next_status, "updated_at": self._now()})
            updated = ContentItem.model_validate(updated.model_dump())
            blockers = validate_item_against_brief(updated, brief)
            if blockers:
                raise ValueError("; ".join(blockers))
            previous = item.status
            self.items[item_id] = updated
            self.approvals.pop(item_id, None)
            self._save()
            self._audit(item_id, previous, updated.status, updated.content_hash())
            return updated

    def approve(
        self,
        item_ids: Iterable[str],
        approver: str,
        lifetime: timedelta = timedelta(hours=24),
        expected_hashes: Mapping[str, str] | None = None,
    ) -> ApprovalRecord:
        with self._locked():
            ids = list(dict.fromkeys(item_ids))
            if not ids:
                raise ValueError("at least one item must be approved")
            if lifetime <= timedelta(0) or lifetime > timedelta(hours=24):
                raise ValueError("approval lifetime must be greater than zero and no more than 24 hours")
            items = [self._item(item_id) for item_id in ids]
            if any(item.status is not ContentStatus.PENDING_APPROVAL for item in items):
                raise ValueError("only pending_approval items can be approved")
            current_hashes = {item.id: item.content_hash() for item in items}
            if expected_hashes is not None and current_hashes != dict(expected_hashes):
                raise ValueError("content changed after it was displayed; review the exact copy again")
            now = self._now()
            record = ApprovalRecord(
                item_hashes=current_hashes,
                approver=approver,
                approved_at=now,
                expires_at=now + lifetime,
            )
            transitions: list[tuple[ContentItem, ContentStatus]] = []
            for item in items:
                previous = item.status
                item.status = ContentStatus.APPROVED
                item.updated_at = now
                # Store an item-scoped record so no campaign-level approval can leak across items.
                self.approvals[item.id] = record.model_copy(update={"item_hashes": {item.id: item.content_hash()}})
                transitions.append((item, previous))
            self._save()
            for item, previous in transitions:
                self._audit(item.id, previous, item.status, item.content_hash())
            return record

    def mark_published(self, item_id: str) -> ContentItem:
        """Record a human-confirmed publication. No network operation occurs."""
        with self._locked():
            self._expire_stale_unlocked()
            item = self._item(item_id)
            record = self.approvals.get(item_id)
            if item.status is not ContentStatus.APPROVED or record is None:
                raise ValueError("item needs an unexpired exact-copy approval before publication is recorded")
            if record.item_hashes.get(item_id) != item.content_hash():
                raise ValueError("content changed after approval")
            return self._transition(item, ContentStatus.PUBLISHED, invalidate=False)

    def reject(self, item_id: str) -> ContentItem:
        with self._locked():
            item = self._item(item_id)
            if item.status is ContentStatus.PUBLISHED:
                raise ValueError("a published record is immutable")
            return self._transition(item, ContentStatus.REJECTED, invalidate=True)

    def expire_stale(self) -> list[str]:
        with self._locked():
            return self._expire_stale_unlocked()

    def _expire_stale_unlocked(self) -> list[str]:
        now = self._now()
        expired: list[str] = []
        for item_id, item in list(self.items.items()):
            if item.status is not ContentStatus.APPROVED:
                continue
            record = self.approvals.get(item_id)
            if record is None or now >= record.expires_at or record.item_hashes.get(item_id) != item.content_hash():
                self._transition(item, ContentStatus.EXPIRED, invalidate=True)
                expired.append(item_id)
        return expired

    def _transition(self, item: ContentItem, status: ContentStatus, invalidate: bool) -> ContentItem:
        previous = item.status
        item.status = status
        item.updated_at = self._now()
        if invalidate:
            self.approvals.pop(item.id, None)
        self._save()
        self._audit(item.id, previous, status, item.content_hash())
        return item

    def _item(self, item_id: str) -> ContentItem:
        try:
            return self.items[item_id]
        except KeyError as exc:
            raise ValueError(f"unknown content item: {item_id}") from exc
