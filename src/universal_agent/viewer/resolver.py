"""Canonical resolver: any identity hint → one `SessionViewTarget`.

The resolver consolidates the daemon-workspace glob fallback (previously
in `ops_service.py:_session_workspace`), the run-catalog reverse lookup,
and the provider-session → latest-run mapping into a single function. UI
producers call `POST /api/viewer/resolve` with whatever identity hints
they have; the backend returns the canonical target including the
`viewer_href` they should navigate to.

Resolution order (first match wins; later branches backfill missing
fields where possible):

    1. run_id              → run_catalog.get_run
    2. workspace_dir       → run_catalog.find_run_for_workspace, then path-existence
    3. workspace_name      → resolve to absolute path, recurse via workspace_dir
    4. session_id (via provider_session_id mapping in run_catalog)
    5. session_id (daemon fallback) → ops_service._session_workspace, then run lookup

The `source` field on the returned target records which branch resolved
it, so production logs can reveal which path is hit most.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote


@dataclass
class SessionViewTarget:
    target_kind: str  # "run" | "session"
    target_id: str
    run_id: Optional[str]
    session_id: Optional[str]
    workspace_dir: str
    is_live_session: bool
    source: str
    viewer_href: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _viewer_href(target_kind: str, target_id: str) -> str:
    """Build the canonical viewer URL. Producers MUST use this verbatim."""
    return f"/dashboard/viewer/{quote(target_kind, safe='')}/{quote(target_id, safe='')}"


def _is_live_session(session_id: Optional[str], workspace_dir: str) -> bool:
    """Heuristic: live if the workspace has an `active_run_workspace` marker
    or the session id has a still-active daemon prefix.
    """
    if not workspace_dir:
        return False
    marker = Path(workspace_dir) / "active_run_workspace"
    if marker.exists():
        return True
    if session_id and session_id.startswith("daemon_"):
        # Daemon sessions are conceptually always-live (they re-attach to
        # whatever the latest run is). Distinct from archived runs.
        return True
    return False


def _build_target_from_run(
    run: dict[str, Any],
    *,
    source: str,
    fallback_session_id: Optional[str] = None,
) -> Optional[SessionViewTarget]:
    run_id = str(run.get("run_id") or "").strip()
    workspace_dir = str(run.get("workspace_dir") or "").strip()
    if not run_id or not workspace_dir:
        return None
    session_id = (
        str(run.get("provider_session_id") or fallback_session_id or "").strip() or None
    )
    return SessionViewTarget(
        target_kind="run",
        target_id=run_id,
        run_id=run_id,
        session_id=session_id,
        workspace_dir=workspace_dir,
        is_live_session=_is_live_session(session_id, workspace_dir),
        source=source,
        viewer_href=_viewer_href("run", run_id),
    )


def _build_target_from_path(
    workspace_dir: Path,
    *,
    source: str,
    session_id: Optional[str] = None,
) -> Optional[SessionViewTarget]:
    if not workspace_dir.is_dir():
        return None
    abs_path = str(workspace_dir.resolve())
    target_kind = "session" if session_id else "run"
    target_id = session_id or workspace_dir.name
    return SessionViewTarget(
        target_kind=target_kind,
        target_id=target_id,
        run_id=None,
        session_id=session_id,
        workspace_dir=abs_path,
        is_live_session=_is_live_session(session_id, abs_path),
        source=source,
        viewer_href=_viewer_href(target_kind, target_id),
    )


# ── Lazy imports ──────────────────────────────────────────────────────────────
# The resolver is imported by routes that may load before the durable DB is
# ready; we lazy-import the catalog and ops surfaces so import order is safe.


def _get_run_catalog():
    from universal_agent.run_catalog import RunCatalogService

    return RunCatalogService()


def _resolve_workspaces_root() -> Path:
    """Resolve the AGENT_RUN_WORKSPACES root used for daemon-glob fallback."""
    import os

    override = os.getenv("AGENT_RUN_WORKSPACES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "AGENT_RUN_WORKSPACES"


def _daemon_glob_workspace(session_id: str) -> Optional[Path]:
    """Reproduce ops_service._session_workspace's daemon-glob fallback,
    but as a free function so the viewer doesn't need an OpsService instance.

    Mirrors `ops_service.py:42-70` exactly. Future cleanup can have ops_service
    delegate to this function once the new contract is in production.
    """
    if not session_id or not session_id.startswith("daemon_"):
        return None

    root = _resolve_workspaces_root()
    if not root.is_dir():
        return None

    direct = root / session_id
    if direct.is_dir():
        return direct.resolve()

    prefix = f"run_{session_id}_"
    candidates: list[Path] = []
    try:
        for p in root.iterdir():
            if p.is_dir() and p.name.startswith(prefix):
                candidates.append(p)
        archive_root = root / "_daemon_archives"
        if archive_root.is_dir():
            for p in archive_root.iterdir():
                if p.is_dir() and p.name.startswith(prefix):
                    candidates.append(p)
    except OSError:
        return None

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].resolve()


# ── Public entry point ───────────────────────────────────────────────────────


def resolve_session_view_target(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    workspace_name: Optional[str] = None,
    trace: Optional[list[str]] = None,
) -> Optional[SessionViewTarget]:
    """Accept any combination of identity hints; return the canonical target.

    Returns None when none of the inputs resolves. Never raises on bad input.
    When `trace` is supplied, every branch records what it tried + why it
    missed — used by the route handler to surface diagnostic info on 404s.
    """
    catalog = _get_run_catalog()

    def _trace(msg: str) -> None:
        if trace is not None:
            trace.append(msg)

    # 1. run_id direct lookup
    if run_id:
        run = catalog.get_run(run_id)
        if run:
            built = _build_target_from_run(run, source="run_catalog.get_run")
            if built:
                return built
            _trace(f"run_id={run_id!r}: catalog row found but missing workspace_dir")
        else:
            _trace(f"run_id={run_id!r}: not in run_catalog")

    # 2. workspace_dir reverse lookup
    if workspace_dir:
        ws_path = Path(workspace_dir).expanduser()
        run = catalog.find_run_for_workspace(str(ws_path))
        if run:
            built = _build_target_from_run(
                run, source="run_catalog.find_run_for_workspace"
            )
            if built:
                return built
            _trace(f"workspace_dir={workspace_dir!r}: catalog row found but malformed")
        elif ws_path.is_dir():
            built = _build_target_from_path(
                ws_path, source="workspace_dir_path", session_id=session_id
            )
            if built:
                return built
        else:
            _trace(f"workspace_dir={workspace_dir!r}: not in catalog and not on disk")

    # 3. workspace_name → derive absolute path, recurse
    if workspace_name:
        root = _resolve_workspaces_root()
        candidate = root / workspace_name
        if candidate.is_dir():
            return resolve_session_view_target(
                session_id=session_id,
                run_id=run_id,
                workspace_dir=str(candidate),
                trace=trace,
            )
        # Also check the daemon archive subdir
        archive_candidate = root / "_daemon_archives" / workspace_name
        if archive_candidate.is_dir():
            return resolve_session_view_target(
                session_id=session_id,
                run_id=run_id,
                workspace_dir=str(archive_candidate),
                trace=trace,
            )
        _trace(
            f"workspace_name={workspace_name!r}: not found at "
            f"{root}/{workspace_name} or {root}/_daemon_archives/{workspace_name}"
        )

    # 4. session_id via provider_session_id mapping
    if session_id:
        run = catalog.find_latest_run_for_provider_session(session_id)
        if run:
            built = _build_target_from_run(
                run,
                source="run_catalog.find_latest_run_for_provider_session",
                fallback_session_id=session_id,
            )
            if built:
                return built
            _trace(
                f"session_id={session_id!r}: provider lookup found row but malformed"
            )
        else:
            _trace(f"session_id={session_id!r}: no provider_session_id match in catalog")

        # 5. Daemon active workspace fallback (prefix glob)
        daemon_workspace = _daemon_glob_workspace(session_id)
        if daemon_workspace is not None:
            run = catalog.find_run_for_workspace(str(daemon_workspace))
            if run:
                built = _build_target_from_run(
                    run,
                    source="daemon_glob+run_catalog",
                    fallback_session_id=session_id,
                )
                if built:
                    return built
            built = _build_target_from_path(
                daemon_workspace,
                source="daemon_glob_path",
                session_id=session_id,
            )
            if built:
                return built
        else:
            if session_id.startswith("daemon_"):
                _trace(
                    f"session_id={session_id!r}: daemon glob found no matches "
                    f"under {_resolve_workspaces_root()}"
                )

        # 6. Literal directory fallback — `<root>/<session_id>` as a basename.
        # Some non-daemon sessions persist their workspace under a directory
        # named exactly the session id (e.g. some VP missions, hooks).
        root = _resolve_workspaces_root()
        for candidate in (root / session_id, root / "_daemon_archives" / session_id):
            if candidate.is_dir():
                run = catalog.find_run_for_workspace(str(candidate.resolve()))
                if run:
                    built = _build_target_from_run(
                        run,
                        source="session_id_literal_dir+run_catalog",
                        fallback_session_id=session_id,
                    )
                    if built:
                        return built
                built = _build_target_from_path(
                    candidate,
                    source="session_id_literal_dir",
                    session_id=session_id,
                )
                if built:
                    return built
        _trace(
            f"session_id={session_id!r}: literal dir fallback also missed at "
            f"{root}/{session_id}"
        )

    return None
