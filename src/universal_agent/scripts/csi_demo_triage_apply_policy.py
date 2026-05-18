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
    StaleTierPolicy,
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
    parser.add_argument(
        "--age-days",
        type=int,
        default=None,
        help=(
            "Override max age in days. Default uses the configured policy "
            "(14 days for tier-3). Lower for one-time aggressive sweeps; "
            "raise to be more conservative."
        ),
    )
    parser.add_argument(
        "--max-score",
        type=float,
        default=None,
        help=(
            "Override max ranking_score for dismissal. Candidates with score "
            "<= this OR NULL get dismissed. Default 5.0 for tier-3."
        ),
    )
    parser.add_argument(
        "--tier",
        type=int,
        default=None,
        choices=[3, 4],
        help=(
            "Override which tier the policy targets. Default is the "
            "configured policy (tier 3). Pass 4 ONLY for explicit one-time "
            "purges of stale tier-4 candidates — operator-driven, not the "
            "automatic default."
        ),
    )
    parser.add_argument(
        "--policy-name",
        default=None,
        help=(
            "Override the policy name used in decided_by stamps. Useful for "
            "one-off sweeps so the audit trail distinguishes them from the "
            "default policy. Default: 'stale-tier-N-override' when other "
            "overrides are provided, else the configured policy name."
        ),
    )
    return parser.parse_args()


def _custom_policy_from_args(args: argparse.Namespace) -> StaleTierPolicy | None:
    """Build a one-off StaleTierPolicy when the operator passed overrides."""
    overrides = (args.age_days, args.max_score, args.tier)
    if all(o is None for o in overrides):
        return None
    base = DEFAULT_POLICIES[0]
    tier = args.tier if args.tier is not None else base.tier
    max_age = args.age_days if args.age_days is not None else base.max_age_days
    max_score = args.max_score if args.max_score is not None else base.max_ranking_score
    name = args.policy_name or f"stale-tier-{tier}-override"
    return StaleTierPolicy(
        name=name,
        tier=tier,
        max_age_days=max_age,
        max_ranking_score=max_score,
        decided_by=f"auto-policy:{name}",
    )


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

    custom = _custom_policy_from_args(args)
    policies = (custom,) if custom is not None else None
    if custom is not None:
        print(
            f"using one-off policy: tier={custom.tier} "
            f"max_age={custom.max_age_days}d max_score={custom.max_ranking_score} "
            f"name={custom.name}",
            file=sys.stderr,
        )

    report = apply_policies(dry_run=dry_run, policies=policies)

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
