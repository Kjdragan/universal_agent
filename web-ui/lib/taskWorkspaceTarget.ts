export type TaskWorkspaceTargetInput = {
  assigned_session_id?: string | null;
  canonical_execution_session_id?: string | null;
  canonical_execution_run_id?: string | null;
  canonical_execution_workspace?: string | null;
  workflow_run_id?: string | null;
  links?: {
    session_id?: string | null;
    workspace_name?: string | null;
    workspace_dir?: string | null;
  } | null;
};

export type TaskWorkspaceTarget = {
  sessionId?: string;
  runId?: string;
  workspaceDir?: string;
  workspaceName?: string;
};

function cleanId(value: string | null | undefined): string {
  return String(value || "").trim();
}

export function resolveTaskWorkspaceTarget(
  item: TaskWorkspaceTargetInput,
): TaskWorkspaceTarget | null {
  const sessionId = cleanId(
    item.links?.session_id ||
      item.canonical_execution_session_id ||
      item.assigned_session_id,
  );
  const runId = cleanId(item.canonical_execution_run_id || item.workflow_run_id);
  const workspaceName = cleanId(item.links?.workspace_name);
  const workspaceDir = cleanId(
    item.links?.workspace_dir || item.canonical_execution_workspace,
  );

  // For VP-delegated tasks (Cody/Atlas), the upstream completed-card
  // enrichment sets sessionId/runId to the ``vp-mission-<id>`` mirror —
  // those are NOT valid keys in the resolver's session/run catalog, so
  // sending them alone yields a 404. The same payload always carries
  // ``workspace_dir`` (Cody's mission directory). The resolver's
  // workspace-path branch keys on that dir to return a usable
  // SessionViewTarget. Pass every hint we have and let the backend pick.
  const hasNavigableId = Boolean(sessionId || runId || workspaceDir || workspaceName);
  if (!hasNavigableId) {
    return null;
  }

  const target: TaskWorkspaceTarget = {};
  // For Simone-todo / daemon-executed tasks, the Task Hub card carries BOTH
  // a session_id (the daemon executor's persistent session) AND a run_id
  // (the per-task run record). The session workspace contains the actual
  // chat history, logs, and work_products/. The run workspace is just
  // metadata (manifest, activity journal, attempts/). Users clicking the
  // Workspace button want to see the agent's actual work, not the run
  // metadata. So when both identities exist, prefer the session — drop
  // run_id from the payload so the backend resolver picks the daemon's
  // session workspace via daemon-glob fallback.
  //
  // run_id is still passed when there's no session_id (archived run-only
  // entries from the run catalog).
  if (sessionId) {
    target.sessionId = sessionId;
  } else if (runId) {
    target.runId = runId;
  }
  if (workspaceDir) {
    target.workspaceDir = workspaceDir;
  }
  if (workspaceName) {
    target.workspaceName = workspaceName;
  }
  return target;
}
