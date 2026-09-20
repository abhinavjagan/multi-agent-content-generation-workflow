"""Validated data contracts for approval-gated campaign content."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


_CONTENT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,95}$")
_FACT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _https_url(value: AnyHttpUrl | None, field_name: str) -> AnyHttpUrl | None:
    if value is None:
        return None
    if value.scheme != "https":
        raise ValueError(f"{field_name} must use https")
    if value.username or value.password:
        raise ValueError(f"{field_name} must not contain credentials")
    return value


class ContentStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ContentType(str, Enum):
    POST = "post"
    REPLY = "reply"


class FactRecord(BaseModel):
    """One claim a draft is allowed to rely on."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    claim: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=500)

    @field_validator("claim", "source")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class CampaignBrief(BaseModel):
    """The facts and boundaries supplied to the drafting workflow."""

    model_config = ConfigDict(extra="forbid")

    experiment_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    audience: str = Field(min_length=1, max_length=500)
    hypothesis: str = Field(min_length=1, max_length=800)
    canonical_url: AnyHttpUrl
    launch_starts_at: datetime
    launch_ends_at: datetime
    verified_facts: list[FactRecord] = Field(min_length=1, max_length=100)
    tone_constraints: list[str] = Field(default_factory=list, max_length=30)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=100)
    sponsor_disclosure: str = Field(min_length=1, max_length=300)

    @field_validator("audience", "hypothesis", "sponsor_disclosure")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("tone_constraints", "prohibited_claims")
    @classmethod
    def validate_text_list(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("list entries must not be blank")
        if any(len(value) > 500 for value in normalized):
            raise ValueError("list entries must be at most 500 characters")
        return normalized

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        validated = _https_url(value, "canonical_url")
        assert validated is not None
        return validated

    @model_validator(mode="after")
    def validate_brief(self) -> "CampaignBrief":
        self.launch_starts_at = _aware_utc(self.launch_starts_at, "launch_starts_at")
        self.launch_ends_at = _aware_utc(self.launch_ends_at, "launch_ends_at")
        if self.launch_ends_at <= self.launch_starts_at:
            raise ValueError("launch_ends_at must be after launch_starts_at")
        fact_ids = [fact.id for fact in self.verified_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("verified fact IDs must be unique")
        return self

    @property
    def fact_ids(self) -> set[str]:
        return {fact.id for fact in self.verified_facts}


class ContentItem(BaseModel):
    """Exact public copy plus enough context to approve it safely."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,95}$")
    experiment_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    channel: str = Field(default="x", pattern=r"^[a-z0-9-]{1,32}$")
    content_type: ContentType
    exact_copy: str = Field(alias="copy", min_length=1, max_length=280)
    context_url: AnyHttpUrl | None = None
    media_url: AnyHttpUrl | None = None
    alt_text: str | None = Field(default=None, max_length=1_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=40)
    sponsor_related: bool = False
    status: ContentStatus = ContentStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("exact_copy")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            raise ValueError("copy contains control characters")
        normalized = value.strip()
        if not normalized:
            raise ValueError("copy must not be blank")
        return normalized

    @field_validator("context_url", "media_url")
    @classmethod
    def validate_public_url(cls, value: AnyHttpUrl | None, info) -> AnyHttpUrl | None:
        return _https_url(value, info.field_name)

    @field_validator("alt_text")
    @classmethod
    def normalize_alt_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence references must be unique")
        if any(not _FACT_ID.fullmatch(value) for value in values):
            raise ValueError("evidence references must use fact ID syntax")
        return values

    @model_validator(mode="after")
    def validate_reply_context(self) -> "ContentItem":
        self.created_at = _aware_utc(self.created_at, "created_at")
        self.updated_at = _aware_utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        if self.content_type is ContentType.REPLY and self.context_url is None:
            raise ValueError("a reply requires the exact parent context_url")
        if self.media_url is not None and not self.alt_text:
            raise ValueError("media requires alt_text")
        return self

    def approval_payload(self) -> dict[str, Any]:
        """Fields whose mutation invalidates a previous approval."""
        return {
            "id": self.id,
            "experiment_slug": self.experiment_slug,
            "channel": self.channel,
            "content_type": self.content_type.value,
            "copy": self.exact_copy,
            "context_url": str(self.context_url) if self.context_url else None,
            "media_url": str(self.media_url) if self.media_url else None,
            "alt_text": self.alt_text,
            "evidence_refs": sorted(self.evidence_refs),
            "sponsor_related": self.sponsor_related,
        }

    def content_hash(self) -> str:
        payload = json.dumps(self.approval_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalRecord(BaseModel):
    """Approval for exact item hashes, never for an entire campaign."""

    model_config = ConfigDict(extra="forbid")

    item_hashes: dict[str, str] = Field(min_length=1, max_length=20)
    approver: str = Field(min_length=1, max_length=120)
    approved_at: datetime
    expires_at: datetime

    @field_validator("approver")
    @classmethod
    def normalize_approver(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("approver must not be blank")
        return normalized

    @field_validator("item_hashes")
    @classmethod
    def validate_item_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _CONTENT_ID.fullmatch(item_id) for item_id in value):
            raise ValueError("approval contains an invalid content item ID")
        if any(not _SHA256.fullmatch(digest) for digest in value.values()):
            raise ValueError("approval hashes must be lowercase SHA-256 digests")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> "ApprovalRecord":
        self.approved_at = _aware_utc(self.approved_at, "approved_at")
        self.expires_at = _aware_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.approved_at:
            raise ValueError("approval must expire after it is issued")
        if self.expires_at - self.approved_at > timedelta(hours=24):
            raise ValueError("approval must expire within 24 hours")
        return self


_METRIC_TOKEN = re.compile(
    r"(?P<currency>(?:₹|\$|€|£)\s*\d[\d,.]*(?:\s*[kmb])?)"
    r"|(?P<percent>\b\d[\d,.]*\s*%)"
    r"|(?P<count>\b\d[\d,.]*(?:\s*[kmb])?)\s*"
    r"(?:visitors?|players?|plays?|completions?|views?|clicks?|shares?|replies|sponsors?|revenue|users?|signups?|downloads?)\b",
    re.IGNORECASE,
)


def _numeric_claim_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for match in _METRIC_TOKEN.finditer(value):
        token = next(group for group in match.groups() if group is not None)
        tokens.add(re.sub(r"[\s,]", "", token).casefold())
    return tokens


def validate_item_against_brief(item: ContentItem, brief: CampaignBrief) -> list[str]:
    """Return human-readable blockers; an empty list means the item is eligible for review."""
    blockers: list[str] = []
    if item.experiment_slug != brief.experiment_slug:
        blockers.append("item experiment_slug does not match the brief")

    unknown_refs = sorted(set(item.evidence_refs) - brief.fact_ids)
    if unknown_refs:
        blockers.append(f"unknown evidence references: {', '.join(unknown_refs)}")

    lowered = item.exact_copy.casefold()
    for claim in brief.prohibited_claims:
        if claim.strip() and claim.casefold() in lowered:
            blockers.append(f"copy contains prohibited claim: {claim}")

    numeric_tokens = _numeric_claim_tokens(item.exact_copy)
    if numeric_tokens and not item.evidence_refs:
        blockers.append("numeric audience, engagement, sponsor, or revenue claims require evidence references")
    elif numeric_tokens:
        cited_claims = " ".join(
            fact.claim for fact in brief.verified_facts if fact.id in item.evidence_refs
        )
        unsupported = sorted(numeric_tokens - _numeric_claim_tokens(cited_claims))
        if unsupported:
            blockers.append(
                "numeric claim tokens are not supported by cited facts: "
                + ", ".join(unsupported)
            )

    if item.sponsor_related and not re.search(r"\bsponsored\b|\bno affiliation\b", lowered):
        blockers.append("sponsor-related copy must disclose sponsorship or explicitly state no affiliation")

    return blockers
