// Mirrors src/universal_agent/viewer/resolver.py:SessionViewTarget.
// Producers POST /api/viewer/resolve to normalize identity hints into
// this canonical shape, then navigate to app/page.tsx via openViewer().
//
// History/logs/files types used to live here for the parallel
// /dashboard/viewer/... route — both removed; the live three-panel UI
// in app/page.tsx hydrates from trace.json + run.log directly.

export type SessionViewTarget = {
  target_kind: "run" | "session";
  target_id: string;
  run_id: string | null;
  session_id: string | null;
  workspace_dir: string;
  is_live_session: boolean;
  source: string;
  viewer_href: string;
  // AGENT_RUN_WORKSPACES-relative dir holding a VP CLI mission's run.log /
  // cli_stream.log (vp_<vp>_external/<mission>/<mission>/). Empty unless the
  // resolver located it. Forwarded to the viewer as the `log_ws` URL param so
  // the Activity panel can rehydrate a Cody demo run whose logs live outside
  // its workspace_dir.
  log_workspace_rel?: string | null;
};

export type ResolveInput = {
  session_id?: string | null;
  run_id?: string | null;
  workspace_dir?: string | null;
  workspace_name?: string | null;
};
