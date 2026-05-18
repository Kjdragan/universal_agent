#!/usr/bin/env python3
"""YouTube Digest pipeline comparison harness.

Runs the daily-digest synthesis pipelines against the SAME input transcripts so
you can A/B-evaluate output quality across:

  - single_call: legacy one-LLM-call shape (today's production default)
  - map_reduce:  per-video retell + meta-synthesis (default for new path)

It also lets you sweep model + concurrency knobs on the map step without
touching the live cron, so you can answer questions like:
  - "Is glm-4.5-air retell quality on par with glm-5-turbo?"
  - "At what concurrency do FUP 429s start dominating wall time?"
  - "How does single_call output compare to map_reduce on the same playlist?"

Usage:
  # Compare both pipelines on the latest SUNDAY pocket:
  uv run python -m universal_agent.scripts.youtube_digest_compare \
      --day SUNDAY \
      --runs single_call:default \
      --runs map_reduce:glm-4.5-air@conc=3 \
      --runs map_reduce:glm-5-turbo@conc=3

  # Sweep concurrency on glm-4.5-air alone:
  uv run python -m universal_agent.scripts.youtube_digest_compare \
      --day SUNDAY \
      --runs map_reduce:glm-4.5-air@conc=2 \
      --runs map_reduce:glm-4.5-air@conc=3 \
      --runs map_reduce:glm-4.5-air@conc=5

Each --runs entry has the shape <pipeline>[:<model>][@conc=<int>]. Outputs:
  AGENT_RUN_WORKSPACES/daily_digests/comparison_<date>/<label>/{digest.md, run_metadata.json}
  AGENT_RUN_WORKSPACES/daily_digests/comparison_<date>/index.json  (sweep manifest)
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.scripts import youtube_daily_digest as ydd
from universal_agent.youtube_ingest import ingest_youtube_transcript

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("youtube_digest_compare")


@dataclass
class RunSpec:
    label: str
    pipeline: str  # "single_call" | "map_reduce"
    map_model: str | None  # only meaningful for map_reduce
    map_concurrency: int | None  # only meaningful for map_reduce


def _parse_run_spec(spec: str) -> RunSpec:
    """Parse one --runs argument into a RunSpec.

    Accepted shapes:
      single_call:default
      single_call
      map_reduce
      map_reduce:glm-4.5-air
      map_reduce:glm-4.5-air@conc=3
    """
    raw = spec.strip()
    if "@" in raw:
        head, conc_part = raw.split("@", 1)
    else:
        head, conc_part = raw, ""
    if ":" in head:
        pipeline, model_part = head.split(":", 1)
    else:
        pipeline, model_part = head, ""
    pipeline = pipeline.strip().lower()
    if pipeline not in {"single_call", "map_reduce"}:
        raise ValueError(f"Unknown pipeline in --runs entry: {spec!r} (expected single_call|map_reduce)")
    model = (model_part or "").strip() or None
    if model and model.lower() == "default":
        model = None
    conc = None
    if conc_part:
        if not conc_part.startswith("conc="):
            raise ValueError(f"Unknown qualifier in --runs entry: {spec!r}; expected @conc=<int>")
        try:
            conc = int(conc_part.removeprefix("conc=").strip())
        except ValueError as exc:
            raise ValueError(f"Invalid concurrency in {spec!r}: {exc}") from exc
    parts = [pipeline]
    if model:
        parts.append(model)
    if conc is not None:
        parts.append(f"conc{conc}")
    label = "_".join(parts)
    return RunSpec(label=label, pipeline=pipeline, map_model=model, map_concurrency=conc)


def _load_pocket(day_name: str, date_override: str | None) -> dict[str, Any]:
    """Load the most recent (or specified) repopulate pocket for the given day."""
    day = day_name.upper()
    pockets_dir = ydd._pockets_dir() / day
    if not pockets_dir.exists():
        raise FileNotFoundError(f"No pocket directory for {day} at {pockets_dir}")
    if date_override:
        path = ydd._pocket_path(day_name=day, date_str=date_override)
    else:
        candidates = sorted(pockets_dir.glob(f"*_{day}_playlist_pocket.json"))
        if not candidates:
            raise FileNotFoundError(f"No pocket files in {pockets_dir}")
        path = candidates[-1]
    pocket = json.loads(path.read_text(encoding="utf-8"))
    logger.info("Loaded pocket: %s (%d videos)", path, pocket.get("video_count", 0))
    return pocket


def _ingest_payloads(pocket: dict[str, Any]) -> list[ydd.VideoTranscriptPayload]:
    """Re-ingest transcripts for the pocket's videos so both pipelines see the
    SAME source data. This mirrors the production ingestion loop including
    proxy → no-proxy → metadata-only fallback."""
    payloads: list[ydd.VideoTranscriptPayload] = []
    for video in pocket.get("videos", []):
        video_id = str(video.get("video_id") or "").strip()
        title = str(video.get("title") or "").strip() or video_id
        if not video_id:
            continue
        result = None
        for attempt_proxy in [True, False]:
            try:
                result = ingest_youtube_transcript(
                    video_url=None,
                    video_id=video_id,
                    require_proxy=attempt_proxy,
                )
                if result.get("ok"):
                    break
                detail = str(result.get("detail", ""))
                if attempt_proxy and ("407" in detail or "NO_USER" in detail or "proxy" in detail.lower()):
                    continue
                break
            except Exception as exc:
                logger.warning("Ingest %s proxy=%s: %s", video_id, attempt_proxy, exc)
                if attempt_proxy:
                    continue
                result = {"ok": False, "error": str(exc)}
        if result and result.get("ok"):
            text = result.get("transcript_text", "") or ""
            if len(text) > 50_000:
                text = text[:50_000] + "... [TRUNCATED]"
            payloads.append(
                ydd.VideoTranscriptPayload(
                    video_id=video_id,
                    title=title,
                    transcript_text=text,
                    is_metadata_only=False,
                    original_item={"video_id": video_id, "title": title},
                )
            )
        else:
            payloads.append(
                ydd.VideoTranscriptPayload(
                    video_id=video_id,
                    title=title,
                    transcript_text="",
                    is_metadata_only=True,
                    original_item={"video_id": video_id, "title": title},
                )
            )
    logger.info(
        "Re-ingest complete: %d total, %d with transcript, %d metadata-only",
        len(payloads),
        sum(1 for p in payloads if not p.is_metadata_only),
        sum(1 for p in payloads if p.is_metadata_only),
    )
    return payloads


async def _run_one(spec: RunSpec, payloads: list[ydd.VideoTranscriptPayload], *, day_name: str, date_str: str) -> dict[str, Any]:
    """Execute a single labeled run. Returns metrics + artifact path."""
    started = time.perf_counter()
    env_snapshot: dict[str, str | None] = {}
    # Apply env overrides ONLY for this run; restore at the end.
    overrides: dict[str, str] = {}
    if spec.pipeline == "map_reduce":
        if spec.map_model:
            overrides["UA_YOUTUBE_DIGEST_MAP_MODEL"] = spec.map_model
        if spec.map_concurrency is not None:
            overrides["UA_YOUTUBE_DIGEST_MAP_CONCURRENCY"] = str(spec.map_concurrency)
    for k, v in overrides.items():
        env_snapshot[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        if spec.pipeline == "single_call":
            full_prompt = ydd.SYNTHESIS_PROMPT + "\n\n---\n\n".join(
                f"Title: {p.title}\nVideo ID: {p.video_id}\nTranscript:\n"
                + (p.transcript_text if p.transcript_text else f"[Metadata-only — transcript unavailable]\n\nTitle: {p.title}")
                + "\n"
                for p in payloads
            )
            digest_content = await ydd._generate_digest_content(
                full_prompt=full_prompt,
                pipeline_override="single_call",
            )
            map_metrics = None
        else:
            map_results = await ydd._map_retell_videos(payloads)
            reduce_output = await ydd._reduce_meta_synthesize(
                map_results,
                day_name=day_name,
                date_str=date_str,
            )
            digest_content = ydd._assemble_map_reduce_digest(
                reduce_output=reduce_output,
                map_results=map_results,
            )
            # Error-class breakdown lets us correlate model+concurrency choice
            # with the actual rate-limit error surface (e.g. does glm-4.5-air
            # generate more or fewer FUP 429s than glm-5-turbo at conc=3?).
            error_class_breakdown: dict[str, int] = {}
            for r in map_results:
                cls = r.last_error_class
                if cls:
                    error_class_breakdown[cls] = error_class_breakdown.get(cls, 0) + 1
            map_metrics = {
                "videos": len(map_results),
                "ok": sum(1 for r in map_results if r.error is None),
                "failed": sum(1 for r in map_results if r.error is not None),
                "latency_seconds": [r.elapsed_seconds for r in map_results],
                "map_model": map_results[0].map_model if map_results else None,
                "total_retries": sum(r.retries for r in map_results),
                "total_rate_limit_hits": sum(r.rate_limit_hits for r in map_results),
                "error_class_breakdown": error_class_breakdown,
                "per_video_retries": [
                    {
                        "video_id": r.video_id,
                        "retries": r.retries,
                        "rate_limit_hits": r.rate_limit_hits,
                        "last_error_class": r.last_error_class,
                        "ok": r.error is None,
                        "elapsed_seconds": r.elapsed_seconds,
                    }
                    for r in map_results
                ],
            }
        elapsed = time.perf_counter() - started
        return {
            "label": spec.label,
            "pipeline": spec.pipeline,
            "map_model": spec.map_model,
            "map_concurrency": spec.map_concurrency,
            "ok": True,
            "elapsed_seconds": elapsed,
            "digest_content": digest_content,
            "map_metrics": map_metrics,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        logger.error("Run %s failed after %.1fs: %s", spec.label, elapsed, exc)
        return {
            "label": spec.label,
            "pipeline": spec.pipeline,
            "map_model": spec.map_model,
            "map_concurrency": spec.map_concurrency,
            "ok": False,
            "elapsed_seconds": elapsed,
            "error": str(exc),
            "digest_content": None,
            "map_metrics": None,
        }
    finally:
        # Restore env so back-to-back runs don't bleed.
        for k, prev in env_snapshot.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _diff_classifications(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a video-id-keyed table of how each run classified each video."""
    table: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not run.get("ok") or not run.get("digest_content"):
            continue
        decisions = ydd._extract_decision_json(run["digest_content"])
        for row in decisions.get("ranked_videos", []):
            vid = str(row.get("video_id") or "")
            if not vid:
                continue
            entry = table.setdefault(vid, {"title": row.get("title", ""), "runs": {}})
            entry["runs"][run["label"]] = {
                "value_score": row.get("value_score"),
                "value_tier": row.get("value_tier"),
                "code_implementation_prospect": row.get("code_implementation_prospect"),
                "evidence_quality": row.get("evidence_quality"),
                "reason": row.get("reason"),
            }
    return table


