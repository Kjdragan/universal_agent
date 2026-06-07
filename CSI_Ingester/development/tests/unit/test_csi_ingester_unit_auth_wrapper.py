"""Regression guard: csi-ingester.service must wrap ExecStart in csi_run.sh.

The in-loop batch_brief LLM path resolves its key from the process env
(``csi_ingester.llm_auth.resolve_csi_llm_auth`` -> ``os.getenv``). On the prod
VPS the shared LLM auth vars (ANTHROPIC_API_KEY / ZAI_API_KEY) are injected by
``infisical run`` via ``scripts/csi_run.sh``. Every CSI unit that calls the LLM
wraps its ExecStart in csi_run.sh; csi-ingester.service shipped without it, so
batch briefs silently degraded to "(LLM unavailable - plain summary)" even
though the gateway and timers were healthy. This test locks the wrapper in.
"""

from __future__ import annotations

from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parents[2] / "deployment" / "systemd"


def _exec_start(unit_name: str) -> str:
    text = (SYSTEMD_DIR / unit_name).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart=") :].strip()
    raise AssertionError(f"no ExecStart in {unit_name}")


def test_csi_ingester_wraps_execstart_in_csi_run() -> None:
    exec_start = _exec_start("csi-ingester.service")
    assert "scripts/csi_run.sh" in exec_start, (
        "csi-ingester.service must invoke scripts/csi_run.sh so infisical injects "
        f"the LLM auth vars into the batch_brief loop; got: {exec_start}"
    )
    # csi_run.sh must be the entrypoint (first token), not buried mid-command.
    assert exec_start.split()[0].endswith("/csi_run.sh"), (
        f"csi_run.sh must be the ExecStart entrypoint; got: {exec_start}"
    )


def test_csi_ingester_matches_trend_unit_wrapper() -> None:
    # The LLM-OK trend-report unit is the reference pattern for cred injection.
    ingester = _exec_start("csi-ingester.service").split()[0]
    trend = _exec_start("csi-rss-trend-report.service").split()[0]
    assert ingester == trend, (
        "csi-ingester.service should use the same csi_run.sh entrypoint as the "
        f"LLM-OK trend units; ingester={ingester} trend={trend}"
    )
