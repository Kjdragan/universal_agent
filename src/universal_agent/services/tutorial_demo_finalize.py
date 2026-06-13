"""P6 deterministic finalize for completed tutorial_build VP missions.

Runs in the VP worker's terminal sync (worker_loop._execute_mission_logic)
when a tutorial_build source task completes:

  1. Validate or SYNTHESIZE `manifest.json` in the demo workspace —
     schema-compatible with services/cody_implementation.py::DemoManifest
     (read_manifest round-trips it) — from the mission row + the source
     task's metadata (video_id / video_title / video_url) when Cody
     didn't author one. Never overwrites a Cody-authored manifest.
  2. Mechanical checks (existence only, no LLM): uv-managed env present
     (.venv/ OR uv.lock OR pyproject.toml) and README.md carries run
     instructions ('## run' heading, 'uv run', or 'python ' text).
  3. Register the demo on the dashboard demo surface by symlinking the
     workspace into the walked root (gateway_server.
     _claude_code_intel_demos walks UA_DEMOS_ROOT, default /opt/ua_demos,
     via iterdir() — symlinks-to-dirs satisfy is_dir(), so the walker
     needs zero changes).

Running BEFORE worker_loop._stamp_demo_manifest_build_session means the
previously no-op'ing P5 session stamp now finds a manifest and succeeds.
Best-effort throughout: returns a result dict, never raises.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _demos_root() -> Path:
    # Mirrors gateway_server._claude_code_intel_demos_root (not imported:
    # gateway_server is a heavyweight app module; the env contract is the
    # stable interface).
    return Path(os.environ.get("UA_DEMOS_ROOT") or "/opt/ua_demos")


def _slugify(text: str, *, fallback: str) -> str:
    slug = _SLUG_RE.sub("-", str(text or "").lower()).strip("-")[:40].strip("-")
    return slug or fallback


def _readme_has_run_instructions(workspace: Path) -> bool:
    readme = workspace / "README.md"
    if not readme.is_file():
        return False
    try:
        text = readme.read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return False
    return ("## run" in text) or ("# run" in text) or ("uv run" in text) or ("python " in text)


def _mechanical_checks(workspace: Path) -> dict[str, bool]:
    return {
        "venv_or_project": (
            (workspace / ".venv").is_dir()
            or (workspace / "uv.lock").is_file()
            or (workspace / "pyproject.toml").is_file()
        ),
        "readme_run_instructions": _readme_has_run_instructions(workspace),
    }


def _synthesize_manifest(
    *,
    workspace: Path,
    mission: dict[str, Any],
    mission_id: str,
    task_meta: dict[str, Any],
    cody_mode: str,
    demo_id: str,
) -> Path:
    from universal_agent.services.cody_implementation import resolve_demo_artifacts_dir

    target = resolve_demo_artifacts_dir(workspace) / "manifest.json"
    if target.is_file():
        return target  # Cody authored one — never clobber
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        # DemoManifest-compatible core (cody_implementation.read_manifest)
        "demo_id": demo_id,
        "feature": str(task_meta.get("video_title") or mission.get("objective") or "")[:120],
        "endpoint_required": "any",
        "endpoint_hit": "zai" if cody_mode == "zai" else "anthropic_native",
        "model_used": "",
        "claude_code_version": "",
        "claude_agent_sdk_version": "",
        "wall_time_seconds": 0.0,
        # Mission reached vp.mission.completed AND passed the COMPLETION.md
        # attestation guard (worker_loop runs the guard before terminal sync).
        "acceptance_passed": True,
        "iteration": 1,
        "started_at": str(mission.get("started_at") or ""),
        "finished_at": now_iso,
        "notes": "synthesized by tutorial_demo_finalize (builder did not author manifest.json)",
        # Extra keys (read_manifest ignores unknowns; the demos walker
        # sorts on `timestamp`):
        "timestamp": now_iso,
        "build_kind": "tutorial_build",
        "video_id": str(task_meta.get("video_id") or ""),
        "video_title": str(task_meta.get("video_title") or ""),
        "video_url": str(task_meta.get("video_url") or ""),
        "manifest_synthesized": True,
    }
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _register_demo_symlink(workspace: Path, slug: str) -> tuple[str, str]:
    """Symlink the workspace into the walked demos root. Returns (demo_id, link)."""
    root = _demos_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return "", ""
    for n in range(1, 100):
        demo_id = f"{slug}__demo-{n}"
        link = root / demo_id
        try:
            if link.is_symlink() or link.exists():
                try:
                    if link.resolve() == workspace.resolve():
                        return demo_id, str(link)  # re-run: already registered
                except OSError:
                    pass
                continue
            os.symlink(str(workspace), str(link))
            return demo_id, str(link)
        except FileExistsError:
            continue
        except OSError as exc:
            logger.warning("Demo symlink registration failed at %s: %s", link, exc)
            return "", ""
    return "", ""


def finalize_tutorial_build_demo(
    *,
    task_id: str,
    task_meta: dict[str, Any],
    mission: dict[str, Any],
    mission_id: str,
    workspace_candidates: list[str],
) -> dict[str, Any]:
    """Deterministic tutorial_build demo finalize. Never raises."""
    result: dict[str, Any] = {"ok": False, "task_id": task_id, "mission_id": mission_id}
    try:
        workspace: Path | None = None
        for cand in workspace_candidates:
            if cand and Path(cand).expanduser().is_dir():
                workspace = Path(cand).expanduser()
                break
        if workspace is None:
            result["reason"] = "no_workspace_dir"
            return result
        result["workspace_dir"] = str(workspace)

        from universal_agent.services.cody_mode import resolve_from_payload

        try:
            mission_payload = json.loads(mission.get("payload_json") or "{}")
        except Exception:
            mission_payload = {}
        cody_mode = resolve_from_payload(
            mission_payload if isinstance(mission_payload, dict) else None
        )

        slug = _slugify(
            str(task_meta.get("video_title") or ""),
            fallback=_slugify(str(task_meta.get("video_id") or ""), fallback=f"tutorial-{mission_id[-8:]}"),
        )
        demo_id, demo_link = _register_demo_symlink(workspace, slug)

        manifest_path = _synthesize_manifest(
            workspace=workspace,
            mission=mission,
            mission_id=mission_id,
            task_meta=task_meta,
            cody_mode=cody_mode,
            demo_id=demo_id or f"{slug}__demo-1",
        )
        checks = _mechanical_checks(workspace)
        # Persist the checks into the manifest (additive merge).
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload["mechanical_checks"] = checks
                manifest_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        except Exception:
            pass

        result.update({
            "ok": True,
            "manifest_path": str(manifest_path),
            "checks": checks,
            "demo_id": demo_id,
            "demo_link": demo_link,
        })
        return result
    except Exception as exc:
        logger.warning("tutorial_build finalize failed for %s: %s", task_id, exc, exc_info=True)
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result
