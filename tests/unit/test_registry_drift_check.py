"""Tests for scripts/registry_drift_check.py — the registry-vs-code drift gate.

The headline test is ``test_real_registry_is_clean``: it runs the drift check
against the actual committed registry on every PR (via pr-validate's
``pytest tests/unit``), so a code change that deletes a symbol the registry calls
``canonical`` — or revives something the registry calls ``removed`` — fails CI even
when the PR never touches project_docs/. The remaining tests prove the gate has
teeth by planting each drift class and asserting it is caught (and that the
legitimate stub/backfill escape hatch is honored).
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import registry_drift_check as rdc  # noqa: E402

REGISTRY = REPO_ROOT / rdc.REGISTRY_REL

# A symbol that genuinely resolves as live code (used to simulate "alive").
LIVE_REF = "task_hub.py::reconcile_task_lifecycle"
# A symbol that cannot resolve (used to simulate "gone").
DEAD_REF = "task_hub.py::__definitely_not_a_real_symbol_zzz__"

_TABLE_HEADER = "| Name | Status | What | Entry |\n|---|---|---|---|\n"


def _write_registry(tmp_path: Path, *rows: str) -> Path:
    """Build a minimal registry-shaped markdown file with the given table rows."""
    p = tmp_path / "registry.md"
    p.write_text(_TABLE_HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return p


def test_real_registry_is_clean():
    """The committed registry must have zero drift — this is the every-PR guard."""
    assert REGISTRY.exists(), f"registry missing at {REGISTRY}"
    drift = rdc.check_registry(REGISTRY)
    assert drift == [], "registry drift detected:\n" + "\n".join(drift)


def test_removed_but_alive_is_flagged(tmp_path):
    """A row marked `removed` that cites a still-resolving symbol (no stub marker)
    is the inverse drift doc_audit can't catch — it must fail."""
    reg = _write_registry(tmp_path, f"| `Ghost` | removed | turned off | `{LIVE_REF}` |")
    drift = rdc.check_registry(reg)
    assert any("STILL resolves" in d for d in drift), drift


def test_removed_with_stub_marker_is_allowed(tmp_path):
    """The doc's own escape hatch: a `removed` row whose text says backfill/stub/
    dead-module/no-op may legitimately cite a still-present symbol."""
    reg = _write_registry(
        tmp_path,
        f"| `Ghost` | removed | survives only as a backfill-only entry | `{LIVE_REF}` |",
    )
    drift = rdc.check_registry(reg)
    assert not any("STILL resolves" in d for d in drift), drift


def test_canonical_but_gone_is_flagged(tmp_path):
    """A row marked `canonical` whose cited symbol no longer resolves must fail."""
    reg = _write_registry(tmp_path, f"| `Phantom` | canonical | the right way | `{DEAD_REF}` |")
    drift = rdc.check_registry(reg)
    assert any("does NOT resolve" in d for d in drift), drift


def test_unclear_rows_are_not_asserted(tmp_path):
    """`unclear` is informational — it should never produce drift either way."""
    reg = _write_registry(tmp_path, f"| `Maybe` | unclear | doc-only claim | `{DEAD_REF}` |")
    drift = rdc.check_registry(reg)
    assert not any("Phantom" in d or "does NOT resolve" in d for d in drift), drift


def test_status_legend_table_is_harmless(tmp_path):
    """The 'How to read status' legend lists the status words in column 0 with no
    code citations — it must not be mistaken for claims."""
    reg = _write_registry(
        tmp_path,
        "| **canonical** | The current, correct way. | Build on it. |",
        "| **removed** | Gone from code or a stub/backfill reference. | Don't resurrect. |",
    )
    drift = rdc.check_registry(reg)
    assert drift == [], drift


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
