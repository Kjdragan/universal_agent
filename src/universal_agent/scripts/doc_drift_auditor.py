"""
Documentation Drift Auditor — Stage 1

A deterministic Python script (zero LLM) that performs nightly checks on
documentation health. Produces a structured drift report consumed by the
Stage 2 VP agent dispatcher.

Checks performed:
  1. Git change scanner (last 24h)
  2. Index natural health check (bidirectional sync with README/Status indexes)
  3. Internal link checker (broken markdown links)
  4. Glossary drift detector (candidate terms)
  5. Deployment doc co-change rule
  6. Agentic file drift (AGENTS.md, workflows, skills)
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(subprocess.run(
    ["git", "rev-parse", "--show-toplevel"],
    capture_output=True, text=True, check=True,
).stdout.strip())

DOCS_DIR = REPO_ROOT / "docs"
README_INDEX = DOCS_DIR / "README.md"
STATUS_INDEX = DOCS_DIR / "Documentation_Status.md"
GLOSSARY_FILE = DOCS_DIR / "Glossary.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"

ARTIFACTS_BASE = REPO_ROOT / "artifacts" / "doc-drift-reports"

# Directories/patterns to exclude from docs scanning
DOCS_EXCLUDE = {"KEVIN_OTHER_DOCUMENTATION", "__pycache__"}

# Code areas → likely doc areas mapping
CODE_TO_DOCS_MAP = {
    "src/universal_agent/tools/": ["03_Operations/", "04_API_Reference/"],
    "src/universal_agent/gateway_server": ["02_Flows/", "04_API_Reference/"],
    "src/universal_agent/cron_service": ["03_Operations/"],
    ".github/workflows/deploy": ["deployment/", "06_Deployment_And_Environments/"],
    ".agents/": ["AGENTS.md"],
    "src/universal_agent/heartbeat": ["02_Subsystems/Heartbeat_Service.md", "03_Operations/88_"],
    "src/universal_agent/memory": ["02_Subsystems/Memory_System.md"],
}

# Agentic config files that should be updated when agent behavior changes
AGENTIC_CONFIG_FILES = {
    "AGENTS.md",
    ".agents/workflows/",
    ".agents/skills/",
}

AGENTIC_CODE_DIRS = {
    "src/universal_agent/tools/",
    ".agents/",
    "src/universal_agent/scripts/",
}


# ---------------------------------------------------------------------------
# Issue model
# ---------------------------------------------------------------------------
class Issue:
    """A single drift issue."""

    def __init__(self, category: str, severity: str, file: str,
                 description: str, suggested_action: str):
        self.category = category
        self.severity = severity  # P0, P1, P2
        self.file = file
        self.description = description
        self.suggested_action = suggested_action

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "severity": self.severity,
            "file": self.file,
            "description": self.description,
            "suggested_action": self.suggested_action,
        }


# ---------------------------------------------------------------------------
# 1. Git Change Scanner
# ---------------------------------------------------------------------------
def scan_git_changes(since_hours: int = 24) -> dict[str, Any]:
    """Get files changed in the last N hours, categorized by area."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since_hours} hours ago",
             "--name-status", "--pretty=format:"],
            capture_output=True, text=True, check=True,
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError:
        logger.warning("git log failed — possibly no commits in timeframe")
        return {"files": [], "by_area": {}}

    files: list[dict[str, str]] = []
    by_area: dict[str, list[str]] = defaultdict(list)

    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", maxsplit=1)
        if len(parts) < 2:
            continue
        status, filepath = parts[0], parts[1]
        files.append({"status": status, "path": filepath})

        # Categorize
        for code_prefix in CODE_TO_DOCS_MAP:
            if filepath.startswith(code_prefix):
                by_area[code_prefix].append(filepath)

    return {"files": files, "by_area": dict(by_area)}


# ---------------------------------------------------------------------------
# 2. Index Natural Health Check
# ---------------------------------------------------------------------------
def _extract_md_links(content: str) -> set[str]:
    """Extract markdown link targets from content — [text](path)."""
    pattern = r'\[(?:[^\]]*)\]\(([^)]+)\)'
    links = set()
    for match in re.finditer(pattern, content):
        target = match.group(1)
        # Only care about relative paths (not http/https)
        if not target.startswith(("http://", "https://", "#", "file://")):
            # Strip anchor fragments
            target = target.split("#")[0]
            if target:
                links.add(target)
    return links


