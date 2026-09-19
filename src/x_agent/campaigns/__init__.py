"""Local, approval-gated campaign records.

This package deliberately contains no publishing client. It prepares and
records exact copy for a human-controlled handoff to the destination service.
"""

from .models import (
    ApprovalRecord,
    CampaignBrief,
    ContentItem,
    ContentStatus,
    ContentType,
    FactRecord,
)
from .queue import ApprovalQueue

__all__ = [
    "ApprovalQueue",
    "ApprovalRecord",
    "CampaignBrief",
    "ContentItem",
    "ContentStatus",
    "ContentType",
    "FactRecord",
]
