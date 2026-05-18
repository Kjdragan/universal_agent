"""Apply the CSI demo-triage auto-dismiss policy.

Default behavior is dry-run: prints what would be dismissed but mutates
nothing. Pass ``--apply`` to actually dismiss.

Usage on the VPS::

    cd /opt/universal_agent && PYTHONPATH=src uv run python \
        -m universal_agent.scripts.csi_demo_triage_apply_policy

    cd /opt/universal_agent && PYTHONPATH=src uv run python \
        -m universal_agent.scripts.csi_demo_triage_apply_policy --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from universal_agent.services.csi_demo_triage_policy import (
    DEFAULT_POLICIES,
    apply_policies,
    policy_auto_apply_enabled,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually dismiss matching candidates (default is dry-run).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full report as JSON instead of a human-readable summary.",
    )
    parser.add_argument(
        "--require-env-opt-in",
        action="store_true",
        help=(
            "Only apply when UA_CSI_TRIAGE_AUTO_POLICY_ENABLED is truthy. "
            "Use this when scheduling via cron so the operator can stage the "
            "policy via env flip without touching crontab."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.apply and args.require_env_opt_in and not policy_auto_apply_enabled():
        print(
            "auto-apply requested but UA_CSI_TRIAGE_AUTO_POLICY_ENABLED is off; "
            "running dry-run instead.",
            file=sys.stderr,
        )
        dry_run = True
    else:
        dry_run = not args.apply

    report = apply_policies(dry_run=dry_run)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))
        return 0

    mode = "DRY-RUN" if report["dry_run"] else "APPLIED"
    print(f"== CSI demo-triage auto-policy [{mode}] @ {report['as_of']} ==")
    print(f"Policies considered: {', '.join(report['policies_applied'])}")
    if report["actions_total"] == 0:
        print("No matching candidates — queue already clean.")
        return 0
    print(f"Matched candidates : {report['actions_total']}")
    print(f"Applied dismissals : {report['actions_applied']}")
    for name, count in report["by_policy"].items():
        print(f"  • {name}: {count}")

    # Show the first 20 rows so the operator can sanity-check.
    print()
    print("First 20 affected candidates:")
    for action in report["actions"][:20]:
        score = action.get("ranking_score")
        score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
        print(
            f"  - post={action['post_id']} tier={action['tier']} "
            f"type={action['action_type']} score={score_str} "
            f"age={action['age_days']:.1f}d state_after={action['state_after']} "
            f"reason={action['reason']}"
        )
    if report["actions_total"] > 20:
        print(f"  ... +{report['actions_total'] - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