def _get_all_doc_files() -> set[Path]:
    """Recursively get all .md files under docs/, excluding certain dirs."""
    result = set()
    for md_file in DOCS_DIR.rglob("*.md"):
        # Check if any parent directory is in exclude list
        parts = md_file.relative_to(DOCS_DIR).parts
        if any(part in DOCS_EXCLUDE for part in parts):
            continue
        result.add(md_file)
    return result


def _resolve_index_links(index_file: Path) -> set[Path]:
    """Parse an index file and resolve all linked paths to absolute paths."""
    if not index_file.exists():
        return set()

    content = index_file.read_text(encoding="utf-8")
    raw_links = _extract_md_links(content)
    resolved = set()

    for link in raw_links:
        # Resolve relative to the index file's directory
        target = (index_file.parent / link).resolve()
        if target.suffix == ".md" or target.is_dir():
            resolved.add(target)

    return resolved


def check_index_health(issues: list[Issue]) -> None:
    """Bidirectional sync between docs on disk and index files."""
    all_doc_files = _get_all_doc_files()

    # Resolve linked paths from both indexes
    readme_links = _resolve_index_links(README_INDEX)
    status_links = _resolve_index_links(STATUS_INDEX)
    all_indexed = readme_links | status_links

    # --- Orphan docs: on disk but not in any index ---
    for doc_file in all_doc_files:
        # Skip the index files themselves
        if doc_file in (README_INDEX, STATUS_INDEX):
            continue

        # Check if this file (or its parent directory) is referenced
        is_indexed = False
        for indexed_path in all_indexed:
            if doc_file == indexed_path:
                is_indexed = True
                break
            # Directory-level reference (e.g., linking to "01_Architecture")
            if indexed_path.is_dir() and doc_file.is_relative_to(indexed_path):
                is_indexed = True
                break
            # Resolve to the same file
            try:
                if doc_file.resolve() == indexed_path.resolve():
                    is_indexed = True
                    break
            except (OSError, ValueError):
                pass

        if not is_indexed:
            rel = doc_file.relative_to(DOCS_DIR)
            issues.append(Issue(
                category="index_orphan",
                severity="P1",
                file=str(rel),
                description=f"Doc file `{rel}` exists on disk but is not linked in README.md or Documentation_Status.md",
                suggested_action=f"Add `{rel}` to both docs/README.md and docs/Documentation_Status.md, or delete if obsolete",
            ))

    # --- Dead index entries: linked in index but file doesn't exist ---
    for indexed_path in all_indexed:
        if indexed_path.suffix == ".md" and not indexed_path.exists():
            rel = indexed_path.relative_to(DOCS_DIR) if indexed_path.is_relative_to(DOCS_DIR) else indexed_path
            issues.append(Issue(
                category="index_dead_entry",
                severity="P0",
                file=str(rel),
                description=f"Index references `{rel}` but the file does not exist on disk",
                suggested_action=f"Remove the stale entry from docs/README.md and/or docs/Documentation_Status.md",
            ))


# ---------------------------------------------------------------------------
# 3. Internal Link Checker
# ---------------------------------------------------------------------------
def check_internal_links(issues: list[Issue]) -> None:
    """Verify all internal markdown links in docs/ resolve to existing files."""
    for md_file in _get_all_doc_files():
        content = md_file.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), start=1):
            for match in re.finditer(r'\[(?:[^\]]*)\]\(([^)]+)\)', line):
                target = match.group(1)
                if target.startswith(("http://", "https://", "#", "file://", "mailto:")):
                    continue
                # Strip anchor
                target_path = target.split("#")[0]
                if not target_path:
                    continue

                resolved = (md_file.parent / target_path).resolve()
                if not resolved.exists():
                    rel_source = md_file.relative_to(DOCS_DIR)
                    issues.append(Issue(
                        category="broken_link",
                        severity="P1",
                        file=str(rel_source),
                        description=f"Broken link on line {i}: `[...]('{target_path}')` — target does not exist",
                        suggested_action=f"Fix or remove the broken link in `{rel_source}` line {i}",
                    ))


