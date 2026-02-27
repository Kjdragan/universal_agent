"""
CLI entry point for the Scrapling inbox processor.

Usage::

    python -m src.universal_agent.tools.scrapling_scraper \\
        --inbox ./inbox \\
        --output ./processed \\
        [--loop] [--poll-interval 10] [--escalation-delay 2] \\
        [--per-url-delay 1] [--overwrite] [--log-level DEBUG]

JSON job files placed in *inbox/* are picked up, scraped, and the resulting
Markdown files are written to *output/*.  Each JSON file is moved to
``inbox/processing/`` while being worked on, then to ``inbox/done/`` on
success or ``inbox/failed/`` on a parse/fatal error.
"""

import argparse
import sys
from pathlib import Path

from .inbox_processor import run_inbox_processor


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scrapling-inbox",
        description="Scrapling inbox scraper — processes JSON URL lists into Markdown.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--inbox",
        default="./inbox",
        metavar="DIR",
        help="Directory to watch for JSON job files",
    )
    p.add_argument(
        "--output",
        default="./processed",
        metavar="DIR",
        help="Directory where scraped Markdown files are written",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="Continuously poll the inbox directory instead of processing once",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        metavar="SECS",
        help="Seconds between inbox polls (only used with --loop)",
    )
    p.add_argument(
        "--escalation-delay",
        type=float,
        default=2.0,
        metavar="SECS",
        help="Pause between fetcher-tier escalation attempts",
    )
    p.add_argument(
        "--per-url-delay",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Politeness delay between individual URL requests",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing Markdown output files",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    run_inbox_processor(
        inbox_dir=Path(args.inbox),
        output_dir=Path(args.output),
        loop=args.loop,
        poll_interval=args.poll_interval,
        escalation_delay=args.escalation_delay,
        per_url_delay=args.per_url_delay,
        overwrite=args.overwrite,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
