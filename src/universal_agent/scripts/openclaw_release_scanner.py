"""
OpenClaw Release Scanner — Stage 1

A deterministic Python script (zero LLM) that monitors the OpenClaw GitHub
repository for new releases. Produces a structured release report consumed
by the Stage 2 OpenClaw Sync Agent.

Checks performed:
  1. Fetch recent releases from GitHub REST API
  2. Compare against last-checked state file
  3. For each new release, extract and categorize changes
  4. Produce structured JSON + human-readable Markdown reports

Exit codes:
  0 = No new releases found
  1 = New releases found (signals Stage 2 should run)
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(subprocess.run(
    ["git", "rev-parse", "--show-toplevel"],
    capture_output=True, text=True, check=True,
).stdout.strip())

GITHUB_REPO = "openclaw/openclaw"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"

ARTIFACTS_BASE = REPO_ROOT / "artifacts" / "openclaw-sync"
STATE_FILE = ARTIFACTS_BASE / "last_checked.json"

# OpenClaw component directories → feature area mapping
# Used to categorize file changes into meaningful areas
OPENCLAW_COMPONENT_MAP = {
    "src/gateway/": "Gateway",
    "src/agent/": "Agent Runtime",
    "src/channels/": "Messaging Channels",
    "src/skills/": "Skills System",
    "src/cron/": "Cron & Scheduling",
    "src/ui/": "Control UI",
    "src/plugins/": "Plugin SDK",
    "src/nodes/": "Native Clients",
    "src/security/": "Security",
    "src/tools/": "Tools System",
    "src/memory/": "Memory & Search",
    "src/config/": "Configuration",
    "clients/ios/": "iOS Client",
    "clients/macos/": "macOS Client",
    "clients/android/": "Android Client",
    "docs/": "Documentation",
    ".github/": "CI/CD",
}


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------
def _github_request(endpoint: str, token: str = "") -> dict | list | None:
    """Make a GitHub API request with optional auth token."""
    url = f"{GITHUB_API_BASE}/{endpoint}" if not endpoint.startswith("http") else endpoint
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning(f"GitHub API error {exc.code} for {url}: {exc.reason}")
        return None
    except Exception as exc:
        logger.warning(f"GitHub API request failed for {url}: {exc}")
        return None


def fetch_releases(token: str = "", max_releases: int = 10) -> list[dict]:
    """Fetch recent releases from the GitHub API."""
    data = _github_request(f"releases?per_page={max_releases}", token)
    if not data or not isinstance(data, list):
        logger.warning("No releases returned from GitHub API")
        return []
    return data


def fetch_compare(tag_old: str, tag_new: str, token: str = "") -> dict | None:
    """Fetch a comparison between two tags."""
    return _github_request(f"compare/{tag_old}...{tag_new}", token)


def fetch_changelog(ref: str = "main", token: str = "") -> str:
    """Fetch CHANGELOG.md content at a specific ref."""
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{ref}/CHANGELOG.md"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning(f"Failed to fetch CHANGELOG.md at {ref}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------
def load_state() -> dict:
    """Load the last-checked state from disk."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Failed to load state file: {exc}")
    return {"last_tag": None, "last_checked_at": None, "processed_tags": []}


