"""scripts/dev/probe_csi_extractor.py — manual probe for csi_intelligence_pass.

Dev-only tool to inspect the LLM's output on real CSI packet data without
touching the vault. Use during Phase C iteration to eyeball-tune the
system prompt.

Usage:
    PYTHONPATH=src uv run python scripts/dev/probe_csi_extractor.py \\
        <packet_dir> [--action-index N] [--vault-root PATH] \\
        [--min-tier N] [--no-linked-sources]

Examples:
    # Single action by index
    PYTHONPATH=src uv run python scripts/dev/probe_csi_extractor.py \\
        /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-06/210011__ClaudeDevs \\
        --action-index 0

    # All tier-2/3 actions in a packet
    PYTHONPATH=src uv run python scripts/dev/probe_csi_extractor.py \\
        /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-07/210014__bcherny

The probe:
  - Reads ``actions.json`` from the packet
  - Reads ``linked_sources/*.md`` (truncated to keep prompts bounded)
  - Reads existing vault entity slugs from ``--vault-root`` (default: the
    canonical v1 vault on the production tree)
  - Calls ``analyze_action`` on each tier-2+ action
  - Prints input + structured output to stdout

Does NOT write to the vault. Does NOT touch the database. Pure stdout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

DEFAULT_VAULT_ROOT = Path(
    "/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence"
)


def _load_actions(packet_dir: Path) -> list[dict]:
    actions_path = packet_dir / "actions.json"
    if not actions_path.is_file():
        raise SystemExit(f"❌ {actions_path} not found")
    payload = json.loads(actions_path.read_text(encoding="utf-8"))
    actions = payload.get("actions") if isinstance(payload, dict) else payload
    if not isinstance(actions, list):
        raise SystemExit(
            f"❌ {actions_path} did not contain a list of actions "
            f"(got {type(actions).__name__})"
        )
    return [a for a in actions if isinstance(a, dict)]


def _load_linked_sources(packet_dir: Path, max_chars_each: int = 8000) -> list[str]:
    sources_dir = packet_dir / "linked_sources"
    if not sources_dir.is_dir():
        return []
    out: list[str] = []
    for path in sorted(sources_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append(text[:max_chars_each])
    return out


def _load_existing_entities(vault_root: Path) -> list[str]:
    entities_dir = vault_root / "entities"
    if not entities_dir.is_dir():
        return []
    return sorted(p.stem for p in entities_dir.glob("*.md"))


def _format_action_header(idx: int, action: dict) -> str:
    post_id = action.get("post_id") or "(no post_id)"
    tier = action.get("tier")
    handle = action.get("handle") or "(no handle)"
    return (
        f"\n{'=' * 70}\n"
        f"Action #{idx}  post={post_id}  tier={tier}  handle=@{handle}\n"
        f"{'=' * 70}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dev probe for csi_intelligence_pass.analyze_action."
    )
    parser.add_argument(
        "packet_dir",
        type=Path,
        help="Path to a packet directory containing actions.json + linked_sources/",
    )
    parser.add_argument(
        "--action-index",
        type=int,
        default=None,
        help="Probe only this action index (default: probe every tier ≥ min-tier action)",
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=DEFAULT_VAULT_ROOT,
        help=f"Vault root for existing-entity lookup (default: {DEFAULT_VAULT_ROOT})",
    )
    parser.add_argument(
        "--min-tier",
        type=int,
        default=2,
        help="Skip actions whose tier is below this value (default: 2)",
    )
    parser.add_argument(
        "--no-linked-sources",
        action="store_true",
        help="Don't pass linked_sources to the LLM (probe text-only behavior)",
    )
    args = parser.parse_args(argv)

    packet_dir: Path = args.packet_dir
    if not packet_dir.is_dir():
        print(f"❌ {packet_dir} is not a directory", file=sys.stderr)
        return 2

    actions = _load_actions(packet_dir)
    print(f"📦 Packet: {packet_dir}")
    print(f"   {len(actions)} total actions in actions.json")

    linked_sources: list[str] = []
    if not args.no_linked_sources:
        linked_sources = _load_linked_sources(packet_dir)
        print(f"   {len(linked_sources)} linked_sources/*.md files loaded")

    existing_entities = _load_existing_entities(args.vault_root)
    print(
        f"   {len(existing_entities)} existing vault entity slugs from "
        f"{args.vault_root}"
    )

    # Defer the import so probe argument-parsing failures don't trigger the
    # heavier import chain (pydantic + universal_agent package init).
    from universal_agent.services.csi_intelligence_pass import analyze_action

    indices: list[int]
    if args.action_index is not None:
        if not (0 <= args.action_index < len(actions)):
            print(
                f"❌ --action-index {args.action_index} out of range [0, {len(actions)})",
                file=sys.stderr,
            )
            return 2
        indices = [args.action_index]
    else:
        indices = list(range(len(actions)))

    n_attempted = 0
    n_skipped = 0
    n_errored = 0
    for i in indices:
        action = actions[i]
        tier = action.get("tier")
        try:
            tier_int = int(tier) if tier is not None else 0
        except (TypeError, ValueError):
            tier_int = 0
        if args.action_index is None and tier_int < args.min_tier:
            n_skipped += 1
            continue

        print(_format_action_header(i, action))
        text = str(action.get("text") or "")
        print(f"  text: {text[:250]!r}{' [...]' if len(text) > 250 else ''}")
        cls = action.get("classifier") if isinstance(action.get("classifier"), dict) else {}
        cls_reasoning = str((cls or {}).get("reasoning") or "")
        if cls_reasoning:
            print(
                f"  classifier_reasoning: {cls_reasoning[:200]!r}"
                f"{' [...]' if len(cls_reasoning) > 200 else ''}"
            )
        print(f"  linked_sources_passed: {len(linked_sources)} files")
        print(f"  existing_entities_passed: {len(existing_entities)}")
        print()

        try:
            n_attempted += 1
            delta = analyze_action(
                action=action,
                linked_sources=linked_sources,
                existing_vault_entities=existing_entities,
            )
        except Exception as exc:  # noqa: BLE001 — we want to keep the loop alive
            n_errored += 1
            print(f"  ❌ analyze_action raised: {type(exc).__name__}: {exc}")
            continue

        print("  --- VaultDelta (LLM output) ---")
        print(json.dumps(delta.model_dump(), indent=2, ensure_ascii=False))

    print()
    print("─" * 70)
    print(
        f"Done. attempted={n_attempted}  errored={n_errored}  "
        f"skipped_below_min_tier={n_skipped}"
    )
    return 0 if n_errored == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
