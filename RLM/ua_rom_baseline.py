from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .staging import materialize_markdown_corpus
from .types import CorpusBundle, DistillRequest, LaneResult


def _is_debug_enabled() -> bool:
    return os.environ.get("RLM_DEBUG", "0") in {"1", "true", "TRUE", "yes", "on"}


def _copy_prompts(target_prompts_dir: Path) -> None:
    source_prompts_dir = Path(__file__).resolve().parents[1] / "RLM_Exploration" / "prompts"
    if not source_prompts_dir.exists():
        raise FileNotFoundError(f"Missing prompts source: {source_prompts_dir}")
    target_prompts_dir.mkdir(parents=True, exist_ok=True)
    for source in source_prompts_dir.glob("*.md"):
        shutil.copy2(source, target_prompts_dir / source.name)


def _write_config(config_path: Path, request: DistillRequest, corpus_dir: Path, lane_output_dir: Path) -> None:
    payload = {
        "topic": request.topic,
        "report_title": request.report_title,
        "corpus_dir": str(corpus_dir),
        "output_dir": str(lane_output_dir),
        "model": request.model,
        "planner_max_tokens": 1200,
        "explorer_max_tokens": 1200,
        "section_max_tokens": 1600,
        "summary_max_tokens": 700,
        "explorer_max_steps": 8,
        "max_search_results": 6,
        "max_read_chars": 8000,
        "snippet_window": 400,
        "index_preview_items": 25,
    }
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                out.append(parsed)
        except json.JSONDecodeError:
            continue
    return out


def run_ua_rom_baseline(request: DistillRequest, bundle: CorpusBundle, run_dir: Path) -> LaneResult:
    lane_dir = run_dir / "ua_rom_baseline"
    lane_dir.mkdir(parents=True, exist_ok=True)

    corpus_dir = materialize_markdown_corpus(bundle, lane_dir / "staged_corpus")
    prompts_dir = lane_dir / "prompts"
    _copy_prompts(prompts_dir)

    lane_output_dir = lane_dir / "outputs"
    lane_output_dir.mkdir(parents=True, exist_ok=True)

    config_path = lane_dir / "config.json"
    _write_config(config_path, request, corpus_dir, lane_output_dir)

    runner_script = Path(__file__).resolve().parents[1] / "RLM_Exploration" / "rom_runner.py"
    if not runner_script.exists():
        raise FileNotFoundError(f"Missing baseline runner: {runner_script}")

    cmd = [sys.executable, str(runner_script), "--config", str(config_path)]
    child_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if _is_debug_enabled():
        print(f"[RLM DEBUG] ua_rom_baseline launching: {' '.join(cmd)}", flush=True)
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        merged_lines: list[str] = []
        assert proc.stdout is not None
        started_at = time.time()
        last_heartbeat = started_at
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    merged_lines.append(line)
                    print(f"[RLM BASELINE] {line.rstrip()}", flush=True)
                    last_heartbeat = time.time()
            else:
                if proc.poll() is not None:
                    break
                now = time.time()
                if now - last_heartbeat >= 30:
                    print(
                        f"[RLM BASELINE] still running... elapsed_s={now - started_at:.0f}",
                        flush=True,
                    )
                    last_heartbeat = now
        returncode = proc.wait()
        stdout_text = "".join(merged_lines)
        stderr_text = "(RLM_DEBUG=1) stderr merged into stdout.log"
    else:
        process = subprocess.run(
            cmd,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        returncode = process.returncode
        stdout_text = process.stdout or ""
        stderr_text = process.stderr or ""

    (lane_dir / "stdout.log").write_text(stdout_text, encoding="utf-8")
    (lane_dir / "stderr.log").write_text(stderr_text, encoding="utf-8")

    if returncode != 0:
        raise RuntimeError(
            "ua_rom_baseline failed "
            f"(exit={returncode}). See {lane_dir / 'stderr.log'}"
        )

    report_md = lane_output_dir / "report.md"
    evidence_jsonl = lane_output_dir / "evidence.jsonl"

    evidence = _read_jsonl(evidence_jsonl)

    raw_artifacts = {
        "config": str(config_path),
        "stdout_log": str(lane_dir / "stdout.log"),
        "stderr_log": str(lane_dir / "stderr.log"),
        "outline_json": str(lane_output_dir / "outline.json"),
        "evidence_jsonl": str(evidence_jsonl),
        "report_md": str(report_md),
        "report_html": str(lane_output_dir / "report.html"),
        "sources_md": str(lane_output_dir / "sources.md"),
    }

    return LaneResult(
        mode="ua_rom_baseline",
        lane_dir=lane_dir,
        raw_artifacts=raw_artifacts,
        evidence_items=evidence,
    )