# ---------------------------------------------------------------------------
# 4. Glossary Drift Detector
# ---------------------------------------------------------------------------
def _extract_glossary_terms() -> set[str]:
    """Extract defined terms from the glossary table."""
    if not GLOSSARY_FILE.exists():
        return set()

    content = GLOSSARY_FILE.read_text(encoding="utf-8")
    terms = set()
    for match in re.finditer(r'\*\*([^*]+)\*\*', content):
        terms.add(match.group(1).lower())
    return terms


# Common words / git noise to exclude from glossary candidate detection
GLOSSARY_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "get", "set", "add", "new",
    "delete", "from", "like", "into", "with", "this", "that", "have", "will",
    "each", "make", "more", "some", "them", "than", "its", "also", "back",
    "none", "true", "false", "null", "self", "type", "name", "file", "path",
    "line", "text", "data", "list", "dict", "test", "init", "main", "args",
    "http", "post", "head", "diff", "log", "run", "str", "int", "def",
    "class", "import", "return", "async", "await", "print", "error",
    # Git/diff noise
    "added", "removed", "modified", "changed", "commit", "merge", "branch",
    "index", "mode", "binary", "rename", "copy",
    # Common Python/tech but not project-specific
    "todo", "fixme", "hack", "note", "bug", "feat", "docs", "fix",
}


