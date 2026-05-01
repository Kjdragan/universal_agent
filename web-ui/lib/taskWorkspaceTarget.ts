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
  if (sessionId) target.sessionId = sessionId;
  if (runId) target.runId = runId;
  if (workspaceName && !workspaceName.includes("/")) {
    target.workspaceName = workspaceName;
  }
  return target;
}
