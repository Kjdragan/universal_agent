"""Phase 4 helpers: Simone monitors, evaluates, attaches Cody's demos.

Three responsibilities, one module:

  1. monitor_demo_tasks  — surface in-flight cody_demo_task state to Simone
                            so she sees what Cody is working on / stuck on.
  2. evaluate_demo       — pull together everything Simone needs to judge
                            a returned demo (manifest, briefing, build notes,
                            run output, re-run reproducibility check, endpoint
                            match verdict). Mechanical checks only — Simone
                            herself makes the pass/iterate/defer call.
  3. attach_demo_to_vault — once Simone judges a demo passing, append a
                            `## Demos` section to the vault entity page
                            pointing at the workspace.

The Python performs no LLM call. Simone is an LLM operating from a SKILL.md
that interprets the EvaluationReport this module returns.

Per the v2 design (§9), Simone's evaluator is a multi-loop director.
She can return verdicts:
  - pass     → demo accepted; mark Task Hub complete; attach to vault
  - iterate  → write FEEDBACK.md; reissue task via cody_dispatch
  - defer    → mark Task Hub deferred with reason

See docs/proactive_signals/claudedevs_intel_v2_design.md §9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from universal_agent import task_hub
from universal_agent.services.cody_dispatch import SOURCE_KIND_CODY_DEMO_TASK
from universal_agent.services.cody_implementation import (
    BriefingBundle,
    DemoManifest,
    RunResult,
    detect_endpoint_from_text,
    list_sources,
    load_briefing,
    read_manifest,
    run_in_workspace,
    workspace_for,
)

logger = logging.getLogger(__name__)


# ── Verdict surface ─────────────────────────────────────────────────────────


VERDICT_PASS = "pass"
VERDICT_ITERATE = "iterate"
VERDICT_DEFER = "defer"
VALID_VERDICTS = (VERDICT_PASS, VERDICT_ITERATE, VERDICT_DEFER)


# ── Task Hub monitoring ─────────────────────────────────────────────────────


def monitor_demo_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List every Task Hub item with source_kind=cody_demo_task.

    Returns rows enriched with their iteration count and a derived
    Simone-facing 'state' field summarizing what action she should take.
    """
    task_hub.ensure_schema(conn)
    cur = conn.execute(
        "SELECT * FROM task_hub_items WHERE source_kind = ? ORDER BY priority DESC, updated_at DESC",
        (SOURCE_KIND_CODY_DEMO_TASK,),
    )
    rows: list[dict[str, Any]] = []
    for raw in cur.fetchall():
        item = dict(raw) if not isinstance(raw, dict) else raw
        hydrated = task_hub.hydrate_item(item)
        metadata = hydrated.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "task_id": hydrated.get("task_id"),
                "title": hydrated.get("title"),
                "status": hydrated.get("status"),
                "priority": int(hydrated.get("priority") or 0),
                "demo_id": metadata.get("demo_id"),
                "entity_slug": metadata.get("entity_slug"),
                "workspace_dir": metadata.get("workspace_dir"),
                "iteration": int(metadata.get("iteration") or 1),
                "endpoint_required": metadata.get("endpoint_required") or "anthropic_native",
                "queue_policy": metadata.get("queue_policy") or "wait_indefinitely",
                "created_at": hydrated.get("created_at"),
                "updated_at": hydrated.get("updated_at"),
            }
        )
    return rows


