const API_BASE = "/api/dashboard/gateway";

export type SessionDirectoryItem = {
  session_id: string;
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
};

function inferSource(sessionId: string): string {
  const sid = (sessionId || "").toLowerCase();
  if (sid.startsWith("tg_")) return "telegram";
  if (sid.startsWith("session_hook_")) return "hook";
  if (sid.startsWith("session_")) return "chat";
  if (sid.startsWith("api_")) return "api";
  if (sid.startsWith("cron_")) return "cron";
  return "local";
}

export async function fetchSessionDirectory(limit = 200): Promise<SessionDirectoryItem[]> {
  const opsUrl = `${API_BASE}/api/v1/ops/sessions?limit=${Math.max(1, limit)}&offset=0&status=all&source=all&memory_mode=all`;
  try {
    const opsRes = await fetch(opsUrl);
    if (opsRes.ok) {
      const opsData = (await opsRes.json()) as { sessions?: Array<Record<string, unknown>> };
      const sessions = Array.isArray(opsData.sessions) ? opsData.sessions : [];
      return sessions.map((row) => {
        const sessionId = String(row.session_id || "");
        const sourceRaw = String(row.source || inferSource(sessionId) || "local");
        const source = sourceRaw;
        const channelRaw = String(row.channel || sourceRaw || "local");
        const channel = channelRaw;
        return {
          session_id: sessionId,
          status: String(row.status || "unknown"),
          source,
          channel,
          owner: String(row.owner || "unknown"),
          memory_mode: String(row.memory_mode || "session_only"),
          description: row.description ? String(row.description) : undefined,
          workspace_dir: row.workspace_dir ? String(row.workspace_dir) : undefined,
          last_activity: row.last_activity ? String(row.last_activity) : undefined,
          active_connections: Number(row.active_connections || 0),
          active_runs: Number(row.active_runs || 0),
        };
      });
    }
  } catch {
    // Fallback path below.
  }

  // Fallback path for local-only mode when ops endpoint is unavailable.
  const legacyRes = await fetch(`${API_BASE}/api/v1/sessions`);
  if (!legacyRes.ok) return [];
  const legacyData = (await legacyRes.json()) as { sessions?: Array<Record<string, unknown>> };
  const legacySessions = Array.isArray(legacyData.sessions) ? legacyData.sessions : [];
  return legacySessions.map((row) => {
    const sessionId = String(row.session_id || "");
    const source = inferSource(sessionId);
    return {
      session_id: sessionId,
      status: String(row.status || "unknown"),
      source,
      channel: source,
      owner: "unknown",
      memory_mode: "session_only",
      description: row.description ? String(row.description) : undefined,
      workspace_dir: row.workspace_dir
        ? String(row.workspace_dir)
        : row.workspace_path
          ? String(row.workspace_path)
          : undefined,
      last_activity: row.last_activity ? String(row.last_activity) : undefined,
      active_connections: Number(row.active_connections || 0),
      active_runs: Number(row.active_runs || 0),
    };
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
