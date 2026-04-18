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
};

function cleanId(value: string | null | undefined): string {
  return String(value || "").trim();
}

export function resolveTaskWorkspaceTarget(
  item: TaskWorkspaceTargetInput,
): TaskWorkspaceTarget | null {
  let sessionId = cleanId(
    item.links?.session_id ||
      item.canonical_execution_session_id ||
      item.assigned_session_id,
  );
  const runId = cleanId(item.canonical_execution_run_id || item.workflow_run_id);

  if (sessionId.startsWith("daemon_")) {
    sessionId = "";
  }

  if (sessionId) {
    return runId ? { sessionId, runId } : { sessionId };
  }
  if (runId) {
    return { runId };
  }
  return null;
}
