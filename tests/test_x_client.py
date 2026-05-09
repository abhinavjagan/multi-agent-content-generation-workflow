"""Tests for the X client wrapper (dry-run behavior; no network calls)."""

from __future__ import annotations

import pytest

from x_agent.x_client import XClient, XClientError


def test_dry_run_returns_synthetic_ids() -> None:
    client = XClient(dry_run=True)
    ids = client.post_thread(["hello", "world"])
    assert len(ids) == 2
    assert all(i.startswith("dryrun-") for i in ids)


def test_dry_run_empty_posts_raises() -> None:
    client = XClient(dry_run=True)
    with pytest.raises(XClientError):
        client.post_thread([])


def test_tweet_url_format() -> None:
    assert XClient.tweet_url("12345").endswith("/status/12345")


def test_force_dry_run_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_AGENT_FORCE_DRY_RUN", "1")
    # Even if user explicitly says dry_run=False, env override forces dry-run.
    client = XClient(dry_run=False)
    assert client.dry_run is True
