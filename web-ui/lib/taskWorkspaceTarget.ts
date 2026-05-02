export type TaskWorkspaceTargetInput = {
  assigned_session_id?: string | null;
  canonical_execution_session_id?: string | null;
  canonical_execution_run_id?: string | null;
  workflow_run_id?: string | null;
  links?: {
    session_id?: string | null;
    workspace_name?: string | null;
  } | null;
};

export type TaskWorkspaceTarget = {
  sessionId?: string;
  runId?: string;
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

  // Note: workspace_name alone (a path-like string with `/` in it) is NOT a
  // valid navigation identity. The backend resolver has a `workspace_dir`
  // branch but treats workspace_name as a basename only. Producers should
  // pass workspaceName as a HINT alongside sessionId/runId — the resolver
  // uses it as a fallback when the catalog lookup misses.
  const hasNavigableId = Boolean(sessionId || runId);
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
  // workspaceName is NOT propagated as a navigation identity. The backend
  // resolver treats it as a basename hint at best, and the URL contract
  // (`/?session_id=...&run_id=...`) doesn't carry it. Adding it here just
  // pollutes the resolve payload and pushes the resolver toward the wrong
  // workspace branch when sessionId/runId are already present. Tests pin
  // this contract explicitly.
  void workspaceName;
  return target;
}
