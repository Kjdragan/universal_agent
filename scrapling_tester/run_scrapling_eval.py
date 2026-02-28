#!/usr/bin/env python3
"""Run a reproducible Scrapling inbox evaluation against a directory of JSON files."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


TIER_RE = re.compile(r"^- \*\*Fetcher tier\*\*: (.+)$", re.MULTILINE)
STATUS_RE = re.compile(r"^- \*\*HTTP Status\*\*: (.+)$", re.MULTILINE)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a JSON corpus into an inbox folder, run the Scrapling inbox processor, "
            "and emit a run_report.json with summary metrics."
        )
    )
    parser.add_argument(
        "--source-json-dir",
        required=True,
        help="Directory containing input JSON files to evaluate (recursively).",
    )
    parser.add_argument(
        "--work-root",
        default=str(Path(__file__).resolve().parent / "runs"),
        help="Root directory where run artifacts will be written.",
    )
    parser.add_argument(
        "--run-name",
        default="",
        help="Optional run folder name. Defaults to run_<UTC timestamp>.",
    )
    parser.add_argument("--escalation-delay", type=float, default=1.5)
    parser.add_argument("--per-url-delay", type=float, default=0.25)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _configure_logging(log_file: Path, level: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def _build_run_dir(work_root: Path, run_name: str) -> Path:
    if run_name.strip():
        return work_root / run_name.strip()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    return work_root / f"run_{timestamp}"


def _collect_output_metrics(processed_dir: Path) -> dict[str, object]:
    md_files = sorted(processed_dir.glob("*.md"))
    tier_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    error_stub_files = 0

    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8", errors="replace")
        if text.startswith("# Scrape Error"):
            error_stub_files += 1

        tier_match = TIER_RE.search(text)
        if tier_match:
            tier_counts[tier_match.group(1).strip()] += 1

        status_match = STATUS_RE.search(text)
        if status_match:
            status_counts[status_match.group(1).strip()] += 1

    return {
        "markdown_files": len(md_files),
        "error_stub_files": error_stub_files,
        "fetcher_tier_counts": dict(sorted(tier_counts.items())),
        "http_status_counts": dict(sorted(status_counts.items())),
    }


def main() -> int:
    args = _parse_args()

    source_dir = Path(args.source_json_dir).expanduser().resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"Source directory not found: {source_dir}")

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    try:
        from universal_agent.tools.scrapling_scraper import InboxProcessor
    except Exception as exc:
        raise SystemExit(
            "Could not import scrapling_scraper. "
            "Run with dependencies, e.g. `uv run --with \"scrapling[fetchers]\" ...`. "
            f"Import error: {exc}"
        )

    run_dir = _build_run_dir(Path(args.work_root).expanduser().resolve(), args.run_name)
    inbox_dir = run_dir / "inbox"
    input_copy_dir = inbox_dir / "source_batch"
    processed_dir = run_dir / "processed"
    logs_dir = run_dir / "logs"
    report_path = run_dir / "run_report.json"

    if run_dir.exists():
        shutil.rmtree(run_dir)

    _configure_logging(logs_dir / "run.log", args.log_level)
    logger = logging.getLogger("scrapling_eval")

    logger.info("Preparing run directory: %s", run_dir)
    input_copy_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, input_copy_dir)

    source_json_count = len(list(input_copy_dir.rglob("*.json")))
    logger.info("Copied %d JSON files into inbox seed directory", source_json_count)

    processor = InboxProcessor(
        inbox_dir=inbox_dir,
        output_dir=processed_dir,
        escalation_delay=args.escalation_delay,
        per_url_delay=args.per_url_delay,
        overwrite=args.overwrite,
    )

    start = time.perf_counter()
    summary = processor.run_once()
    duration = round(time.perf_counter() - start, 3)

    output_metrics = _collect_output_metrics(processed_dir)
    done_jobs = len(list((inbox_dir / "done").glob("*.json")))
    failed_jobs = len(list((inbox_dir / "failed").glob("*.json")))

    report = {
        "run_started_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration,
        "source_json_dir": str(source_dir),
        "run_dir": str(run_dir),
        "inbox_dir": str(inbox_dir),
        "processed_dir": str(processed_dir),
        "source_json_count": source_json_count,
        "summary": summary,
        "done_jobs": done_jobs,
        "failed_jobs": failed_jobs,
        "output_metrics": output_metrics,
        "notes": {
            "recursive_inbox_scan": True,
            "auto_url_extraction_from_search_payloads": True,
        },
    }

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Run report written: %s", report_path)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
