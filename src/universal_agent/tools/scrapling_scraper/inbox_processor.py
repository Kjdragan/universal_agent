"""
Inbox-style JSON → Markdown scraping processor.

Directory layout (all relative to *inbox_dir*)::

    inbox/
    ├── <job>.json          ← user drops URL-list files here
    ├── processing/         ← file moved here while being worked on
    ├── done/               ← file moved here after all URLs scraped
    └── failed/             ← file moved here if the job fatally errors

    processed/              ← one .md per URL, at *output_dir*

JSON file formats accepted
--------------------------
List of URL strings::

    ["https://example.com", "https://foo.bar/page"]

Object with ``urls`` key + optional ``options``::

    {
      "urls": ["https://example.com"],
      "options": {
        "min_level": "basic",        // "basic" | "dynamic" | "stealthy"
        "force_level": null,
        "solve_cloudflare": true,
        "headless": true,
        "network_idle": true,
        "timeout": 30,
        "wait_selector": null,
        "proxy": null
      },
      "tags": "optional extra metadata"
    }

Any top-level keys besides ``urls`` and ``options`` are preserved in the
Markdown metadata block.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .content_converter import page_to_markdown
from .fetcher_strategy import FetcherLevel, FetcherStrategy, ScrapeRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job data model
# ---------------------------------------------------------------------------

@dataclass
class JobOptions:
    min_level: FetcherLevel = FetcherLevel.BASIC
    force_level: Optional[FetcherLevel] = None
    solve_cloudflare: bool = True
    headless: bool = True
    network_idle: bool = True
    timeout: float = 30.0
    wait_selector: Optional[str] = None
    proxy: Optional[str] = None
    extra_headers: Optional[dict[str, str]] = None


@dataclass
class ScrapingJob:
    source_file: Path
    urls: list[str]
    options: JobOptions = field(default_factory=JobOptions)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEVEL_MAP: dict[str, FetcherLevel] = {
    "basic": FetcherLevel.BASIC,
    "dynamic": FetcherLevel.DYNAMIC,
    "stealthy": FetcherLevel.STEALTHY,
}


def _parse_level(value: Any, default: Optional[FetcherLevel]) -> Optional[FetcherLevel]:
    if value is None:
        return default
    if isinstance(value, int):
        try:
            return FetcherLevel(value)
        except ValueError:
            return default
    return _LEVEL_MAP.get(str(value).lower(), default)


def _url_to_filename(url: str) -> str:
    """Convert a URL into a safe, descriptive filename stem."""
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").replace("/", "_") or "index"
    # Remove unsafe chars
    safe = re.sub(r"[^\w\-.]", "_", f"{domain}_{path}")
    return safe[:120]  # cap length


def _load_job(path: Path) -> ScrapingJob:
    """Parse a JSON file into a ScrapingJob."""
    raw = json.loads(path.read_text(encoding="utf-8"))

    urls: list[str] = []
    raw_options: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    if isinstance(raw, list):
        urls = [u.strip() for u in raw if isinstance(u, str) and u.strip()]
    elif isinstance(raw, dict):
        raw_urls = raw.get("urls", [])
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        if not isinstance(raw_urls, list):
            raise TypeError(f"'urls' must be a list of strings in {path}")
        urls = [u.strip() for u in raw_urls if isinstance(u, str) and u.strip()]
        raw_options = raw.get("options", {}) or {}
        metadata = {k: v for k, v in raw.items() if k not in ("urls", "options")}
    else:
        raise ValueError(f"Unsupported JSON format in {path}")

    opts = JobOptions(
        min_level=_parse_level(raw_options.get("min_level"), FetcherLevel.BASIC),
        force_level=_parse_level(raw_options.get("force_level"), None),  # type: ignore[arg-type]
        solve_cloudflare=bool(raw_options.get("solve_cloudflare", True)),
        headless=bool(raw_options.get("headless", True)),
        network_idle=bool(raw_options.get("network_idle", True)),
        timeout=float(raw_options.get("timeout", 30.0)),
        wait_selector=raw_options.get("wait_selector") or None,
        proxy=raw_options.get("proxy") or None,
        extra_headers=raw_options.get("extra_headers") or None,
    )

    return ScrapingJob(
        source_file=path,
        urls=urls,
        options=opts,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Main processor
# ---------------------------------------------------------------------------

class InboxProcessor:
    """
    Watches *inbox_dir* for JSON files and scrapes the contained URLs.

    Parameters
    ----------
    inbox_dir:
        Directory where JSON job files are dropped by the user.
    output_dir:
        Directory where scraped Markdown files are written.
    escalation_delay:
        Seconds between fetcher-tier escalation attempts.
    per_url_delay:
        Seconds to wait between individual URL requests (politeness).
    overwrite:
        If False (default), skip URLs whose output file already exists.
    """

    def __init__(
        self,
        inbox_dir: str | Path,
        output_dir: str | Path,
        escalation_delay: float = 2.0,
        per_url_delay: float = 1.0,
        overwrite: bool = False,
    ) -> None:
        self.inbox_dir = Path(inbox_dir)
        self.output_dir = Path(output_dir)
        self.escalation_delay = escalation_delay
        self.per_url_delay = per_url_delay
        self.overwrite = overwrite

        self._strategy = FetcherStrategy(escalation_delay=escalation_delay)

        # Sub-directories inside inbox_dir
        self._processing_dir = self.inbox_dir / "processing"
        self._done_dir = self.inbox_dir / "done"
        self._failed_dir = self.inbox_dir / "failed"

        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (
            self.inbox_dir,
            self._processing_dir,
            self._done_dir,
            self._failed_dir,
            self.output_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> dict[str, Any]:
        """
        Process all JSON files currently in *inbox_dir* (non-recursive first
        level) plus any nested sub-directories.

        Returns a summary dict.
        """
        json_files = sorted(self.inbox_dir.rglob("*.json"))
        # Exclude files already inside our managed sub-directories
        managed = {self._processing_dir, self._done_dir, self._failed_dir}
        json_files = [
            f for f in json_files
            if not any(f.is_relative_to(m) for m in managed)
        ]

        logger.info("Found %d JSON job file(s) in inbox", len(json_files))
        summary: dict[str, Any] = {
            "jobs_found": len(json_files),
            "jobs_succeeded": 0,
            "jobs_failed": 0,
            "urls_scraped": 0,
            "urls_skipped": 0,
            "urls_failed": 0,
        }

        for job_path in json_files:
            result = self._process_job_file(job_path)
            summary["jobs_succeeded"] += result.get("job_ok", 0)
            summary["jobs_failed"] += result.get("job_failed", 0)
            summary["urls_scraped"] += result.get("urls_scraped", 0)
            summary["urls_skipped"] += result.get("urls_skipped", 0)
            summary["urls_failed"] += result.get("urls_failed", 0)

        logger.info("Inbox run complete: %s", summary)
        return summary

    def run_loop(self, poll_interval: float = 10.0) -> None:
        """
        Continuously poll *inbox_dir* for new JSON files.

        Runs until interrupted (KeyboardInterrupt / SIGINT).
        """
        logger.info(
            "Starting inbox polling loop (interval=%.1fs) on %s",
            poll_interval, self.inbox_dir,
        )
        try:
            while True:
                self.run_once()
                logger.debug("Sleeping %.1fs before next poll", poll_interval)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Inbox processor stopped by user")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_job_file(self, job_path: Path) -> dict[str, Any]:
        """Move file to processing/, scrape all URLs, move to done/ or failed/."""
        logger.info("Processing job file: %s", job_path)

        # --- Move to processing/ ---
        processing_path = self._processing_dir / job_path.name
        # Handle name collisions in processing dir
        if processing_path.exists():
            stem = job_path.stem
            suffix = job_path.suffix
            processing_path = self._processing_dir / f"{stem}_{int(time.time())}{suffix}"

        shutil.move(str(job_path), str(processing_path))
        logger.debug("Moved %s → %s", job_path.name, processing_path)

        result: dict[str, Any] = {
            "job_ok": 0,
            "job_failed": 0,
            "urls_scraped": 0,
            "urls_skipped": 0,
            "urls_failed": 0,
        }

        try:
            job = _load_job(processing_path)
        except Exception as exc:
            logger.error("Failed to parse job file %s: %s", processing_path, exc)
            self._move_to(processing_path, self._failed_dir)
            result["job_failed"] = 1
            return result

        if not job.urls:
            logger.warning("Job file %s contains no URLs — skipping", processing_path)
            self._move_to(processing_path, self._done_dir)
            result["job_ok"] = 1
            return result

        # --- Scrape each URL ---
        fatal_error = False
        for url in job.urls:
            url_result = self._scrape_url(url, job)
            if url_result == "scraped":
                result["urls_scraped"] += 1
            elif url_result == "skipped":
                result["urls_skipped"] += 1
            else:
                result["urls_failed"] += 1

            if self.per_url_delay > 0:
                time.sleep(self.per_url_delay)

        # --- Move to done/ ---
        dest_dir = self._failed_dir if fatal_error else self._done_dir
        self._move_to(processing_path, dest_dir)
        result["job_ok"] = 1 if not fatal_error else 0
        result["job_failed"] = 1 if fatal_error else 0
        return result

    def _scrape_url(self, url: str, job: ScrapingJob) -> str:
        """
        Scrape a single URL and write its Markdown output.

        Returns:
            "scraped" | "skipped" | "failed"
        """
        stem = _url_to_filename(url)
        out_path = self.output_dir / f"{stem}.md"

        if out_path.exists() and not self.overwrite:
            logger.debug("Skipping %s (output exists: %s)", url, out_path)
            return "skipped"

        opts = job.options
        req = ScrapeRequest(
            url=url,
            min_level=opts.min_level,
            force_level=opts.force_level,
            solve_cloudflare=opts.solve_cloudflare,
            headless=opts.headless,
            network_idle=opts.network_idle,
            timeout=opts.timeout,
            wait_selector=opts.wait_selector,
            proxy=opts.proxy,
            extra_headers=opts.extra_headers,
        )

        try:
            page, level_used = self._strategy.fetch(req)
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            # Write an error stub so we know this URL was attempted
            _write_error_markdown(out_path, url, str(exc))
            return "failed"

        try:
            md = page_to_markdown(
                page=page,
                url=url,
                fetcher_level=level_used.name,
                job_metadata=job.metadata,
            )
        except Exception as exc:
            logger.error("Failed to convert %s to Markdown: %s", url, exc)
            _write_error_markdown(out_path, url, f"Markdown conversion error: {exc}")
            return "failed"

        try:
            out_path.write_text(md, encoding="utf-8")
            logger.info("Saved %s → %s", url, out_path)
        except Exception as exc:
            logger.error("Failed to write output for %s: %s", url, exc)
            return "failed"

        return "scraped"

    @staticmethod
    def _move_to(src: Path, dest_dir: Path) -> None:
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}_{int(time.time())}{src.suffix}"
        shutil.move(str(src), str(dest))
        logger.debug("Moved %s → %s", src.name, dest)


def _write_error_markdown(path: Path, url: str, error: str) -> None:
    """Write a minimal Markdown stub for a failed URL."""
    from datetime import datetime, timezone
    content = (
        f"# Scrape Error\n\n"
        f"## Metadata\n\n"
        f"- **URL**: {url}\n"
        f"- **Error**: {error}\n"
        f"- **Scraped at**: {datetime.now(timezone.utc).isoformat()}\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error("Failed to write error stub to %s: %s", path, e)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def run_inbox_processor(
    inbox_dir: str | Path,
    output_dir: str | Path,
    loop: bool = False,
    poll_interval: float = 10.0,
    escalation_delay: float = 2.0,
    per_url_delay: float = 1.0,
    overwrite: bool = False,
    log_level: str = "INFO",
) -> None:
    """
    Convenience function to start the inbox processor.

    Args:
        inbox_dir: Directory to watch for JSON job files.
        output_dir: Directory to write Markdown results.
        loop: If True, poll continuously; otherwise process existing files once.
        poll_interval: Seconds between polls (only used when loop=True).
        escalation_delay: Pause between fetcher-tier escalation retries.
        per_url_delay: Politeness delay between individual URL requests.
        overwrite: Overwrite existing output files.
        log_level: Logging level (e.g. "DEBUG", "INFO", "WARNING").
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    processor = InboxProcessor(
        inbox_dir=inbox_dir,
        output_dir=output_dir,
        escalation_delay=escalation_delay,
        per_url_delay=per_url_delay,
        overwrite=overwrite,
    )

    if loop:
        processor.run_loop(poll_interval=poll_interval)
    else:
        processor.run_once()
