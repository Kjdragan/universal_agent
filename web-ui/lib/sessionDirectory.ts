const API_BASE = "/api/dashboard/gateway";

export type SessionDirectoryItem = {
  session_id: string;
  is_live_session?: boolean;
  run_id?: string;
  run_status?: string;
  run_kind?: string;
  trigger_source?: string;
  attempt_count?: number;
  status: string;
  source: string;
  channel: string;
  owner: string;
  memory_mode: string;
  description?: string;
  workspace_dir?: string;
  last_activity?: string;
  active_connections?: number;
  active_runs?: number;
  last_run_source?: string;
  heartbeat_last?: number;
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asOptionalText(value: unknown): string | undefined {
  const normalized = asText(value);
  return normalized || undefined;
}

function asNumber(value: unknown): number | undefined {
  const normalized = Number(value);
  return Number.isFinite(normalized) && normalized !== 0 ? normalized : undefined;
}

function normalizeSessionDirectoryItem(item: SessionDirectoryItem): SessionDirectoryItem {
  const sessionId = asText(item.session_id);
  const source = asText(item.source) || inferSource(sessionId);
  const channel = asText(item.channel) || source;

  return {
    session_id: sessionId,
    is_live_session: item.is_live_session !== false,
    run_id: asOptionalText(item.run_id),
    run_status: asOptionalText(item.run_status),
    run_kind: asOptionalText(item.run_kind),
    trigger_source: asOptionalText(item.trigger_source),
    attempt_count: asNumber(item.attempt_count),
    status: asText(item.status) || "unknown",
    source,
    channel,
    owner: asText(item.owner) || "unknown",
    memory_mode: asText(item.memory_mode) || "direct_only",
    description: asOptionalText(item.description),
    workspace_dir: asOptionalText(item.workspace_dir),
    last_activity: asOptionalText(item.last_activity),
    active_connections: Number(item.active_connections || 0),
    active_runs: Number(item.active_runs || 0),
    last_run_source: asOptionalText(item.last_run_source),
    heartbeat_last: asNumber(item.heartbeat_last),
  };
}

function inferSource(sessionId: string): string {
  const sid = (sessionId || "").toLowerCase();
  if (sid.startsWith("tg_")) return "telegram";
  if (sid.startsWith("session_hook_") || sid.startsWith("run_session_hook_")) return "hook";
  if (sid.startsWith("session_")) return "chat";
  if (sid.startsWith("api_")) return "api";
  if (sid.startsWith("cron_")) return "cron";
  return "local";
}

function inferRunSource(row: Record<string, unknown>, sessionId: string): string {
  const triggerSource = String(row.trigger_source || "").trim().toLowerCase();
  if (triggerSource) return triggerSource;
  return inferSource(sessionId);
}

function sessionIdFromRunRow(row: Record<string, unknown>): string {
  const workspaceDir = String(row.workspace_dir || "").trim();
  if (workspaceDir) {
    const parts = workspaceDir.replace(/\\/g, "/").split("/").filter(Boolean);
    const tail = parts[parts.length - 1] || "";
    if (tail) return tail;
  }
  const runId = String(row.run_id || "").trim();
  return runId || "";
}

function mergeRunMetadata(
  baseRows: SessionDirectoryItem[],
  runRows: Array<Record<string, unknown>>,
): SessionDirectoryItem[] {
  const byWorkspace = new Map<string, Record<string, unknown>>();
  const bySessionId = new Map<string, Record<string, unknown>>();
  for (const row of runRows) {
    const workspaceDir = String(row.workspace_dir || "").trim();
    const sessionId = sessionIdFromRunRow(row);
    if (workspaceDir) byWorkspace.set(workspaceDir, row);
    if (sessionId) bySessionId.set(sessionId, row);
  }

  return baseRows.map((item) => {
    const runRow = byWorkspace.get(String(item.workspace_dir || "").trim()) || bySessionId.get(item.session_id);
    if (!runRow) return normalizeSessionDirectoryItem(item);
    return normalizeSessionDirectoryItem({
      ...item,
      run_id: runRow.run_id ? String(runRow.run_id) : item.run_id,
      run_status: runRow.status ? String(runRow.status) : item.run_status,
      run_kind: runRow.run_kind ? String(runRow.run_kind) : item.run_kind,
      trigger_source: runRow.trigger_source ? String(runRow.trigger_source) : item.trigger_source,
      attempt_count: Number(runRow.attempt_count || item.attempt_count || 0) || undefined,
      status: String(runRow.status || item.status || "unknown"),
      source: inferRunSource(runRow, item.session_id),
      channel: inferRunSource(runRow, item.session_id),
      workspace_dir: runRow.workspace_dir ? String(runRow.workspace_dir) : item.workspace_dir,
    });
  });
}

export async function fetchSessionDirectory(limit = 200): Promise<SessionDirectoryItem[]> {
  const opsUrl = `${API_BASE}/api/v1/ops/sessions?limit=${Math.max(1, limit)}&offset=0&status=all&source=all&memory_mode=all`;
  const runsUrl = `${API_BASE}/api/v1/runs`;
  try {
    const [opsRes, runsRes] = await Promise.all([
      fetch(opsUrl),
      fetch(runsUrl),
    ]);
    const runRows = runsRes.ok
      ? ((await runsRes.json()) as { runs?: Array<Record<string, unknown>> }).runs || []
      : [];
    if (opsRes.ok) {
      const opsData = (await opsRes.json()) as { sessions?: Array<Record<string, unknown>> };
      const sessions = Array.isArray(opsData.sessions) ? opsData.sessions : [];
      const baseRows = sessions.map((row) => {
        const sessionId = String(row.session_id || "");
        const sourceRaw = String(row.source || inferSource(sessionId) || "local");
        const source = sourceRaw;
        const channelRaw = String(row.channel || sourceRaw || "local");
        const channel = channelRaw;
        return normalizeSessionDirectoryItem({
          session_id: sessionId,
          is_live_session: true,
          run_id: row.run_id ? String(row.run_id) : undefined,
          run_status: row.run_status ? String(row.run_status) : undefined,
          run_kind: row.run_kind ? String(row.run_kind) : undefined,
          trigger_source: row.trigger_source ? String(row.trigger_source) : undefined,
          attempt_count: Number(row.attempt_count || 0) || undefined,
          status: String(row.status || "unknown"),
          source,
          channel,
          owner: String(row.owner || "unknown"),
          memory_mode: String(row.memory_mode || "direct_only"),
          description: row.description ? String(row.description) : undefined,
          workspace_dir: row.workspace_dir ? String(row.workspace_dir) : undefined,
          last_activity: row.last_activity ? String(row.last_activity) : undefined,
          active_connections: Number(row.active_connections || 0),
          active_runs: Number(row.active_runs || 0),
          last_run_source: row.last_run_source ? String(row.last_run_source) : undefined,
          heartbeat_last: row.heartbeat_last ? Number(row.heartbeat_last) : undefined,
        });
      });
      return mergeRunMetadata(baseRows, Array.isArray(runRows) ? runRows : []);
    }
  } catch {
    // Fallback path below.
  }

  try {
    const runsRes = await fetch(runsUrl);
    if (runsRes.ok) {
      const runData = (await runsRes.json()) as { runs?: Array<Record<string, unknown>> };
      const runRows = Array.isArray(runData.runs) ? runData.runs : [];
      if (runRows.length > 0) {
        return runRows.map((row) => {
          const sessionId = sessionIdFromRunRow(row);
          const source = inferRunSource(row, sessionId);
          return normalizeSessionDirectoryItem({
            session_id: sessionId,
            is_live_session: false,
            run_id: row.run_id ? String(row.run_id) : undefined,
            run_status: row.status ? String(row.status) : undefined,
            run_kind: row.run_kind ? String(row.run_kind) : undefined,
            trigger_source: row.trigger_source ? String(row.trigger_source) : undefined,
            attempt_count: Number(row.attempt_count || 0) || undefined,
            status: String(row.status || "unknown"),
            source,
            channel: source,
            owner: "unknown",
            memory_mode: "direct_only",
            description: undefined,
            workspace_dir: row.workspace_dir ? String(row.workspace_dir) : undefined,
            last_activity: row.updated_at ? String(row.updated_at) : row.created_at ? String(row.created_at) : undefined,
            active_connections: 0,
            active_runs: 0,
          });
        });
      }
    }
  } catch {
    // Fall through to legacy session fallback.
  }

  const legacyRes = await fetch(`${API_BASE}/api/v1/sessions`);
  if (!legacyRes.ok) return [];
  const legacyData = (await legacyRes.json()) as { sessions?: Array<Record<string, unknown>> };
  const legacySessions = Array.isArray(legacyData.sessions) ? legacyData.sessions : [];
  return legacySessions.map((row) => {
    const sessionId = String(row.session_id || "");
    const source = inferSource(sessionId);
    return normalizeSessionDirectoryItem({
      session_id: sessionId,
      is_live_session: true,
      status: String(row.status || "unknown"),
      source,
      channel: source,
      owner: "unknown",
      memory_mode: "direct_only",
      description: row.description ? String(row.description) : undefined,
      workspace_dir: row.workspace_dir
        ? String(row.workspace_dir)
        : row.workspace_path
          ? String(row.workspace_path)
          : undefined,
      last_activity: row.last_activity ? String(row.last_activity) : undefined,
      active_connections: Number(row.active_connections || 0),
      active_runs: Number(row.active_runs || 0),
    });
  });
}

export async function deleteSessionDirectoryEntry(sessionId: string): Promise<void> {
  const sid = (sessionId || "").trim();
  if (!sid) {
    throw new Error("Session ID is required");
  }
  const res = await fetch(
    `${API_BASE}/api/v1/ops/sessions/${encodeURIComponent(sid)}?confirm=true`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `Delete failed (${res.status})`);
  }
}
