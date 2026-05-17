"""Unit test for the standalone Infisical-fallback helper used by every CSI
cron that needs a Bearer token. Pins the contract that the helper:

  - Returns the value of the first key present in the secrets dict.
  - Returns "" on import or fetch failure (so the caller can keep going
    with the env-file value rather than crashing the cron).
  - Treats whitespace / None / empty values as misses.

The actual production failure mode (empty token → 401 from gateway → every
row written as transcript_status='failed') was the *result* of this helper
not existing. This test pins the helper so a future cleanup can't silently
drop it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "CSI_Ingester" / "development" / "scripts"


@pytest.fixture
def resolver():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "_csi_secret_resolver", SCRIPTS_DIR / "_csi_secret_resolver.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_returns_first_present_key(resolver, monkeypatch):
    secrets = {
        "UA_INTERNAL_API_TOKEN": "real-token-aaaa",
        "UA_YOUTUBE_INGEST_TOKEN": "",  # empty — should be skipped
    }
    monkeypatch.setattr(
        resolver, "_import_fetch_infisical_secrets", lambda: (lambda: secrets)
    )
    out = resolver.resolve_token_from_infisical(
        ["UA_YOUTUBE_INGEST_TOKEN", "UA_INTERNAL_API_TOKEN"]
    )
    assert out == "real-token-aaaa"


def test_returns_empty_when_no_keys_match(resolver, monkeypatch):
    monkeypatch.setattr(
        resolver, "_import_fetch_infisical_secrets", lambda: (lambda: {})
    )
    out = resolver.resolve_token_from_infisical(["MISSING_KEY"])
    assert out == ""


def test_returns_empty_when_import_fails(resolver, monkeypatch):
    def _boom():
        raise ImportError("universal_agent not on path")

    monkeypatch.setattr(resolver, "_import_fetch_infisical_secrets", _boom)
    out = resolver.resolve_token_from_infisical(["UA_INTERNAL_API_TOKEN"])
    assert out == ""


def test_returns_empty_when_fetch_fails(resolver, monkeypatch):
    def _fetch():
        raise RuntimeError("infisical unreachable")

    monkeypatch.setattr(
        resolver, "_import_fetch_infisical_secrets", lambda: _fetch
    )
    out = resolver.resolve_token_from_infisical(["UA_INTERNAL_API_TOKEN"])
    assert out == ""


def test_strips_whitespace_only_values(resolver, monkeypatch):
    secrets = {"UA_INTERNAL_API_TOKEN": "   "}
    monkeypatch.setattr(
        resolver, "_import_fetch_infisical_secrets", lambda: (lambda: secrets)
    )
    out = resolver.resolve_token_from_infisical(["UA_INTERNAL_API_TOKEN"])
    assert out == ""


def test_preserves_key_preference_order(resolver, monkeypatch):
    secrets = {"A": "first", "B": "second"}
    monkeypatch.setattr(
        resolver, "_import_fetch_infisical_secrets", lambda: (lambda: secrets)
    )
    # When both present, the helper takes the first one listed in `keys`.
    assert resolver.resolve_token_from_infisical(["B", "A"]) == "second"
    assert resolver.resolve_token_from_infisical(["A", "B"]) == "first"