def check_glossary_drift(git_changes: dict[str, Any], issues: list[Issue]) -> None:
    """Flag new technical terms appearing frequently in changed files but not in glossary."""
    glossary_terms = _extract_glossary_terms()
    if not glossary_terms:
        return

    # Gather diff content for changed Python/MD files
    changed_paths = [f["path"] for f in git_changes.get("files", [])
                     if f["path"].endswith((".py", ".md"))]

    if not changed_paths:
        return

    # Get the actual diff content (HEAD~1..HEAD for recent changes)
    try:
        result = subprocess.run(
            ["git", "diff", "-U0", "HEAD~1", "HEAD", "--"] + changed_paths[:50],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        diff_text = result.stdout
    except subprocess.CalledProcessError:
        return

    if not diff_text:
        return

    # Find capitalized multi-word terms or all-caps acronyms (3+ chars)
    # that appear 3+ times
    candidates: Counter[str] = Counter()

    # Capitalized terms like "Brain Transplant" or "Durable Execution"
    for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', diff_text):
        candidates[match.group(1).lower()] += 1

    # Acronyms like "MCP", "URW", "CSI" (only from added lines to reduce noise)
    for match in re.finditer(r'\b([A-Z]{3,})\b', diff_text):
        term = match.group(1).lower()
        if len(term) <= 6:  # Very long all-caps are usually constants, not terms
            candidates[term] += 1

    for term, count in candidates.items():
        if (count >= 3
                and term not in glossary_terms
                and term not in GLOSSARY_STOPWORDS
                and len(term) >= 3):
            issues.append(Issue(
                category="glossary_candidate",
                severity="P2",
                file="docs/Glossary.md",
                description=f"Term '{term}' appears {count} times in recent changes but is not in the glossary",
                suggested_action=f"Consider adding '{term}' to docs/Glossary.md if it's project-specific terminology",
            ))


# ---------------------------------------------------------------------------
# 5. Deployment Doc Co-change Rule
# ---------------------------------------------------------------------------
def check_deployment_cochange(git_changes: dict[str, Any], issues: list[Issue]) -> None:
    """If deploy workflows changed, deployment docs must also change."""
    deploy_changed = any(
        f["path"].startswith(".github/workflows/deploy")
        for f in git_changes.get("files", [])
    )
    if not deploy_changed:
        return

    docs_deploy_changed = any(
        f["path"].startswith("docs/deployment/")
        for f in git_changes.get("files", [])
    )
    if not docs_deploy_changed:
        issues.append(Issue(
            category="deploy_cochange_violation",
            severity="P0",
            file=".github/workflows/deploy-*.yml",
            description="Deployment workflow files changed but docs/deployment/ was NOT updated (AGENTS.md rule violation)",
            suggested_action="Update docs/deployment/ to reflect the deployment workflow changes",
        ))


# ---------------------------------------------------------------------------
# 6. Agentic File Drift
# ---------------------------------------------------------------------------
def check_agentic_drift(git_changes: dict[str, Any], issues: list[Issue]) -> None:
    """If agent tools/scripts changed, check if agentic config files were co-updated."""
    agentic_code_changed = [
        f["path"] for f in git_changes.get("files", [])
        if any(f["path"].startswith(d) for d in AGENTIC_CODE_DIRS)
    ]

    if not agentic_code_changed:
        return

    agentic_config_changed = any(
        any(f["path"].startswith(cfg) or f["path"] == cfg
            for cfg in AGENTIC_CONFIG_FILES)
        for f in git_changes.get("files", [])
    )

    if not agentic_config_changed:
        file_list = ", ".join(agentic_code_changed[:5])
        if len(agentic_code_changed) > 5:
            file_list += f" (+{len(agentic_code_changed) - 5} more)"
        issues.append(Issue(
            category="agentic_drift",
            severity="P1",
            file="AGENTS.md / .agents/",
            description=f"Agent code changed ({file_list}) but no agentic config files (AGENTS.md, workflows, skills) were updated",
            suggested_action="Review whether AGENTS.md, workflow files, or SKILL.md files need updates to reflect the code changes",
        ))


# ---------------------------------------------------------------------------
# 7. Code-to-doc cross-reference
# ---------------------------------------------------------------------------
def check_code_doc_crossref(git_changes: dict[str, Any], issues: list[Issue]) -> None:
    """Flag code areas that changed without corresponding doc area updates."""
    changed_files = [f["path"] for f in git_changes.get("files", [])]

    for code_prefix, doc_areas in CODE_TO_DOCS_MAP.items():
        code_touched = [f for f in changed_files if f.startswith(code_prefix)]
        if not code_touched:
            continue

        # Check if any doc area was also touched
        doc_touched = any(
            any(f.startswith("docs/" + da) for da in doc_areas)
            for f in changed_files
        )

        if not doc_touched:
            issues.append(Issue(
                category="code_doc_drift",
                severity="P2",
                file=code_prefix,
                description=f"Code in `{code_prefix}` changed ({len(code_touched)} files) but related doc areas ({', '.join(doc_areas)}) were not updated",
                suggested_action=f"Review docs in {', '.join(doc_areas)} to verify they still accurately describe the code in {code_prefix}",
            ))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_json_report(issues: list[Issue], git_changes: dict[str, Any],
                         date_str: str) -> dict[str, Any]:
    """Build the structured JSON report."""
    return {
        "report_date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_issues": len(issues),
        "issues_by_severity": {
            "P0": len([i for i in issues if i.severity == "P0"]),
            "P1": len([i for i in issues if i.severity == "P1"]),
            "P2": len([i for i in issues if i.severity == "P2"]),
        },
        "git_summary": {
            "files_changed": len(git_changes.get("files", [])),
            "areas_affected": list(git_changes.get("by_area", {}).keys()),
        },
        "issues": [i.to_dict() for i in issues],
    }


def generate_markdown_report(issues: list[Issue], git_changes: dict[str, Any],
                              date_str: str) -> str:
    """Build the human-readable Markdown drift report."""
    lines = [
        f"# Documentation Drift Report — {date_str}",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Files changed (last 24h):** {len(git_changes.get('files', []))}",
        f"**Issues found:** {len(issues)}",
        "",
    ]

    if not issues:
        lines.extend([
            "## ✅ All Clear",
            "",
            "No documentation drift detected. All indexes are in sync, no broken links, and no co-change violations found.",
            "",
            "---",
            "*Generated by doc_drift_auditor.py — Stage 1 of the nightly documentation pipeline*",
        ])
        return "\n".join(lines)

    # Group by severity
    severity_order = ["P0", "P1", "P2"]
    severity_emoji = {"P0": "🔴", "P1": "🟡", "P2": "🔵"}

    for severity in severity_order:
        sev_issues = [i for i in issues if i.severity == severity]
        if not sev_issues:
            continue

        lines.extend([
            f"## {severity_emoji[severity]} {severity} Issues ({len(sev_issues)})",
            "",
        ])

        # Group within severity by category
        by_cat: dict[str, list[Issue]] = defaultdict(list)
        for issue in sev_issues:
            by_cat[issue.category].append(issue)

        for cat, cat_issues in by_cat.items():
            cat_label = cat.replace("_", " ").title()
            lines.append(f"### {cat_label}")
            lines.append("")
            for issue in cat_issues:
                lines.append(f"- **{issue.file}**: {issue.description}")
                lines.append(f"  - *Action:* {issue.suggested_action}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## Git Change Summary",
        "",
        f"Total files changed: {len(git_changes.get('files', []))}",
        "",
    ])

    by_area = git_changes.get("by_area", {})
    if by_area:
        lines.append("| Code Area | Files Changed |")
        lines.append("|-----------|---------------|")
        for area, area_files in by_area.items():
            lines.append(f"| `{area}` | {len(area_files)} |")
        lines.append("")

    lines.extend([
        "---",
        "*Generated by doc_drift_auditor.py — Stage 1 of the nightly documentation pipeline*",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_audit(dry_run: bool = False, since_hours: int = 24) -> int:
    """Execute all checks and produce the drift report. Returns exit code."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    issues: list[Issue] = []

    logger.info("=== Documentation Drift Auditor ===")
    logger.info(f"Scanning changes from the last {since_hours} hours...")

    # 1. Git changes
    git_changes = scan_git_changes(since_hours)
    logger.info(f"  Files changed: {len(git_changes['files'])}")

    # 2. Index health
    logger.info("  Checking index health...")
    check_index_health(issues)
    logger.info(f"    Index issues found: {len([i for i in issues if i.category.startswith('index_')])}")

    # 3. Internal links
    logger.info("  Checking internal links...")
    link_count_before = len(issues)
    check_internal_links(issues)
    logger.info(f"    Broken links found: {len(issues) - link_count_before}")

    # 4. Glossary drift
    logger.info("  Checking glossary drift...")
    glossary_before = len(issues)
    check_glossary_drift(git_changes, issues)
    logger.info(f"    Glossary candidates: {len(issues) - glossary_before}")

    # 5. Deployment co-change rule
    logger.info("  Checking deployment co-change rule...")
    deploy_before = len(issues)
    check_deployment_cochange(git_changes, issues)
    if len(issues) - deploy_before > 0:
        logger.warning("    ⚠️  Deployment co-change violation detected!")

    # 6. Agentic file drift
    logger.info("  Checking agentic file drift...")
    agentic_before = len(issues)
    check_agentic_drift(git_changes, issues)
    if len(issues) - agentic_before > 0:
        logger.info("    Agentic drift detected")

    # 7. Code-doc cross-reference
    logger.info("  Checking code-doc cross-references...")
    crossref_before = len(issues)
    check_code_doc_crossref(git_changes, issues)
    logger.info(f"    Cross-ref drift: {len(issues) - crossref_before}")

    # Generate reports
    json_report = generate_json_report(issues, git_changes, date_str)
    md_report = generate_markdown_report(issues, git_changes, date_str)

    logger.info(f"\n=== Results: {len(issues)} issues found ===")

    if dry_run:
        print("\n--- JSON Report ---")
        print(json.dumps(json_report, indent=2))
        print("\n--- Markdown Report ---")
        print(md_report)
    else:
        # Write to artifacts directory
        report_dir = ARTIFACTS_BASE / date_str
        report_dir.mkdir(parents=True, exist_ok=True)

        json_path = report_dir / "drift_report.json"
        md_path = report_dir / "DRIFT_REPORT.md"

        json_path.write_text(json.dumps(json_report, indent=2), encoding="utf-8")
        md_path.write_text(md_report, encoding="utf-8")

        logger.info(f"Reports written to {report_dir}/")

    return 1 if issues else 0


async def main():
    """Entry point for cron script execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Documentation Drift Auditor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print reports to stdout instead of writing to artifacts")
    parser.add_argument("--since-hours", type=int, default=24,
                        help="How many hours back to scan git history (default: 24)")
    args = parser.parse_args()

    exit_code = run_audit(dry_run=args.dry_run, since_hours=args.since_hours)
    sys.exit(exit_code)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
