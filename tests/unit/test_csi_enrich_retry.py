"""Unit tests for `_csi_retry` — the transient-retry helper used by the
RSS semantic enrichment script (and reusable for the other CSI crons).

The 2026-05-17 cold-start storm wrote 10 permanent `failed` rows in
30 seconds because the gateway hiccupped on a 12-event burst. With this
helper, request_exception now retries up to twice before being recorded,
and the canary's ok_rate doesn't crater on the first fire of every
4-hour timer cycle.

Auth errors (401) and captions_disabled remain permanent — retrying them
would burn a residential-proxy slot for nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "CSI_Ingester" / "development" / "scripts"


@pytest.fixture
def retry_mod():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "_csi_retry", SCRIPTS_DIR / "_csi_retry.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_is_transient_request_exception(retry_mod):
    assert retry_mod.is_transient_failure(
        {"ok": False, "error": "request_exception", "detail": "timeout"}
    )


def test_is_transient_5xx_http_error(retry_mod):
    assert retry_mod.is_transient_failure(
        {"ok": False, "error": "http_error", "http_status": 502}
    )


def test_is_transient_no_status_treated_as_transient(retry_mod):
    """http_status absent or 0 means the network died before any response.
    Retrying once or twice is the right move."""
    assert retry_mod.is_transient_failure({"ok": False, "error": "http_error"})


def test_401_is_permanent(retry_mod):
    """The 2026-03 outage class — empty token returns 401. We must NOT
    retry, because the proxy bandwidth is precious and the next call
    will also 401."""
    assert not retry_mod.is_transient_failure(
        {"ok": False, "error": "http_error", "http_status": 401}
    )


def test_404_is_permanent(retry_mod):
    assert not retry_mod.is_transient_failure(
        {"ok": False, "error": "http_error", "http_status": 404}
    )


def test_captions_disabled_is_permanent(retry_mod):
    assert not retry_mod.is_transient_failure(
        {
            "ok": False,
            "error": "youtube_transcript_api_failed",
            "failure_class": "captions_disabled",
        }
    )


def test_ok_short_circuits(retry_mod):
    assert not retry_mod.is_transient_failure({"ok": True})


def test_retry_returns_first_success(retry_mod):
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"ok": True, "transcript_text": "real"}

    out = retry_mod.retry_on_transient(
        fetch, max_retries=2, backoff_seconds=0.0, sleep=lambda _s: None
    )
    assert out["ok"]
    assert calls["n"] == 1


def test_retry_recovers_after_transient(retry_mod):
    """The cold-start case: first call times out, second succeeds.
    Result is the success — not the transient failure."""
    seq = [
        {"ok": False, "error": "request_exception", "detail": "timeout"},
        {"ok": True, "transcript_text": "got it"},
    ]
    calls = {"n": 0}

    def fetch():
        result = seq[calls["n"]]
        calls["n"] += 1
        return result

    sleeps: list[float] = []
    out = retry_mod.retry_on_transient(
        fetch,
        max_retries=2,
        backoff_seconds=3.0,
        sleep=sleeps.append,
    )
    assert out["ok"]
    assert calls["n"] == 2
    assert sleeps == [3.0]  # first failure → one sleep of base backoff


def test_retry_does_not_retry_permanent_failure(retry_mod):
    """401 must not consume retry slots."""
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"ok": False, "error": "http_error", "http_status": 401}

    out = retry_mod.retry_on_transient(
        fetch, max_retries=2, backoff_seconds=0.0, sleep=lambda _s: None
    )
    assert not out["ok"]
    assert calls["n"] == 1  # single attempt


def test_retry_exhausts_on_persistent_transient(retry_mod):
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"ok": False, "error": "request_exception", "detail": "timeout"}

    out = retry_mod.retry_on_transient(
        fetch, max_retries=2, backoff_seconds=0.0, sleep=lambda _s: None
    )
    assert not out["ok"]
    assert calls["n"] == 3  # 1 initial + 2 retries
    assert out["error"] == "request_exception"


def test_retry_backoff_grows_linearly(retry_mod):
    """Backoff multiplier is `attempt + 1` — first sleep = base, second = 2*base.
    Caps the cold-start storm if every retry also fails."""

    def fetch():
        return {"ok": False, "error": "request_exception", "detail": "timeout"}

    sleeps: list[float] = []
    retry_mod.retry_on_transient(
        fetch,
        max_retries=3,
        backoff_seconds=2.0,
        sleep=sleeps.append,
    )
    # 1 initial fail → sleep 2.0, 2nd fail → 4.0, 3rd fail → 6.0; 4th attempt
    # also fails but no further sleep.
    assert sleeps == [2.0, 4.0, 6.0]