# ── Evaluation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    """One mechanical check the evaluator performed."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class EvaluationReport:
    """Bundle of mechanical checks Simone reviews to make a verdict.

    Simone reads this in conversation, reads the briefing/manifest/build
    notes herself, and produces a pass/iterate/defer verdict + reasoning.
    The dataclass is the structured input to her judgment.
    """

    workspace_dir: str
    demo_id: str
    entity_slug: str
    manifest_present: bool
    manifest: dict[str, Any] | None
    briefing_present: bool
    sources_count: int
    build_notes_excerpt: str
    run_output_excerpt: str
    rerun: dict[str, Any] | None  # RunResult.to_dict() if rerun fired
    endpoint_match: CheckResult
    cody_self_reported_pass: CheckResult
    workspace_complete: CheckResult
    overall_mechanical_ok: bool
    iteration: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_dir": self.workspace_dir,
            "demo_id": self.demo_id,
            "entity_slug": self.entity_slug,
            "manifest_present": self.manifest_present,
            "manifest": self.manifest,
            "briefing_present": self.briefing_present,
            "sources_count": self.sources_count,
            "build_notes_excerpt": self.build_notes_excerpt[:2000],
            "run_output_excerpt": self.run_output_excerpt[:2000],
            "rerun": self.rerun,
            "endpoint_match": self.endpoint_match.to_dict(),
            "cody_self_reported_pass": self.cody_self_reported_pass.to_dict(),
            "workspace_complete": self.workspace_complete.to_dict(),
            "overall_mechanical_ok": self.overall_mechanical_ok,
            "iteration": self.iteration,
        }


def _read_excerpt(path: Path, *, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[truncated; full file at {path}]"
    return text


def evaluate_demo(
    workspace_dir: Path,
    *,
    demo_id: str = "",
    entity_slug: str = "",
    rerun_command: list[str] | tuple[str, ...] | None = None,
    rerun_timeout: int = 600,
) -> EvaluationReport:
    """Run every mechanical check Simone needs before her judgment.

    Doesn't call any LLM. Optionally re-runs the demo via run_in_workspace
    (`rerun_command` non-None) to verify reproducibility, but defaults to
    skipping that since the first end-to-end production run is operator-
    supervised. Simone passes a command when she wants to verify the demo
    runs end-to-end again.
    """
    artifacts = workspace_for(workspace_dir)

    # Manifest & briefing
    manifest_obj = read_manifest(workspace_dir)
    manifest_dict = manifest_obj.to_dict() if manifest_obj else None
    briefing = load_briefing(workspace_dir)
    sources = list_sources(workspace_dir)

    workspace_complete = CheckResult(
        name="workspace_complete",
        ok=bool(briefing.brief and briefing.acceptance and briefing.business_relevance),
        detail="" if briefing.brief and briefing.acceptance and briefing.business_relevance
               else "one or more of BRIEF.md / ACCEPTANCE.md / business_relevance.md missing or empty",
    )

    cody_self_reported_pass = CheckResult(
        name="cody_self_reported_pass",
        ok=bool(manifest_obj and manifest_obj.acceptance_passed),
        detail="" if (manifest_obj and manifest_obj.acceptance_passed) else "manifest.acceptance_passed != True",
    )

    if manifest_obj is None:
        endpoint_match = CheckResult(
            name="endpoint_match",
            ok=False,
            detail="no manifest written by Cody — endpoint cannot be verified",
        )
    elif manifest_obj.endpoint_required in ("", "any"):
        endpoint_match = CheckResult(
            name="endpoint_match",
            ok=True,
            detail="endpoint_required is 'any' — no constraint",
        )
    elif manifest_obj.endpoint_hit == manifest_obj.endpoint_required:
        endpoint_match = CheckResult(
            name="endpoint_match",
            ok=True,
            detail=f"manifest.endpoint_hit == manifest.endpoint_required ({manifest_obj.endpoint_hit})",
        )
    else:
        endpoint_match = CheckResult(
            name="endpoint_match",
            ok=False,
            detail=(
                f"endpoint mismatch: required={manifest_obj.endpoint_required!r} "
                f"hit={manifest_obj.endpoint_hit!r} — likely env-leak"
            ),
        )

    rerun_dict: dict[str, Any] | None = None
    if rerun_command is not None:
        try:
            rerun_result: RunResult = run_in_workspace(
                workspace_dir,
                list(rerun_command),
                timeout=rerun_timeout,
                scrub_env=True,
            )
            rerun_dict = rerun_result.to_dict()
            # If the rerun output reveals ZAI hints, add a soft signal
            # to the report — even if the manifest claims anthropic_native,
            # the live rerun is more authoritative.
            detected = detect_endpoint_from_text(rerun_result.stdout + "\n" + rerun_result.stderr)
            rerun_dict["detected_endpoint"] = detected
        except Exception as exc:
            rerun_dict = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:300]}

    overall = (
        workspace_complete.ok
        and endpoint_match.ok
        and cody_self_reported_pass.ok
    )

    iteration = manifest_obj.iteration if manifest_obj else 1

    return EvaluationReport(
        workspace_dir=str(workspace_dir.resolve()),
        demo_id=demo_id or (manifest_obj.demo_id if manifest_obj else ""),
        entity_slug=entity_slug or (manifest_obj.feature if manifest_obj else ""),
        manifest_present=manifest_obj is not None,
        manifest=manifest_dict,
        briefing_present=workspace_complete.ok,
        sources_count=len(sources),
        build_notes_excerpt=_read_excerpt(artifacts.build_notes_path),
        run_output_excerpt=_read_excerpt(artifacts.run_output_path),
        rerun=rerun_dict,
        endpoint_match=endpoint_match,
        cody_self_reported_pass=cody_self_reported_pass,
        workspace_complete=workspace_complete,
        overall_mechanical_ok=overall,
        iteration=iteration,
    )


