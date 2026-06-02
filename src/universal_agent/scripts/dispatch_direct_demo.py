"""Dispatch a DIRECT / ungated demo build — no CSI gating, no vault entity, no
Simone review. The on-demand + testing lane for the P3 demo-workspace pattern.

Flow:
  provision_demo_workspace  →  write BRIEF/ACCEPTANCE/business_relevance  →
  dispatch_cody_demo_task(review_required=False)  →  vp_dispatch_mission(cody_mode=…)

Cody builds the demo in the workspace; on mission completion the VP-event bridge
auto-finalizes via cody_evaluation.finalize_direct_demo (mechanical endpoint check
only — completes on a manifest whose endpoint_hit matches the required endpoint).

`--cody-mode anthropic` runs on Anthropic Max (CLI client, /goal loop available).
`--cody-mode zai` runs on the ZAI/GLM SDK client (no /goal) — same detailed BRIEF.

Run ON the VPS with the prod env, e.g.:
  cd /opt/universal_agent && .venv/bin/python -m universal_agent.scripts.dispatch_direct_demo \
      --feature "Claude Agent SDK minimal query" --cody-mode anthropic
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import re
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.cody_dispatch import dispatch_cody_demo_task
from universal_agent.services.demo_workspace import provision_demo_workspace


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return (s or "direct-demo")[:48]


def _default_brief(feature: str, endpoint_required: str, cody_mode: str) -> str:
    return f"""# BRIEF — Direct demo: {feature}

This is a DIRECT (ungated, no-review) demo dispatched for {'on-demand' if cody_mode == 'anthropic' else 'ZAI-mode'} testing.

## Goal
Build a small, runnable demo that exercises **{feature}** and proves it ran on the
correct model endpoint. Keep it minimal but real — it must actually execute.

## Hard requirements
- Build inside THIS workspace. Invoke `claude` from inside the workspace dir so the
  vanilla project-local `.claude/settings.json` takes effect.
- Produce a runnable artifact (e.g. `src/demo.py`) and actually run it; capture
  stdout to `run_output.txt`.
- Write `manifest.json` in the workspace root with at least:
    - `demo_id`            — the workspace dir name
    - `feature`            — "{feature}"
    - `endpoint_required`  — "{endpoint_required}"
    - `endpoint_hit`       — the API host your session actually used
                             (`api.anthropic.com` for Anthropic, `api.z.ai` for ZAI)
    - `model_used`, `started_at`, `finished_at`, `notes`
- NO INVENTION: if something is unclear, record the gap in `BUILD_NOTES.md` rather
  than guessing.

## Endpoint
This demo must run on **{endpoint_required}**. Record the real endpoint in
`manifest.endpoint_hit` — the auto-finalizer checks `endpoint_hit` against
`{endpoint_required}` and completes the task on a match (no human review).
"""


def _default_acceptance(feature: str, endpoint_required: str) -> str:
    return f"""# ACCEPTANCE — Direct demo: {feature}

- [ ] `src/` contains a runnable demo of {feature}.
- [ ] The demo was executed; `run_output.txt` shows real output.
- [ ] `manifest.json` exists with `endpoint_hit` reflecting the real endpoint
      and `endpoint_required` = "{endpoint_required}".
- [ ] `endpoint_hit` resolves to {endpoint_required} (api.anthropic.com→anthropic_native,
      api.z.ai→zai).
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Dispatch a direct/ungated demo build.")
    ap.add_argument("--feature", required=True, help="What feature the demo exercises.")
    ap.add_argument("--demo-id", default="", help="Workspace slug (default: <feature>__direct).")
    ap.add_argument(
        "--cody-mode",
        choices=["anthropic", "zai"],
        default="anthropic",
        help="anthropic = Anthropic Max + /goal; zai = ZAI/GLM SDK, no /goal.",
    )
    ap.add_argument("--brief", default="", help="Path to a BRIEF.md to use instead of the default.")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite an existing workspace.")
    args = ap.parse_args()

    cody_mode = args.cody_mode
    endpoint_required = "anthropic_native" if cody_mode == "anthropic" else "zai"
    endpoint_profile = "anthropic_native" if cody_mode == "anthropic" else "none"
    slug = _slugify(args.feature)
    demo_id = args.demo_id.strip() or f"{slug}__direct"

    # 1. Provision the workspace (no vault entity needed).
    prov = provision_demo_workspace(
        demo_id, overwrite=args.overwrite, endpoint_profile=endpoint_profile
    )
    workspace = prov.workspace_dir

    # 2. Write the detailed BRIEF (always — operator requirement) + ACCEPTANCE +
    #    business_relevance so evaluate_demo's workspace_complete check passes.
    brief_text = (
        Path(args.brief).read_text(encoding="utf-8")
        if args.brief
        else _default_brief(args.feature, endpoint_required, cody_mode)
    )
    (workspace / "BRIEF.md").write_text(brief_text, encoding="utf-8")
    (workspace / "ACCEPTANCE.md").write_text(
        _default_acceptance(args.feature, endpoint_required), encoding="utf-8"
    )
    (workspace / "business_relevance.md").write_text(
        f"# Business relevance\n\nDirect test demo of {args.feature} "
        f"(ungated, {cody_mode} endpoint). Validates the direct-demo execution lane.\n",
        encoding="utf-8",
    )

    # 3. Create the cody_demo_task with review_required=False (the ungated flag).
    conn = connect_runtime_db(get_activity_db_path())
    try:
        task = dispatch_cody_demo_task(
            conn,
            workspace_dir=workspace,
            entity_slug=slug,
            entity_path=workspace / "BRIEF.md",
            demo_id=demo_id,
            title=f"[direct] {args.feature} ({cody_mode})",
            endpoint_required=endpoint_required,
            review_required=False,
            extra_metadata={"dispatch_channel": "direct_demo_script", "cody_mode": cody_mode},
        )
    finally:
        conn.close()
    task_id = str(task.get("task_id") or "")

    # 4. Dispatch the build mission to Cody directly (no Simone in the loop).
    from universal_agent.tools.vp_orchestration import _vp_dispatch_mission_impl

    objective = (
        f"Build the DIRECT demo in workspace {workspace}. Read BRIEF.md and "
        f"ACCEPTANCE.md first, then invoke the `cody-implements-from-brief` skill to "
        f"build, run, and write manifest.json (with endpoint_hit) + run_output.txt. "
        f"This is an ungated test demo on the {endpoint_required} endpoint."
    )
    mission_args = {
        "vp_id": "vp.coder.primary",
        "objective": objective,
        "mission_type": "task",
        "cody_mode": cody_mode,
        "task_id": task_id,
        "idempotency_key": f"direct-demo-{demo_id}",
        "metadata": {"use_goal_loop": cody_mode == "anthropic", "task_id": task_id},
    }
    result = asyncio.run(_vp_dispatch_mission_impl(mission_args))

    print("=" * 60)
    print(f"Direct demo dispatched: {demo_id}")
    print(f"  workspace        : {workspace}")
    print(f"  cody_mode        : {cody_mode}")
    print(f"  endpoint_required: {endpoint_required}")
    print(f"  task_id          : {task_id}")
    print("  review_required  : False (auto-finalize on endpoint match)")
    print(f"  mission result   : {result}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
