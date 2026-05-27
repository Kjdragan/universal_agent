"use client";

/**
 * DelegationTracePanel — incremental "where is this task now" panel.
 *
 * Renders the accumulated delegation identifiers off a Task Hub card so
 * an operator can trace the full path of an operator-dispatched task:
 *
 *   operator quick-add
 *     → Simone seizes + routes
 *       → vp_dispatch_mission(vp.coder.primary)
 *         → Cody CLI subprocess (session_id, workspace_dir, pid)
 *           → completion
 *
 * Fields are populated incrementally by the backend as the mission
 * moves through that lifecycle — see ``task_hub.record_cody_dispatch_metadata``.
 * The panel only renders rows that have a value, so an early-stage card
 * shows just routing info and a late-stage card shows the full trace.
 *
 * Pure-display component — no fetches, no state. The data comes from
 * the projection fields exposed by ``gateway_server._task_hub_board_projection``.
 */

type Props = {
  taskId: string;
  delegationTarget?: string | null;
  // Cody-execution identifiers — populated as the mission progresses.
  codyMissionId?: string | null;
  codySessionId?: string | null;
  codyWorkspaceDir?: string | null;
  codyWorkerPid?: number | null;
  codyDispatchedAt?: string | null;
  // Orchestrator identifiers (Simone, etc.) — already used by the
  // Workspace button but useful to show in the trace so operators see
  // the full handoff chain.
  assignedAgentId?: string | null;
  assignedSessionId?: string | null;
  assignmentState?: string | null;
};

function shortId(value: string | null | undefined, head = 8): string {
  if (!value) return "";
  if (value.length <= head + 4) return value;
  return `${value.slice(0, head)}…`;
}

function fmtTs(value: string | null | undefined): string {
  if (!value) return "";
  // Show seconds resolution; the input is ISO-8601 UTC.
  try {
    const d = new Date(value);
    if (isNaN(d.getTime())) return value;
    return d.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
  } catch {
    return value;
  }
}

type Row = { label: string; value: string; full?: string; mono?: boolean };

export function DelegationTracePanel(props: Props) {
  const rows: Row[] = [];

  if (props.delegationTarget) {
    rows.push({
      label: "Delegated to",
      value: props.delegationTarget,
      mono: true,
    });
  }
  if (props.assignedAgentId) {
    rows.push({
      label: "Claimed by",
      value: props.assignedAgentId,
      mono: true,
    });
  }
  if (props.assignmentState && props.assignmentState !== "completed") {
    rows.push({
      label: "Assignment",
      value: props.assignmentState,
      mono: true,
    });
  }
  if (props.codyDispatchedAt) {
    rows.push({
      label: "Cody dispatched",
      value: fmtTs(props.codyDispatchedAt),
      mono: true,
    });
  }
  if (props.codyMissionId) {
    rows.push({
      label: "Mission",
      value: shortId(props.codyMissionId, 18),
      full: props.codyMissionId,
      mono: true,
    });
  }
  if (props.codySessionId) {
    rows.push({
      label: "Cody session",
      value: shortId(props.codySessionId, 12),
      full: props.codySessionId,
      mono: true,
    });
  } else if (props.assignedSessionId) {
    // Pre-PR-#488b state OR a non-Cody dispatch — fall back to the
    // orchestrator session so the operator still sees something.
    rows.push({
      label: "Orchestrator session",
      value: shortId(props.assignedSessionId, 18),
      full: props.assignedSessionId,
      mono: true,
    });
  }
  if (props.codyWorkerPid) {
    rows.push({
      label: "Worker PID",
      value: String(props.codyWorkerPid),
      mono: true,
    });
  }
  if (props.codyWorkspaceDir) {
    rows.push({
      label: "Workspace",
      value: `…${props.codyWorkspaceDir.slice(-48)}`,
      full: props.codyWorkspaceDir,
      mono: true,
    });
  }

  if (rows.length === 0) {
    return null;
  }

  return (
    <div className="mt-2 p-2 bg-white/[0.02] border border-white/[0.06] rounded-md">
      <div className="font-mono text-[9px] font-bold tracking-[0.12em] uppercase text-kcd-text-muted mb-1.5">
        🔗 Delegation Trace
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[10px] leading-tight">
        {rows.map((row, idx) => (
          <DelegationRow key={`${row.label}-${idx}`} row={row} />
        ))}
      </dl>
    </div>
  );
}

function DelegationRow({ row }: { row: Row }) {
  return (
    <>
      <dt className="text-kcd-text-muted opacity-70 whitespace-nowrap">
        {row.label}
      </dt>
      <dd
        className={
          row.mono
            ? "font-mono text-kcd-text-dim truncate"
            : "text-kcd-text-dim truncate"
        }
        title={row.full || row.value}
      >
        {row.value}
      </dd>
    </>
  );
}

export default DelegationTracePanel;