# ── Feedback file ───────────────────────────────────────────────────────────


def write_feedback_file(
    workspace_dir: Path,
    *,
    feedback_markdown: str,
    iteration: int,
) -> Path:
    """Write FEEDBACK.md so Cody's next iteration knows what to change.

    Simone writes the prose; this helper just persists it with a header
    so the iteration count is unambiguous to Cody.
    """
    artifacts = workspace_for(workspace_dir)
    target = artifacts.feedback_path
    target.parent.mkdir(parents=True, exist_ok=True)
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"# Feedback for iteration {iteration}\n\n_Authored by Simone on {iso}._\n\n{feedback_markdown.strip()}\n"
    target.write_text(body, encoding="utf-8")
    return target


# ── Task Hub status updates ─────────────────────────────────────────────────


def complete_demo_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    completion_summary: str = "",
) -> dict[str, Any]:
    """Mark a cody_demo_task DONE in Task Hub and stamp completion."""
    task_hub.ensure_schema(conn)
    existing = task_hub.get_item(conn, task_id)
    if not existing:
        raise KeyError(f"task not found: {task_id}")
    metadata = dict(existing.get("metadata") or {})
    metadata["completion_summary"] = completion_summary
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    updated = dict(existing)
    updated["status"] = task_hub.TASK_STATUS_COMPLETED
    updated["agent_ready"] = False
    updated["metadata"] = metadata
    # Drop the 'agent-ready' label so upsert_item doesn't auto-restore
    # agent_ready=True from the label set.
    updated["labels"] = [
        lbl for lbl in (existing.get("labels") or []) if str(lbl) != "agent-ready"
    ]
    return task_hub.upsert_item(conn, updated)


def defer_demo_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    reason: str,
) -> dict[str, Any]:
    """Mark a cody_demo_task DEFERRED with an explicit reason.

    Used when Simone judges the demo can't be salvaged on this iteration —
    docs are too thin, feature isn't actually buildable yet, etc. The vault
    entity page records the deferral reason.
    """
    task_hub.ensure_schema(conn)
    existing = task_hub.get_item(conn, task_id)
    if not existing:
        raise KeyError(f"task not found: {task_id}")
    metadata = dict(existing.get("metadata") or {})
    metadata["deferred_reason"] = reason
    metadata["deferred_at"] = datetime.now(timezone.utc).isoformat()
    # Task Hub doesn't have a DEFERRED status — use PARKED to take it out of
    # the agent-ready queue while keeping the row for later resurrection.
    # Reason persists in metadata.deferred_reason.
    updated = dict(existing)
    updated["status"] = task_hub.TASK_STATUS_PARKED
    updated["agent_ready"] = False
    updated["metadata"] = metadata
    # Drop the 'agent-ready' label so upsert_item doesn't auto-restore
    # agent_ready=True from the label set.
    updated["labels"] = [
        lbl for lbl in (existing.get("labels") or []) if str(lbl) != "agent-ready"
    ]
    return task_hub.upsert_item(conn, updated)


