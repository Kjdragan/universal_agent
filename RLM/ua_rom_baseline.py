from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .staging import materialize_markdown_corpus
from .types import CorpusBundle, DistillRequest, LaneResult


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
    process = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )

    (lane_dir / "stdout.log").write_text(process.stdout or "", encoding="utf-8")
    (lane_dir / "stderr.log").write_text(process.stderr or "", encoding="utf-8")

    if process.returncode != 0:
        raise RuntimeError(
            "ua_rom_baseline failed "
            f"(exit={process.returncode}). See {lane_dir / 'stderr.log'}"
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