async def main() -> int:
    parser = argparse.ArgumentParser(description="A/B comparison harness for YouTube Digest pipelines.")
    parser.add_argument("--day", required=True, help="Day of week (MONDAY..SUNDAY) — picks the matching pocket.")
    parser.add_argument("--pocket-date", default=None, help="Pocket date YYYY-MM-DD (optional; defaults to latest).")
    parser.add_argument(
        "--runs",
        action="append",
        required=True,
        help="Run specs of the form <pipeline>[:<model>][@conc=<int>]. Pass multiple times.",
    )
    parser.add_argument(
        "--output-label",
        default=None,
        help="Optional label appended to the comparison output directory name.",
    )
    args = parser.parse_args()

    initialize_runtime_secrets()
    specs = [_parse_run_spec(s) for s in args.runs]
    logger.info("Will execute %d runs: %s", len(specs), [s.label for s in specs])

    pocket = _load_pocket(args.day, args.pocket_date)
    payloads = _ingest_payloads(pocket)
    if not payloads:
        logger.error("No payloads; aborting.")
        return 2

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    label_suffix = f"_{args.output_label}" if args.output_label else ""
    base_dir = ydd._digest_artifacts_dir() / f"comparison_{date_str}_{args.day.upper()}{label_suffix}"
    base_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing comparison outputs to %s", base_dir)

    runs: list[dict[str, Any]] = []
    for spec in specs:
        logger.info(">>> Running %s ...", spec.label)
        run = await _run_one(spec, payloads, day_name=args.day, date_str=date_str)
        runs.append(run)
        run_dir = base_dir / spec.label
        run_dir.mkdir(parents=True, exist_ok=True)
        if run.get("digest_content"):
            (run_dir / "digest.md").write_text(run["digest_content"], encoding="utf-8")
        metadata = {k: v for k, v in run.items() if k != "digest_content"}
        metadata["started_at_utc"] = now.isoformat()
        (run_dir / "run_metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("  done: ok=%s elapsed=%.1fs", run["ok"], run["elapsed_seconds"])

    classifications = _diff_classifications(runs)
    (base_dir / "classifications_diff.json").write_text(
        json.dumps(classifications, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    index = {
        "comparison_dir": str(base_dir),
        "pocket_video_count": len(payloads),
        "day_name": args.day.upper(),
        "started_at_utc": now.isoformat(),
        "runs": [
            {
                "label": r["label"],
                "pipeline": r["pipeline"],
                "map_model": r["map_model"],
                "map_concurrency": r["map_concurrency"],
                "ok": r["ok"],
                "elapsed_seconds": r["elapsed_seconds"],
                "error": r.get("error"),
                "map_metrics": r.get("map_metrics"),
            }
            for r in runs
        ],
    }
    (base_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Comparison complete. Index written to %s/index.json", base_dir)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
