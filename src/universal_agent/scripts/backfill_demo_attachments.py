"""Backfill `## Demos` bullets onto vault entity pages for legacy demos.

`attach_demo_to_vault_entity` (Phase 4 / `vault-demo-attach` skill) shipped
on disk but was never invoked in production. Three demos exist in
`/opt/ua_demos/<workspace>/` (custom-subagents__demo-1, webhooks__demo-1,
e3rneinuzx__demo-1) with no backlink from the corresponding entity page.

This script walks `--demos-root` (default `/opt/ua_demos/`), parses the
entity slug from each workspace dir name (`<entity_slug>__demo-N`), and
calls `attach_demo_to_vault_entity` once per workspace. Idempotent — the
helper appends a bullet rather than upserting, but running twice with
the same demo_id appends twice (so prefer running once). Use the
`detach_demo_from_vault_entity` helper if you need to clean up a
mistake.

Workspaces whose parsed slug does not have a matching entity page get a
clear log line and a non-fatal skip. Ambiguous workspace names (e.g.
`e3rneinuzx__demo-1` where no entity slug matches) can be mapped via
`--mapping <workspace_basename>=<entity_slug>` (repeatable).

Exits non-zero only when zero attachments succeed.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from universal_agent.services.claude_code_intel_replay import (
    resolve_external_vault_root,
)
from universal_agent.services.cody_evaluation import attach_demo_to_vault_entity
from universal_agent.services.cody_implementation import read_manifest

logger = logging.getLogger(__name__)

DEFAULT_DEMOS_ROOT = Path("/opt/ua_demos")


def _parse_entity_slug(workspace_name: str, *, mapping: dict[str, str]) -> str:
    """Map a workspace dir name to its entity slug.

    Default convention: `<entity_slug>__demo-N` → `<entity_slug>`.
    A `mapping` override takes priority for non-canonical names.
    """
    if workspace_name in mapping:
        return mapping[workspace_name]
    if "__demo-" in workspace_name:
        return workspace_name.split("__demo-", 1)[0]
    return workspace_name


def _parse_mappings(raw: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in raw or []:
        if "=" not in spec:
            raise SystemExit(f"--mapping must be NAME=SLUG, got: {spec!r}")
        name, slug = spec.split("=", 1)
        name = name.strip()
        slug = slug.strip()
        if not name or not slug:
            raise SystemExit(f"--mapping NAME and SLUG must both be non-empty: {spec!r}")
        out[name] = slug
    return out


def backfill(
    *,
    demos_root: Path,
    vault_root: Path,
    mapping: dict[str, str],
    dry_run: bool = False,
) -> tuple[int, int]:
    """Walk `demos_root`, attach each workspace's demo to its entity page.

    Returns ``(succeeded, total_attempted)``.
    """
    if not demos_root.exists():
        logger.error("demos_root does not exist: %s", demos_root)
        return 0, 0

    succeeded = 0
    attempted = 0
    for workspace in sorted(demos_root.iterdir()):
        if not workspace.is_dir():
            continue
        attempted += 1
        slug = _parse_entity_slug(workspace.name, mapping=mapping)
        entity_path = vault_root / "entities" / f"{slug}.md"
        manifest = read_manifest(workspace)
        if manifest is None:
            logger.warning(
                "skip %s: no manifest.json (cannot derive demo_id / endpoint_hit)",
                workspace.name,
            )
            continue
        if not entity_path.exists():
            logger.warning(
                "skip %s: no entity page at %s (use --mapping %s=<correct-slug>)",
                workspace.name,
                entity_path,
                workspace.name,
            )
            continue
        if dry_run:
            logger.info(
                "DRY-RUN would attach demo %s (workspace %s) to entity %s",
                manifest.demo_id or workspace.name,
                workspace,
                slug,
            )
            succeeded += 1
            continue
        try:
            updated = attach_demo_to_vault_entity(
                workspace_dir=workspace,
                vault_root=vault_root,
                entity_slug=slug,
                manifest=manifest,
            )
            logger.info("attached %s → %s", workspace.name, updated)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 — log per-workspace, keep going
            logger.exception("attach failed for %s: %s", workspace.name, exc)
    return succeeded, attempted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demos-root",
        type=Path,
        default=DEFAULT_DEMOS_ROOT,
        help=f"Root dir containing demo workspaces (default: {DEFAULT_DEMOS_ROOT})",
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=None,
        help="Vault root (default: resolve_external_vault_root())",
    )
    parser.add_argument(
        "--mapping",
        action="append",
        default=[],
        help="Override workspace→entity slug mapping (NAME=SLUG, repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended attachments without writing the entity pages",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    vault_root = args.vault_root or resolve_external_vault_root()
    mapping = _parse_mappings(args.mapping)

    logger.info(
        "Backfilling demos under %s into vault %s (dry_run=%s)",
        args.demos_root,
        vault_root,
        args.dry_run,
    )

    succeeded, attempted = backfill(
        demos_root=args.demos_root,
        vault_root=vault_root,
        mapping=mapping,
        dry_run=args.dry_run,
    )
    logger.info("Done: %d / %d workspaces attached.", succeeded, attempted)
    if attempted == 0:
        logger.error("No workspaces found under %s — nothing to do.", args.demos_root)
        return 1
    if succeeded == 0:
        logger.error("No attachments succeeded — see warnings above for missing entity pages or manifests.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
