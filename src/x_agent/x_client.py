"""Thin wrapper over ``tweepy.Client`` for posting single tweets and threads.

Supports a dry-run mode that never touches the network. Dry-run is enabled
explicitly by the caller, or implicitly when X API credentials are missing.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .config import get_settings

log = logging.getLogger(__name__)


class XClientError(RuntimeError):
    """Raised when posting to X fails."""


class XClient:
    """Posts tweets via X API v2 (or simulates in dry-run mode)."""

    def __init__(self, *, dry_run: bool | None = None) -> None:
        import os
        self.settings = get_settings()
        forced = os.environ.get("X_AGENT_FORCE_DRY_RUN") == "1"
        if dry_run is None:
            dry_run = forced or not self.settings.has_x_credentials
        elif forced:
            dry_run = True
        self.dry_run = dry_run
        self._client: Any | None = None
        if not self.dry_run:
            self._client = self._build_client()

    def _build_client(self) -> Any:
        try:
            import tweepy
        except ImportError as exc:
            raise XClientError(
                "tweepy is not installed; run `pip install -e .`"
            ) from exc

        s = self.settings
        if not s.has_x_credentials:
            raise XClientError(
                "X API credentials are missing; set X_API_KEY/X_API_SECRET/"
                "X_ACCESS_TOKEN/X_ACCESS_TOKEN_SECRET in .env or pass dry_run=True"
            )
        return tweepy.Client(
            consumer_key=s.x_api_key.get_secret_value(),
            consumer_secret=s.x_api_secret.get_secret_value(),
            access_token=s.x_access_token.get_secret_value(),
            access_token_secret=s.x_access_token_secret.get_secret_value(),
            wait_on_rate_limit=True,
        )

    @staticmethod
    def tweet_url(tweet_id: str) -> str:
        return f"https://x.com/i/web/status/{tweet_id}"

    def post_thread(self, posts: list[str]) -> list[str]:
        """Post one or more tweets, chaining replies for thread mode.

        Returns the list of resulting tweet IDs in order. Raises
        ``XClientError`` if any individual call fails.
        """
        if not posts:
            raise XClientError("post_thread requires at least one post")

        if self.dry_run:
            ids = [f"dryrun-{uuid.uuid4().hex[:12]}" for _ in posts]
            for i, (tid, body) in enumerate(zip(ids, posts), start=1):
                log.info("[dry-run] would post %d/%d (%d chars) id=%s", i, len(posts), len(body), tid)
            return ids

        ids: list[str] = []
        prev_id: str | None = None
        for i, body in enumerate(posts, start=1):
            kwargs: dict[str, Any] = {"text": body}
            if prev_id is not None:
                kwargs["in_reply_to_tweet_id"] = prev_id
            try:
                response = self._client.create_tweet(**kwargs)
            except Exception as exc:  # tweepy raises various subclasses
                raise XClientError(
                    f"failed to post tweet {i}/{len(posts)}: {exc}"
                ) from exc

            data = getattr(response, "data", None) or {}
            tweet_id = str(data.get("id")) if isinstance(data, dict) else str(getattr(data, "id", ""))
            if not tweet_id:
                raise XClientError(f"tweet {i}/{len(posts)} returned no id")
            ids.append(tweet_id)
            prev_id = tweet_id
            # Mild backoff between thread posts to be polite to the API.
            if i < len(posts):
                time.sleep(0.5)
        return ids
