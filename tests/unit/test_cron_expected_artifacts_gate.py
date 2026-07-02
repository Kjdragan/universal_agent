"""Day-specific artifact gate: cron_service._verify_expected_artifacts must
reject a STALE artifact (left by a prior run) when a spec sets
``newer_than_run_start`` — the 2026-07-02 paper_to_podcast run ended before
writing report.html yet passed 'success' because a Jun-30 report.html was still
on disk. The gate uses no ``self`` state, so we invoke it with a dummy self.
"""

import time
from types import SimpleNamespace

from universal_agent.cron_service import CronService


def _job(ws, fresh_required):
    return SimpleNamespace(
        job_id="p2p_test",
        workspace_dir=str(ws),
        metadata={
            "expected_artifacts": [
                {
                    "path": "work_products/paper_to_podcast/report.html",
                    "min_bytes": 2000,
                    "label": "Synthesis report",
                    "newer_than_run_start": fresh_required,
                }
            ]
        },
    )


def _record(started_at):
    return SimpleNamespace(
        status="success", started_at=started_at, error=None, output_preview=None
    )


def _write_report(ws):
    d = ws / "work_products" / "paper_to_podcast"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "report.html"
    f.write_text("<html>" + "x" * 3000 + "</html>", encoding="utf-8")
    return f


def test_stale_report_is_rejected(tmp_path):
    """File written BEFORE the run started (stale) -> downgraded to error."""
    _write_report(tmp_path)
    record = _record(started_at=time.time() + 100)  # run "starts" after the file
    flipped = CronService._verify_expected_artifacts(SimpleNamespace(), _job(tmp_path, True), record)
    assert flipped is True
    assert record.status == "error"
    assert "stale" in (record.error or "").lower()


def test_fresh_report_passes(tmp_path):
    """File written AFTER the run started (this run's output) -> success stands."""
    record = _record(started_at=time.time() - 100)  # run started before the file
    _write_report(tmp_path)
    flipped = CronService._verify_expected_artifacts(SimpleNamespace(), _job(tmp_path, True), record)
    assert flipped is False
    assert record.status == "success"


def test_without_flag_stale_still_passes(tmp_path):
    """Back-compat: specs without newer_than_run_start keep existence-only semantics."""
    _write_report(tmp_path)
    record = _record(started_at=time.time() + 100)
    flipped = CronService._verify_expected_artifacts(SimpleNamespace(), _job(tmp_path, False), record)
    assert flipped is False
    assert record.status == "success"


def test_missing_report_is_rejected(tmp_path):
    """No file at all -> downgraded regardless of the freshness flag."""
    (tmp_path / "work_products" / "paper_to_podcast").mkdir(parents=True)
    record = _record(started_at=time.time() - 100)
    flipped = CronService._verify_expected_artifacts(SimpleNamespace(), _job(tmp_path, True), record)
    assert flipped is True
    assert record.status == "error"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
