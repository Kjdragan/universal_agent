"""Quarantine SPA-404 hallucinations that the research-grounding pass wrote to the CSI vault.

Background: prior to the 2026-05-17 fix in ``research_grounding`` the
deterministic URL synthesizer slugified ``@`` mentions from tweet text into
fake docs.anthropic.com paths (e.g. ``/en/docs/oniricsunset``). docs.anthropic.com
is a Next.js SPA that returns HTTP 200 with the same shell HTML for any path,
so the fetcher persisted ~96 KiB of CSS/JS link markup as a legitimate
"Grounded source" and the vault ingester linked it from the canonical index.

This script walks the CSI knowledge vault and the per-packet research_grounding
directories, detects pages that look like SPA shells, and moves them under
``<vault>/sources/quarantine/`` (and ``<packet>/research_grounding/quarantine/``)
with a sibling ``*.reason.txt`` describing why they were flagged. It never
deletes content — every move is reversible.

Run with ``--dry-run`` first to inspect what would move.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import shutil
import sys
from typing import Iterator

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.services.research_grounding import _is_spa_404_shell

logger = logging.getLogger(__name__)


VAULT_SLUG = "claude-code-intelligence"
QUARANTINE_DIRNAME = "quarantine"


def _iter_vault_sources(vault_root: Path) -> Iterator[Path]:
    sources_dir = vault_root / "sources"
    if not sources_dir.exists():
        return iter(())
    return (
        path
        for path in sources_dir.iterdir()
        if path.is_file()
        and path.suffix == ".md"
        and "grounded-source" in path.name
    )


def _iter_packet_grounding_files(packets_root: Path) -> Iterator[Path]:
    if not packets_root.exists():
        return iter(())
    return packets_root.rglob("research_grounding/*/documentation_*.md")


def _looks_like_spa_shell(path: Path) -> bool:
    try:
        chars = path.stat().st_size
    except OSError:
        return False
    return _is_spa_404_shell(content_path=str(path), content_chars=chars)


def _quarantine(path: Path, *, dry_run: bool, reason: str) -> bool:
    """Move ``path`` under a sibling ``quarantine/`` dir. Returns True on action."""
    target_dir = path.parent / QUARANTINE_DIRNAME
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        logger.info("already quarantined: %s", target)
        return False
    if dry_run:
        logger.info("[dry-run] would quarantine %s -> %s (%s)", path, target, reason)
        return True
    shutil.move(str(path), str(target))
    (target.with_suffix(target.suffix + ".reason.txt")).write_text(
        f"Quarantined by csi_vault_cleanup_grounding_hallucinations.\nReason: {reason}\n",
        encoding="utf-8",
    )
    logger.info("quarantined %s -> %s", path, target)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=None,
        help="Override artifacts dir (defaults to artifacts.resolve_artifacts_dir()).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would move without touching disk.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    artifacts_root: Path = (args.artifacts_root or resolve_artifacts_dir()).expanduser().resolve()
    vault_root = artifacts_root / "knowledge-vaults" / VAULT_SLUG
    packets_root = artifacts_root / "proactive" / "claude_code_intel" / "packets"

    moved_vault = 0
    moved_grounding = 0

    for path in _iter_vault_sources(vault_root):
        if _looks_like_spa_shell(path):
            if _quarantine(path, dry_run=args.dry_run, reason="spa_404_shell"):
                moved_vault += 1

    for path in _iter_packet_grounding_files(packets_root):
        if _looks_like_spa_shell(path):
            if _quarantine(path, dry_run=args.dry_run, reason="spa_404_shell"):
                moved_grounding += 1

    logger.info(
        "Cleanup summary: %d vault page(s), %d grounding file(s) %s.",
        moved_vault,
        moved_grounding,
        "would move" if args.dry_run else "moved",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
