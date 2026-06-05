"""Behavioral contract for the CSI Threads -> Infisical sync-back script.

`CSI_Ingester/development/scripts/csi_threads_infisical_sync.py` was rewritten
(Follow-up FU1 of PR #737) to route the write-back through the project primitive
`universal_agent.infisical_loader.upsert_infisical_secret` instead of the raw
`infisical_client` SDK (which is not installed in the CSI venv). These tests pin
the exit-code contract and that every payload key is upserted exactly once.

We monkeypatch the loader symbol by OBJECT attribute (not a string path) so the
script's lazy `from universal_agent.infisical_loader import upsert_infisical_secret`
picks up the fake at call time.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT
    / "CSI_Ingester"
    / "development"
    / "scripts"
    / "csi_threads_infisical_sync.py"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "csi_threads_infisical_sync_under_test", SCRIPT_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_payload(tmp_path: Path, payload: dict[str, str]) -> Path:
    f = tmp_path / "updates.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return f


def test_dry_run_never_calls_upsert(monkeypatch, tmp_path):
    import universal_agent.infisical_loader as loader

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loader, "upsert_infisical_secret", lambda k, v: calls.append((k, v)) or True
    )
    mod = _load_script_module()
    f = _write_payload(tmp_path, {"THREADS_ACCESS_TOKEN": "t", "THREADS_TOKEN_EXPIRES_AT": "x"})
    monkeypatch.setattr(sys, "argv", ["prog", "--updates-file", str(f), "--dry-run"])

    assert mod.main() == 0
    assert calls == []  # dry-run must not mutate the vault


def test_upserts_each_key_once_and_succeeds(monkeypatch, tmp_path):
    import universal_agent.infisical_loader as loader

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loader, "upsert_infisical_secret", lambda k, v: calls.append((k, v)) or True
    )
    mod = _load_script_module()
    payload = {
        "THREADS_APP_ID": "a",
        "THREADS_APP_SECRET": "s",
        "THREADS_USER_ID": "u",
        "THREADS_ACCESS_TOKEN": "t",
        "THREADS_TOKEN_EXPIRES_AT": "x",
    }
    f = _write_payload(tmp_path, payload)
    monkeypatch.setattr(sys, "argv", ["prog", "--updates-file", str(f)])

    assert mod.main() == 0
    assert [k for k, _ in calls] == list(payload)  # every key, once, in order
    assert dict(calls) == payload


def test_any_upsert_failure_returns_1(monkeypatch, tmp_path):
    import universal_agent.infisical_loader as loader

    def fake(key: str, value: str) -> bool:
        # Simulate a transient REST failure on the last key.
        return key != "THREADS_TOKEN_EXPIRES_AT"

    monkeypatch.setattr(loader, "upsert_infisical_secret", fake)
    mod = _load_script_module()
    f = _write_payload(tmp_path, {"THREADS_ACCESS_TOKEN": "t", "THREADS_TOKEN_EXPIRES_AT": "x"})
    monkeypatch.setattr(sys, "argv", ["prog", "--updates-file", str(f)])

    assert mod.main() == 1


def test_invalid_payload_returns_2(monkeypatch, tmp_path):
    mod = _load_script_module()
    f = _write_payload(tmp_path, {})  # empty object -> invalid after normalization
    monkeypatch.setattr(sys, "argv", ["prog", "--updates-file", str(f)])

    assert mod.main() == 2


def test_no_raw_infisical_client_import_in_source():
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    # The docstring intentionally *names* the SDK; what must not exist is an
    # actual import of it. Check the import forms specifically.
    assert "from infisical_client import" not in src, (
        "csi_threads_infisical_sync.py must not import the raw infisical_client SDK; "
        "use universal_agent.infisical_loader.upsert_infisical_secret instead."
    )
    assert "import infisical_client" not in src
