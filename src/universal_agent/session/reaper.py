"""Session reaper for cleaning up stale workspaces.

This module provides functionality to archive old workspaces to reduce
RAM pressure and maintain a cleaner workspace state.

Usage:
    # Dry-run (preview what would be archived)
    uv run python -m src.universal_agent.session.reaper --dry-run

    # Execute cleanup
    uv run python -m src.universal_agent.session.reaper --execute
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import logging
from pathlib import Path
import shutil
from typing import List, Optional

logger = logging.getLogger(__name__)

# Default paths relative to project root
DEFAULT_WORKSPACES_DIR = Path("AGENT_RUN_WORKSPACES")
DEFAULT_ARCHIVE_DIR = Path("AGENT_RUN_WORKSPACES_ARCHIVE")

# Prefixes to skip (these have their own lifecycle)
SKIP_PREFIXES = ("cron_",)


async def cleanup_stale_workspaces(
    max_age_hours: int = 24,
    workspaces_dir: Optional[Path] = None,
    archive_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> List[dict]:
    """Archive workspaces older than max_age_hours.

    This function moves stale workspaces from the active workspaces directory
    to an archive directory. It does NOT delete workspaces.

    Args:
        max_age_hours: Minimum age in hours for a workspace to be considered stale.
                       Defaults to 24 hours.
        workspaces_dir: Path to the active workspaces directory.
                        Defaults to AGENT_RUN_WORKSPACES.
        archive_dir: Path to the archive directory.
                     Defaults to AGENT_RUN_WORKSPACES_ARCHIVE.
        dry_run: If True, only log what would be done without actually moving files.

    Returns:
        List of dicts with information about archived workspaces.
        Each dict contains: 'name', 'age_hours', 'source', 'destination'

    Raises:
        FileNotFoundError: If workspaces_dir does not exist.
    """
    workspaces_path = (workspaces_dir or DEFAULT_WORKSPACES_DIR).resolve()
    archive_path = (archive_dir or DEFAULT_ARCHIVE_DIR).resolve()

    if not workspaces_path.exists():
        logger.warning(f"Workspaces directory does not exist: {workspaces_path}")
        return []

    # Ensure archive directory exists (even in dry-run for validation)
    if not dry_run:
        archive_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Archive directory ready: {archive_path}")

    archived: List[dict] = []
    now = datetime.now()

    # Iterate through all items in workspaces directory
    for ws in workspaces_path.iterdir():
        # Skip non-directories
        if not ws.is_dir():
            continue

        # Skip workspaces with protected prefixes
        if ws.name.startswith(SKIP_PREFIXES):
            logger.debug(f"Skipping protected workspace: {ws.name}")
            continue

        # Calculate workspace age
        try:
            last_modified = datetime.fromtimestamp(ws.stat().st_mtime)
            age_hours = (now - last_modified).total_seconds() / 3600
        except OSError as e:
            logger.warning(f"Could not stat workspace {ws.name}: {e}")
            continue

        # Check if workspace is stale
        if age_hours > max_age_hours:
            destination = archive_path / ws.name
            archive_info = {
                "name": ws.name,
                "age_hours": round(age_hours, 1),
                "source": str(ws),
                "destination": str(destination),
            }

            if dry_run:
                logger.info(
                    f"[DRY-RUN] Would archive: {ws.name} "
                    f"(age: {age_hours:.1f}h) -> {destination}"
                )
            else:
                try:
                    # Handle case where destination already exists
                    if destination.exists():
                        logger.warning(
                            f"Archive destination already exists, skipping: {destination}"
                        )
                        continue

                    shutil.move(str(ws), str(destination))
                    logger.info(
                        f"Archived stale workspace: {ws.name} (age: {age_hours:.1f}h)"
                    )
                except Exception as e:
                    logger.error(f"Failed to archive {ws.name}: {e}")
                    continue

            archived.append(archive_info)

    if archived:
        action = "Would archive" if dry_run else "Archived"
        logger.info(f"{action} {len(archived)} stale workspace(s)")
    else:
        logger.info("No stale workspaces found")

    return archived


def main() -> None:
    """CLI entry point for the session reaper."""
    parser = argparse.ArgumentParser(
        description="Archive stale agent workspaces to reduce RAM pressure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Preview what would be archived (dry-run)
    uv run python -m src.universal_agent.session.reaper --dry-run

    # Actually archive stale workspaces
    uv run python -m src.universal_agent.session.reaper --execute

    # Archive workspaces older than 12 hours
    uv run python -m src.universal_agent.session.reaper --execute --max-age 12
        """,
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be archived without making changes",
    )
    mode_group.add_argument(
        "--execute",
        action="store_true",
        help="Actually archive stale workspaces",
    )

    parser.add_argument(
        "--max-age",
        type=int,
        default=24,
        help="Maximum age in hours before a workspace is considered stale (default: 24)",
    )

    parser.add_argument(
        "--workspaces-dir",
        type=Path,
        help="Path to workspaces directory (default: AGENT_RUN_WORKSPACES)",
    )

    parser.add_argument(
        "--archive-dir",
        type=Path,
        help="Path to archive directory (default: AGENT_RUN_WORKSPACES_ARCHIVE)",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run cleanup
    dry_run = args.dry_run
    logger.info(
        f"Starting workspace cleanup (mode: {'dry-run' if dry_run else 'execute'}, "
        f"max_age: {args.max_age}h)"
    )

    result = asyncio.run(
        cleanup_stale_workspaces(
            max_age_hours=args.max_age,
            workspaces_dir=args.workspaces_dir,
            archive_dir=args.archive_dir,
            dry_run=dry_run,
        )
    )

    # Print summary
    print(f"\nSummary: {len(result)} stale workspace(s) {'would be ' if dry_run else ''}archived")
    for info in result:
        print(f"  - {info['name']} (age: {info['age_hours']}h)")


if __name__ == "__main__":
    main()