# ── Vault attachment ────────────────────────────────────────────────────────


_DEMOS_SECTION_HEADER = "## Demos"


def attach_demo_to_vault_entity(
    *,
    workspace_dir: Path,
    vault_root: Path,
    entity_slug: str,
    manifest: DemoManifest | dict[str, Any] | None = None,
) -> Path:
    """Append a ## Demos section to vault/entities/<slug>.md linking the demo.

    Idempotent: if a `## Demos` section already exists, this APPENDS a
    new bullet (since v2 design §4.2 expects EXTEND on a known entity, not
    REVISE). If the section doesn't exist yet, it's created.

    `manifest` can be a DemoManifest, a plain dict, or None — the helper
    pulls demo_id and endpoint_hit if available.
    """
    entity_path = vault_root / "entities" / f"{entity_slug}.md"
    if not entity_path.exists():
        raise FileNotFoundError(f"entity page not found: {entity_path}")

    if isinstance(manifest, DemoManifest):
        manifest_dict = manifest.to_dict()
    elif isinstance(manifest, dict):
        manifest_dict = dict(manifest)
    else:
        manifest_dict = {}

    demo_id = str(manifest_dict.get("demo_id") or workspace_dir.name)
    endpoint_hit = str(manifest_dict.get("endpoint_hit") or "")
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    workspace_str = str(workspace_dir.resolve())

    bullet = f"- `{demo_id}` — `{workspace_str}` — endpoint: `{endpoint_hit}` — attached {iso}"

    text = entity_path.read_text(encoding="utf-8")
    if _DEMOS_SECTION_HEADER in text:
        # Find the section and append a bullet to its bullet list.
        # Splits on the section header so we can rebuild safely.
        header_idx = text.index(_DEMOS_SECTION_HEADER)
        before = text[: header_idx + len(_DEMOS_SECTION_HEADER)]
        after = text[header_idx + len(_DEMOS_SECTION_HEADER) :]
        # Insert bullet right after the header (preserve any content after).
        new_text = before + "\n\n" + bullet + after.lstrip("\n")
        # Avoid double-blank lines if there was already content right under
        # the header.
        if "\n\n\n" in new_text:
            new_text = new_text.replace("\n\n\n", "\n\n")
        entity_path.write_text(new_text, encoding="utf-8")
    else:
        # Append a new section at the end.
        suffix = f"\n\n{_DEMOS_SECTION_HEADER}\n\n{bullet}\n"
        entity_path.write_text(text.rstrip() + suffix, encoding="utf-8")
    return entity_path


def detach_demo_from_vault_entity(
    *,
    vault_root: Path,
    entity_slug: str,
    demo_id: str,
) -> Path:
    """Remove a demo bullet from the entity page's ## Demos section.

    Used when Simone defers/retires a demo and wants to stop pointing at
    a no-longer-canonical reference.
    """
    entity_path = vault_root / "entities" / f"{entity_slug}.md"
    if not entity_path.exists():
        raise FileNotFoundError(f"entity page not found: {entity_path}")
    text = entity_path.read_text(encoding="utf-8")
    if _DEMOS_SECTION_HEADER not in text:
        return entity_path
    lines = text.splitlines()
    keep: list[str] = []
    for line in lines:
        if line.startswith("- ") and f"`{demo_id}`" in line:
            continue
        keep.append(line)
    entity_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    return entity_path
