"""Tests for csi_seed_dedicated_auth_env.py (dedicated CSI LLM lane seeding).

Covers the enhancements that let it provision the cost-safe ZAI lane through
`infisical run`: reading shared keys from the process env, restricting to a
single lane, and never printing secret values.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "csi_seed_dedicated_auth_env.py"
)


def _run(env_file: Path, *args: str, env_vars: dict[str, str] | None = None):
    # Clean env (PATH only + provided vars) so the host's real ANTHROPIC_*/ZAI_*
    # keys don't leak into the test.
    env = {"PATH": os.environ.get("PATH", "")}
    if env_vars:
        env.update(env_vars)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_lane_zai_seeds_only_zai_from_process_env(tmp_path: Path) -> None:
    f = tmp_path / "csi.env"
    f.write_text("CSI_DB_PATH=/x\n", encoding="utf-8")
    r = _run(
        f,
        "--lane",
        "zai",
        "--set-mode-1",
        env_vars={
            "ZAI_API_KEY": "zk-secret",
            "ZAI_BASE_URL": "https://zai.example/v1",
            "ANTHROPIC_API_KEY": "ant-secret",
        },
    )
    assert r.returncode == 0, r.stderr
    body = f.read_text(encoding="utf-8")
    assert "CSI_ZAI_API_KEY=zk-secret" in body
    assert "CSI_ZAI_BASE_URL=https://zai.example/v1" in body
    assert "CSI_LLM_AUTH_MODE=1" in body
    # lane=zai must NOT seed the Anthropic lane (cost safety).
    assert "CSI_ANTHROPIC_API_KEY" not in body
    # Secret VALUES must never be printed; only key names.
    assert "zk-secret" not in r.stdout
    assert "ant-secret" not in r.stdout
    assert "CSI_ZAI_API_KEY" in r.stdout


def test_file_value_takes_precedence_over_process_env(tmp_path: Path) -> None:
    f = tmp_path / "csi.env"
    f.write_text("ZAI_API_KEY=from-file\n", encoding="utf-8")
    r = _run(f, "--lane", "zai", env_vars={"ZAI_API_KEY": "from-env"})
    assert r.returncode == 0, r.stderr
    assert "CSI_ZAI_API_KEY=from-file" in f.read_text(encoding="utf-8")


def test_lane_both_seeds_anthropic_and_zai(tmp_path: Path) -> None:
    f = tmp_path / "csi.env"
    f.write_text("\n", encoding="utf-8")
    r = _run(f, env_vars={"ANTHROPIC_API_KEY": "a", "ZAI_API_KEY": "z"})
    assert r.returncode == 0, r.stderr
    body = f.read_text(encoding="utf-8")
    assert "CSI_ANTHROPIC_API_KEY=a" in body
    assert "CSI_ZAI_API_KEY=z" in body


def test_missing_shared_source_is_skipped_not_fatal(tmp_path: Path) -> None:
    f = tmp_path / "csi.env"
    f.write_text("CSI_DB_PATH=/x\n", encoding="utf-8")
    r = _run(f, "--lane", "zai")  # no ZAI_* in env
    assert r.returncode == 0, r.stderr
    assert "CSI_ZAI_API_KEY" not in f.read_text(encoding="utf-8")
    assert "Skipped" in r.stdout