def save_state(state: dict) -> None:
    """Save the last-checked state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Release analysis
# ---------------------------------------------------------------------------
def _categorize_changelog_entry(line: str) -> str:
    """Categorize a changelog entry by its prefix convention.

    OpenClaw uses prefixes like `gateway/`, `agent/`, `security/` etc.
    """
    line_lower = line.lower().strip("- *")
    for prefix_pattern, category in {
        "security": "Security",
        "gateway": "Gateway",
        "agent": "Agent Runtime",
        "channel": "Messaging Channels",
        "telegram": "Messaging Channels",
        "discord": "Messaging Channels",
        "skill": "Skills System",
        "cron": "Cron & Scheduling",
        "ui": "Control UI",
        "plugin": "Plugin SDK",
        "ios": "iOS Client",
        "macos": "macOS Client",
        "android": "Android Client",
        "node": "Native Clients",
        "tool": "Tools System",
        "memory": "Memory & Search",
        "config": "Configuration",
        "build": "Build & CI",
        "docker": "Build & CI",
        "exec": "Exec & Sandboxing",
        "browser": "Browser Integration",
        "acp": "Agent Communication Protocol",
    }.items():
        if line_lower.startswith(prefix_pattern) or f"/{prefix_pattern}" in line_lower:
            return category
    return "General"


def _parse_changelog_section(body: str) -> dict[str, list[str]]:
    """Parse a release body into categorized change entries."""
    categories: dict[str, list[str]] = defaultdict(list)

    current_section = "Changes"
    for line in body.splitlines():
        stripped = line.strip()

        # Detect section headers (e.g., "### Added", "### Fixed", "## Changes")
        header_match = re.match(r'^#{1,3}\s+(.+)', stripped)
        if header_match:
            current_section = header_match.group(1).strip()
            continue

        # Only process bullet items
        if not stripped.startswith(("- ", "* ", "• ")):
            continue

        entry_text = stripped.lstrip("-*• ").strip()
        if not entry_text:
            continue

        # Categorize by OpenClaw's component prefixes
        component = _categorize_changelog_entry(entry_text)

        # Clean up the prefix from the entry
        # OpenClaw uses "**component/feature**: description" format
        clean_entry = entry_text
        categories[component].append(f"[{current_section}] {clean_entry}")

    return dict(categories)


def _categorize_file_changes(files: list[dict]) -> dict[str, int]:
    """Categorize file changes by OpenClaw component area."""
    area_counts: dict[str, int] = defaultdict(int)
    for f in files:
        filename = f.get("filename", "")
        matched = False
        for prefix, area in OPENCLAW_COMPONENT_MAP.items():
            if filename.startswith(prefix):
                area_counts[area] += 1
                matched = True
                break
        if not matched:
            area_counts["Other"] += 1
    return dict(area_counts)


def analyze_release(
    release: dict,
    previous_tag: str | None,
    token: str = "",
) -> dict[str, Any]:
    """Analyze a single release and produce a structured report entry."""
    tag = release.get("tag_name", "unknown")
    name = release.get("name", tag)
    body = release.get("body", "")
    published_at = release.get("published_at", "")
    html_url = release.get("html_url", "")
    prerelease = release.get("prerelease", False)

    result: dict[str, Any] = {
        "tag": tag,
        "name": name,
        "published_at": published_at,
        "html_url": html_url,
        "prerelease": prerelease,
        "changelog_categories": {},
        "file_changes": {},
        "total_files_changed": 0,
        "additions": 0,
        "deletions": 0,
        "raw_body": body,
    }

    # Parse changelog body
    result["changelog_categories"] = _parse_changelog_section(body)

    # Fetch comparison with previous tag if available
    if previous_tag:
        compare_data = fetch_compare(previous_tag, tag, token)
        if compare_data:
            files = compare_data.get("files", [])
            result["total_files_changed"] = len(files)
            result["additions"] = sum(f.get("additions", 0) for f in files)
            result["deletions"] = sum(f.get("deletions", 0) for f in files)
            result["file_changes"] = _categorize_file_changes(files)

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_json_report(
    releases: list[dict[str, Any]],
    date_str: str,
) -> dict[str, Any]:
    """Build the structured JSON report."""
    return {
        "report_date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "openclaw_repo": f"https://github.com/{GITHUB_REPO}",
        "new_releases_count": len(releases),
        "releases": releases,
    }


def generate_markdown_report(
    releases: list[dict[str, Any]],
    date_str: str,
) -> str:
    """Build the human-readable Markdown release report."""
    lines = [
        f"# OpenClaw Release Report — {date_str}",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Repository:** [openclaw/openclaw](https://github.com/{GITHUB_REPO})",
        f"**New releases found:** {len(releases)}",
        "",
    ]

    if not releases:
        lines.extend([
            "## ✅ No New Releases",
            "",
            "No new OpenClaw releases detected since last check.",
            "",
            "---",
            "*Generated by openclaw_release_scanner.py — Stage 1 of the OpenClaw sync pipeline*",
        ])
        return "\n".join(lines)

    for i, rel in enumerate(releases):
        tag = rel["tag"]
        name = rel.get("name", tag)
        published = rel.get("published_at", "unknown")
        url = rel.get("html_url", "")
        prerelease = rel.get("prerelease", False)
        pre_badge = " 🧪 (pre-release)" if prerelease else ""

        lines.extend([
            f"## {'🆕' if i == 0 else '📦'} Release: {name}{pre_badge}",
            "",
            f"- **Tag:** `{tag}`",
            f"- **Published:** {published}",
            f"- **URL:** [{tag}]({url})" if url else "",
            "",
        ])

        # File change stats
        total_files = rel.get("total_files_changed", 0)
        additions = rel.get("additions", 0)
        deletions = rel.get("deletions", 0)
        if total_files > 0:
            lines.extend([
                "### 📊 Change Statistics",
                "",
                f"| Metric | Value |",
                f"|--------|-------|",
                f"| Files changed | {total_files} |",
                f"| Lines added | +{additions} |",
                f"| Lines removed | -{deletions} |",
                "",
            ])

            # File changes by area
            file_changes = rel.get("file_changes", {})
            if file_changes:
                lines.append("**Changes by component area:**")
                lines.append("")
                lines.append("| Component | Files |")
                lines.append("|-----------|-------|")
                for area, count in sorted(file_changes.items(), key=lambda x: -x[1]):
                    lines.append(f"| {area} | {count} |")
                lines.append("")

        # Categorized changelog entries
        categories = rel.get("changelog_categories", {})
        if categories:
            lines.extend([
                "### 📝 Changes by Category",
                "",
            ])
            for category, entries in sorted(categories.items()):
                lines.append(f"#### {category}")
                lines.append("")
                for entry in entries:
                    lines.append(f"- {entry}")
                lines.append("")
        elif rel.get("raw_body"):
            lines.extend([
                "### 📝 Release Notes",
                "",
                rel["raw_body"],
                "",
            ])

        lines.append("---")
        lines.append("")

    # Summary of all areas touched across all releases
    all_areas: dict[str, int] = defaultdict(int)
    all_categories: dict[str, int] = defaultdict(int)
    for rel in releases:
        for area, count in rel.get("file_changes", {}).items():
            all_areas[area] += count
        for cat, entries in rel.get("changelog_categories", {}).items():
            all_categories[cat] += len(entries)

    if all_areas:
        lines.extend([
            "## 📈 Impact Summary (All Releases Combined)",
            "",
            "### Component Areas Most Affected",
            "",
            "| Component | Total Files Changed |",
            "|-----------|-------------------|",
        ])
        for area, count in sorted(all_areas.items(), key=lambda x: -x[1]):
            lines.append(f"| {area} | {count} |")
        lines.append("")

    if all_categories:
        lines.extend([
            "### Feature Categories",
            "",
            "| Category | Change Entries |",
            "|----------|--------------|",
        ])
        for cat, count in sorted(all_categories.items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {count} |")
        lines.append("")

    lines.extend([
        "---",
        "*Generated by openclaw_release_scanner.py — Stage 1 of the OpenClaw sync pipeline*",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_scanner(
    dry_run: bool = False,
    force_rescan: bool = False,
    max_releases: int = 5,
    token: str = "",
) -> int:
    """Execute the release scan and produce reports. Returns exit code."""
    import os
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("=== OpenClaw Release Scanner ===")
    logger.info(f"Repository: {GITHUB_REPO}")

    # Fetch releases
    logger.info(f"Fetching up to {max_releases} recent releases...")
    all_releases = fetch_releases(token, max_releases=max_releases)
    if not all_releases:
        logger.warning("Could not fetch releases from GitHub API")
        return 0

    logger.info(f"  Found {len(all_releases)} releases on GitHub")

    # Load state to determine what's new
    state = load_state()
    last_tag = state.get("last_tag")
    processed_tags = set(state.get("processed_tags", []))

    if force_rescan:
        logger.info("  Force rescan enabled — processing all fetched releases")
        new_releases_raw = all_releases[:max_releases]
    else:
        # Filter to only releases we haven't processed
        new_releases_raw = [
            r for r in all_releases
            if r.get("tag_name") not in processed_tags
        ]
        logger.info(f"  New (unprocessed) releases: {len(new_releases_raw)}")

    if not new_releases_raw:
        logger.info("✅ No new OpenClaw releases since last check.")
        return 0

    # Sort chronologically (oldest first) for proper previous-tag chaining
    new_releases_raw.sort(key=lambda r: r.get("published_at", ""))

    # Analyze each new release
    analyzed: list[dict[str, Any]] = []
    previous_tag = last_tag  # start from last known tag

    for release in new_releases_raw:
        tag = release.get("tag_name", "unknown")
        logger.info(f"  Analyzing release {tag}...")
        analysis = analyze_release(release, previous_tag, token)
        analyzed.append(analysis)
        previous_tag = tag

    # Sort results newest-first for the report
    analyzed.reverse()

    # Generate reports
    json_report = generate_json_report(analyzed, date_str)
    md_report = generate_markdown_report(analyzed, date_str)

    logger.info(f"\n=== Results: {len(analyzed)} new releases analyzed ===")

    if dry_run:
        print("\n--- JSON Report ---")
        print(json.dumps(json_report, indent=2))
        print("\n--- Markdown Report ---")
        print(md_report)
    else:
        # Write to artifacts directory
        report_dir = ARTIFACTS_BASE / date_str
        report_dir.mkdir(parents=True, exist_ok=True)

        json_path = report_dir / "release_report.json"
        md_path = report_dir / "RELEASE_REPORT.md"

        json_path.write_text(json.dumps(json_report, indent=2), encoding="utf-8")
        md_path.write_text(md_report, encoding="utf-8")

        logger.info(f"Reports written to {report_dir}/")

        # Update state
        newest_tag = analyzed[0]["tag"]  # first after reverse = newest
        all_processed = processed_tags | {r["tag"] for r in analyzed}
        save_state({
            "last_tag": newest_tag,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "processed_tags": sorted(all_processed),
        })
        logger.info(f"State updated — last_tag: {newest_tag}")

    return 1  # new releases found → signal Stage 2


def main():
    """Entry point for CLI and GitHub Actions execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="OpenClaw Release Scanner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print reports to stdout instead of writing to artifacts")
    parser.add_argument("--force-rescan", action="store_true",
                        help="Re-scan all recent releases regardless of state file")
    parser.add_argument("--max-releases", type=int, default=5,
                        help="Maximum releases to fetch (default: 5)")
    args = parser.parse_args()

    exit_code = run_scanner(
        dry_run=args.dry_run,
        force_rescan=args.force_rescan,
        max_releases=args.max_releases,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
