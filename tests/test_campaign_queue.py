"""Cramzz campaign contracts stay exact-copy, short-lived, and local-only."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from x_agent.campaigns import ApprovalQueue, ApprovalRecord, CampaignBrief, ContentItem, ContentStatus
from x_agent.campaigns.models import validate_item_against_brief


ROOT = Path(__file__).resolve().parents[1]


def load_brief() -> CampaignBrief:
    raw = json.loads((ROOT / "campaigns/cramzz/packet-panic.example.json").read_text())
    return CampaignBrief.model_validate(raw)


def load_item() -> ContentItem:
    raw = json.loads((ROOT / "campaigns/cramzz/content-item.example.json").read_text())
    return ContentItem.model_validate(raw)


def test_example_brief_and_item_are_valid() -> None:
    assert validate_item_against_brief(load_item(), load_brief()) == []


def test_unsupported_metric_and_implied_sponsorship_are_blocked() -> None:
    item = load_item().model_copy(
        update={
            "exact_copy": "10,000 players love our official partner.",
            "evidence_refs": [],
            "sponsor_related": True,
        }
    )
    blockers = validate_item_against_brief(item, load_brief())
    assert any("require evidence" in blocker for blocker in blockers)
    assert any("disclose" in blocker for blocker in blockers)


def test_numeric_claim_requires_relevant_cited_fact() -> None:
    item = load_item().model_copy(
        update={
            "exact_copy": "1M users completed Packet Panic.",
            "evidence_refs": ["six-hop-ttl"],
        }
    )
    blockers = validate_item_against_brief(item, load_brief())
    assert any("not supported by cited facts: 1m" in blocker for blocker in blockers)


def test_exact_copy_approval_and_publication_record(tmp_path: Path) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    item = queue.add(load_item(), load_brief())
    assert item.status is ContentStatus.DRAFT
    queue.submit(item.id)
    record = queue.approve([item.id], approver="Abhinav")
    assert record.item_hashes[item.id] == item.content_hash()
    published = queue.mark_published(item.id)
    assert published.status is ContentStatus.PUBLISHED
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [entry["to"] for entry in audit] == ["draft", "pending_approval", "approved", "published"]
    assert all("copy" not in entry and "approver" not in entry for entry in audit)


def test_edit_invalidates_approval_and_requires_fresh_review(tmp_path: Path) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    item = queue.add(load_item(), load_brief())
    queue.submit(item.id)
    queue.approve([item.id], approver="Abhinav")
    changed = queue.edit(item.id, load_brief(), copy=f"{item.exact_copy[:-1]}!")
    assert changed.status is ContentStatus.PENDING_APPROVAL
    assert item.id not in queue.approvals
    with pytest.raises(ValueError, match="unexpired exact-copy approval"):
        queue.mark_published(item.id)


def test_expired_approval_cannot_be_recorded_as_published(tmp_path: Path) -> None:
    current = [datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)]
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: current[0])
    item = queue.add(load_item(), load_brief())
    queue.submit(item.id)
    queue.approve([item.id], approver="Abhinav", lifetime=timedelta(hours=1))
    current[0] += timedelta(hours=2)
    with pytest.raises(ValueError, match="unexpired exact-copy approval"):
        queue.mark_published(item.id)
    assert queue.items[item.id].status is ContentStatus.EXPIRED


def test_generated_content_cannot_arrive_preapproved(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl")
    item = load_item().model_copy(update={"status": ContentStatus.APPROVED})
    with pytest.raises(ValueError, match="only as draft"):
        queue.add(item, load_brief())


def test_published_record_is_immutable(tmp_path: Path) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    item = queue.add(load_item(), load_brief())
    queue.submit(item.id)
    queue.approve([item.id], approver="Abhinav")
    queue.mark_published(item.id)
    with pytest.raises(ValueError, match="immutable"):
        queue.edit(item.id, load_brief(), copy="Changed after publishing")
    with pytest.raises(ValueError, match="immutable"):
        queue.reject(item.id)


def test_edit_revalidates_brief_before_invalidating_approval(tmp_path: Path) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    item = queue.add(load_item(), load_brief())
    queue.submit(item.id)
    queue.approve([item.id], approver="Abhinav")

    with pytest.raises(ValueError, match="prohibited claim"):
        queue.edit(item.id, load_brief(), copy="This is the best networking game.")

    assert queue.items[item.id].status is ContentStatus.APPROVED
    assert item.id in queue.approvals


def test_approval_fails_if_copy_changed_after_display(tmp_path: Path) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    first = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    item = first.add(load_item(), load_brief())
    first.submit(item.id)
    displayed = {item.id: first.items[item.id].content_hash()}

    second = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    second.edit(item.id, load_brief(), copy=f"{item.exact_copy[:-1]}!")

    with pytest.raises(ValueError, match="changed after it was displayed"):
        first.approve([item.id], approver="Abhinav", expected_hashes=displayed)
    assert first.items[item.id].status is ContentStatus.PENDING_APPROVAL


def test_loaded_approval_must_be_aware_and_no_longer_than_24_hours() -> None:
    start = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    digest = "a" * 64
    with pytest.raises(ValueError, match="within 24 hours"):
        ApprovalRecord(
            item_hashes={"packet-panic-launch-post-v1": digest},
            approver="Abhinav",
            approved_at=start,
            expires_at=start + timedelta(hours=25),
        )
    with pytest.raises(ValueError, match="timezone"):
        ApprovalRecord(
            item_hashes={"packet-panic-launch-post-v1": digest},
            approver="Abhinav",
            approved_at=start.replace(tzinfo=None),
            expires_at=(start + timedelta(hours=1)).replace(tzinfo=None),
        )


def test_approved_item_without_record_expires_fail_closed(tmp_path: Path) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    queue = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    item = queue.add(load_item(), load_brief())
    queue.submit(item.id)
    raw = json.loads((tmp_path / "queue.json").read_text())
    raw["items"][0]["status"] = "approved"
    (tmp_path / "queue.json").write_text(json.dumps(raw))

    reloaded = ApprovalQueue(tmp_path / "queue.json", tmp_path / "audit.jsonl", clock=lambda: now)
    assert reloaded.expire_stale() == [item.id]
    assert reloaded.items[item.id].status is ContentStatus.EXPIRED


def test_queue_files_and_directory_are_private(tmp_path: Path) -> None:
    queue_dir = tmp_path / "campaigns"
    queue = ApprovalQueue(queue_dir / "queue.json", queue_dir / "audit.jsonl")
    queue.add(load_item(), load_brief())

    assert stat.S_IMODE(queue_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((queue_dir / "queue.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((queue_dir / "audit.jsonl").stat().st_mode) == 0o600
