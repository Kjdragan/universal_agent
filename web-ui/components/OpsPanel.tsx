"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAgentStore } from "@/lib/store";

const API_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8002";
const OPS_TOKEN = process.env.NEXT_PUBLIC_UA_OPS_TOKEN;

type SessionSummary = {
  session_id: string;
  status: string;
  last_activity?: string;
  workspace_dir?: string;
};

type SkillStatus = {
  name: string;
  enabled: boolean;
  available: boolean;
  unavailable_reason?: string | null;
};

type SystemEventItem = {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at?: string;
  session_id?: string;
  timestamp: number;
};

type PresenceItem = {
  node_id: string;
  status: string;
  reason?: string | null;
  metadata?: Record<string, unknown>;
  updated_at?: string;
  timestamp: number;
};

type ChannelStatus = {
  id: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  note?: string;
  probe?: {
    status?: string;
    checked_at?: string;
    http_status?: number;
    detail?: string;
  };
};

type ApprovalRecord = {
  approval_id: string;
  status?: string;
  summary?: string;
  requested_by?: string;
  created_at?: number;
  updated_at?: number;
  metadata?: Record<string, unknown>;
};

function buildHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (OPS_TOKEN) {
    headers["X-UA-OPS-TOKEN"] = OPS_TOKEN;
  }
  return headers;
}

function safeJsonParse(value: string): { ok: true; data: Record<string, unknown> } | { ok: false; error: string } {
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { ok: true, data: parsed as Record<string, unknown> };
    }
    return { ok: false, error: "Config must be a JSON object" };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export function OpsPanel() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [skills, setSkills] = useState<SkillStatus[]>([]);
  const [channels, setChannels] = useState<ChannelStatus[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [logTail, setLogTail] = useState<string>("");
  const [activityTail, setActivityTail] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const systemEvents = useAgentStore((state) => state.systemEvents);
  const systemPresence = useAgentStore((state) => state.systemPresence);
  const addSystemEvent = useAgentStore((state) => state.addSystemEvent);
  const setSystemPresence = useAgentStore((state) => state.setSystemPresence);
  const systemEventsRef = useRef(systemEvents);

  const [opsConfigText, setOpsConfigText] = useState("{}");
  const [opsConfigHash, setOpsConfigHash] = useState<string | null>(null);
  const [opsConfigStatus, setOpsConfigStatus] = useState<string>("Not loaded");
  const [opsConfigError, setOpsConfigError] = useState<string | null>(null);
  const [opsConfigSaving, setOpsConfigSaving] = useState(false);
  const [opsSchemaText, setOpsSchemaText] = useState<string>("{}");
  const [opsSchemaStatus, setOpsSchemaStatus] = useState<string>("Not loaded");
  const [heartbeatState, setHeartbeatState] = useState<{
    status: string;
    last_run?: string;
    last_summary?: string;
    error?: string;
  }>({ status: "Not loaded" });

  const mergedEvents = useMemo(() => {
    const byId = new Map<string, SystemEventItem>();
    for (const event of systemEvents) {
      if (!event.id) continue;
      byId.set(event.id, event);
    }
    return Array.from(byId.values()).sort((a, b) => a.timestamp - b.timestamp).slice(-200);
  }, [systemEvents]);

  useEffect(() => {
    systemEventsRef.current = systemEvents;
  }, [systemEvents]);

  const presenceList = useMemo(() => {
    const byNode = new Map<string, PresenceItem>();
    for (const presence of systemPresence) {
      byNode.set(presence.node_id, presence);
    }
    return Array.from(byNode.values()).sort((a, b) => a.node_id.localeCompare(b.node_id));
  }, [systemPresence]);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/sessions`, {
        headers: buildHeaders(),
      });
      const data = await res.json();
      const nextSessions = data.sessions || [];
      setSessions(nextSessions);
      if (nextSessions.length > 0) {
        setSelected((prev) => prev ?? nextSessions[0].session_id);
      }
    } catch (err) {
      console.error("Ops sessions fetch failed", err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/skills`, {
        headers: buildHeaders(),
      });
      const data = await res.json();
      setSkills(data.skills || []);
    } catch (err) {
      console.error("Ops skills fetch failed", err);
    }
  }, []);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/channels`, {
        headers: buildHeaders(),
      });
      const data = await res.json();
      setChannels(data.channels || []);
    } catch (err) {
      console.error("Ops channels fetch failed", err);
    }
  }, []);

  const probeChannel = useCallback(async (channelId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/channels/${channelId}/probe`, {
        method: "POST",
        headers: buildHeaders(),
      });
      const data = await res.json();
      const probe = data.probe;
      setChannels((prev) =>
        prev.map((channel) =>
          channel.id === channelId ? { ...channel, probe } : channel
        )
      );
    } catch (err) {
      console.error("Channel probe failed", err);
    }
  }, []);

  const fetchApprovals = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/approvals`, {
        headers: buildHeaders(),
      });
      const data = await res.json();
      setApprovals(data.approvals || []);
    } catch (err) {
      console.error("Ops approvals fetch failed", err);
    }
  }, []);

  const updateApproval = useCallback(async (approvalId: string, status: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/approvals/${approvalId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...buildHeaders(),
        },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) {
        throw new Error(`Approval update failed (${res.status})`);
      }
      const data = await res.json();
      const updated = data.approval as ApprovalRecord;
      setApprovals((prev) =>
        prev.map((item) =>
          item.approval_id === approvalId ? updated : item
        )
      );
    } catch (err) {
      console.error("Approval update failed", err);
    }
  }, []);

  const fetchLogs = useCallback(async (sessionId: string) => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/ops/logs/tail?session_id=${encodeURIComponent(sessionId)}&limit=120`,
        { headers: buildHeaders() },
      );
      const data = await res.json();
      setLogTail((data.lines || []).join("\n"));
    } catch (err) {
      console.error("Ops logs fetch failed", err);
    }
  }, []);

  const fetchPresence = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/system/presence`);
      const data = await res.json();
      const nodes = data.nodes || [];
      nodes.forEach((node: Record<string, unknown>) => {
        const nodeId = (node.node_id as string) ?? "gateway";
        setSystemPresence({
          node_id: nodeId,
          status: (node.status as string) ?? "unknown",
          reason: (node.reason as string) ?? undefined,
          metadata: (node.metadata as Record<string, unknown>) ?? {},
          updated_at: (node.updated_at as string) ?? undefined,
        });
      });
    } catch (err) {
      console.error("System presence fetch failed", err);
    }
  }, [setSystemPresence]);

  const fetchSystemEvents = useCallback(async (sessionId: string) => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/system/events?session_id=${encodeURIComponent(sessionId)}`,
      );
      const data = await res.json();
      const events = data.events || [];
      const existingIds = new Set(systemEventsRef.current.map((event) => event.id));
      events.forEach((event: Record<string, unknown>) => {
        const eventId = (event.event_id as string) ?? "";
        if (eventId && existingIds.has(eventId)) return;
        addSystemEvent({
          event_type: (event.type as string) ?? "system_event",
          payload: (event.payload as Record<string, unknown>) ?? {},
          created_at: (event.created_at as string) ?? undefined,
          session_id: sessionId,
        });
      });
    } catch (err) {
      console.error("System events fetch failed", err);
    }
  }, [addSystemEvent]);

  const fetchHeartbeat = useCallback(async (sessionId: string) => {
    setHeartbeatState({ status: "Loading..." });
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/heartbeat/last?session_id=${encodeURIComponent(sessionId)}`,
      );
      if (!res.ok) {
        const detail = await res.text();
        setHeartbeatState({
          status: res.status === 400 ? "Disabled" : `Unavailable (${res.status})`,
          error: detail || `Heartbeat not available (${res.status})`,
        });
        return;
      }
      const data = await res.json();
      setHeartbeatState({
        status: "OK",
        last_run: data.last_run,
        last_summary: data.last_summary,
      });
    } catch (err) {
      setHeartbeatState({
        status: "Error",
        error: (err as Error).message,
      });
    }
  }, []);

  const fetchActivityLog = useCallback(async (sessionId: string) => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/ops/sessions/${encodeURIComponent(sessionId)}/preview`,
        { headers: buildHeaders() },
      );
      if (res.ok) {
        const data = await res.json();
        setActivityTail((data.lines || []).join("\n"));
      } else {
        setActivityTail("(Activity log unavailable)");
      }
    } catch (err) {
      console.error("Ops activity fetch failed", err);
      setActivityTail("(Error fetching activity log)");
    }
  }, []);

  const deleteSession = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    if (!confirm(`Permanently delete session ${sessionId}?`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/sessions/${sessionId}?confirm=true`, {
        method: "DELETE",
        headers: buildHeaders(),
      });
      if (res.ok) {
        setSelected(null);
        fetchSessions();
      } else {
        alert("Delete failed: " + res.statusText);
      }
    } catch (err) {
      console.error("Delete session failed", err);
      alert("Delete failed");
    }
  }, [fetchSessions]);

  const resetSession = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    if (!confirm(`Reset session ${sessionId}? This will archive state.`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/sessions/${sessionId}/reset`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildHeaders(),
        },
        body: JSON.stringify({ clear_logs: true }),
      });
      if (res.ok) {
        fetchSessions(); // Status might change
        alert("Session reset successfully");
      } else {
        alert("Reset failed: " + res.statusText);
      }
    } catch (err) {
      console.error("Reset session failed", err);
      alert("Reset failed");
    }
  }, [fetchSessions]);

  const compactLogs = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/sessions/${sessionId}/compact`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildHeaders(),
        },
        body: JSON.stringify({ max_lines: 500, max_bytes: 250000 }),
      });
      if (res.ok) {
        alert("Logs compacted");
        if (selected === sessionId) fetchLogs(sessionId);
      } else {
        alert("Compact failed: " + res.statusText);
      }
    } catch (err) {
      console.error("Compact logs failed", err);
      alert("Compact failed");
    }
  }, [selected, fetchLogs]);

  const loadOpsSchema = useCallback(async () => {
    setOpsSchemaStatus("Loading...");
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/config/schema`, {
        headers: buildHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Schema load failed (${res.status})`);
      }
      const data = await res.json();
      setOpsSchemaText(JSON.stringify(data.schema || {}, null, 2));
      setOpsSchemaStatus("Loaded");
    } catch (err) {
      console.error("Ops schema fetch failed", err);
      setOpsSchemaStatus("Load failed");
    }
  }, []);

  const loadOpsConfig = useCallback(async () => {
    setOpsConfigError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/config`, { headers: buildHeaders() });
      if (!res.ok) {
        throw new Error(`Config load failed (${res.status})`);
      }
      const data = await res.json();
      const config = data.config || {};
      setOpsConfigText(JSON.stringify(config, null, 2));
      setOpsConfigHash(data.base_hash || null);
      setOpsConfigStatus("Loaded");
    } catch (err) {
      setOpsConfigError((err as Error).message);
      setOpsConfigStatus("Load failed");
    }
  }, []);

  const saveOpsConfig = useCallback(async () => {
    const parsed = safeJsonParse(opsConfigText);
    if (!parsed.ok) {
      setOpsConfigError(parsed.error);
      return;
    }
    setOpsConfigSaving(true);
    setOpsConfigError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/config`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildHeaders(),
        },
        body: JSON.stringify({
          config: parsed.data,
          base_hash: opsConfigHash,
        }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Config save failed (${res.status})`);
      }
      const data = await res.json();
      setOpsConfigText(JSON.stringify(data.config || {}, null, 2));
      setOpsConfigHash(data.base_hash || null);
      setOpsConfigStatus("Saved");
    } catch (err) {
      setOpsConfigError((err as Error).message);
      setOpsConfigStatus("Save failed");
    } finally {
      setOpsConfigSaving(false);
    }
  }, [opsConfigHash, opsConfigText]);

  useEffect(() => {
    fetchSessions();
    fetchSkills();
    fetchChannels();
    fetchApprovals();
    fetchPresence();
    loadOpsConfig();
    loadOpsSchema();
  }, [fetchApprovals, fetchChannels, fetchPresence, fetchSessions, fetchSkills, loadOpsConfig, loadOpsSchema]);

  useEffect(() => {
    if (selected) {
      fetchLogs(selected);
      fetchActivityLog(selected);
      fetchSystemEvents(selected);
      fetchHeartbeat(selected);
    }
  }, [selected, fetchLogs, fetchSystemEvents, fetchHeartbeat, fetchActivityLog]);

  return (
    <div className="flex flex-col border rounded-lg bg-background/60 overflow-hidden mb-3">
      <div className="p-3 border-b bg-muted/20 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Ops Panel</h3>
        <button
          onClick={() => {
            fetchSessions();
            fetchSkills();
            fetchChannels();
            fetchApprovals();
            fetchPresence();
            loadOpsConfig();
            loadOpsSchema();
            if (selected) {
              fetchLogs(selected);
              fetchActivityLog(selected);
              fetchSystemEvents(selected);
              fetchHeartbeat(selected);
            }
          }}
          className="text-xs px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors"
          disabled={loading}
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 p-3 text-xs">
        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">Sessions</div>
          <div className="space-y-1 max-h-40 overflow-y-auto scrollbar-thin">
            {sessions.length === 0 && (
              <div className="text-muted-foreground">No sessions found</div>
            )}
            {sessions.map((session) => (
              <button
                key={session.session_id}
                onClick={() => setSelected(session.session_id)}
                className={`w-full text-left px-2 py-1 rounded border text-xs ${selected === session.session_id
                  ? "border-primary text-primary"
                  : "border-border/50 text-muted-foreground"
                  }`}
              >
                <div className="font-mono truncate">{session.session_id}</div>
                <div className="flex justify-between">
                  <span>{session.status}</span>
                  <span className="opacity-60">
                    {session.last_activity?.slice(11, 19) ?? "--:--:--"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">Skills</div>
          <div className="space-y-1 max-h-40 overflow-y-auto scrollbar-thin">
            {skills.length === 0 && (
              <div className="text-muted-foreground">No skills found</div>
            )}
            {skills.map((skill) => (
              <div key={skill.name} className="flex items-center justify-between">
                <span className="truncate">{skill.name}</span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded ${skill.enabled && skill.available
                    ? "bg-emerald-500/10 text-emerald-500"
                    : "bg-amber-500/10 text-amber-500"
                    }`}
                >
                  {skill.enabled && skill.available ? "enabled" : "disabled"}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 px-3 pb-3 text-xs">
        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">Channels</div>
          <div className="space-y-2 max-h-40 overflow-y-auto scrollbar-thin">
            {channels.length === 0 && (
              <div className="text-muted-foreground">No channels found</div>
            )}
            {channels.map((channel) => (
              <div key={channel.id} className="border rounded px-2 py-1 bg-background/50">
                <div className="flex items-center justify-between">
                  <span className="font-mono">{channel.id}</span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded ${channel.enabled
                      ? "bg-emerald-500/10 text-emerald-500"
                      : "bg-amber-500/10 text-amber-500"
                      }`}
                  >
                    {channel.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
                <div className="text-[10px] text-muted-foreground">{channel.note}</div>
                <div className="flex items-center justify-between mt-1">
                  <span className="text-[10px] text-muted-foreground">
                    probe: {channel.probe?.status ?? "n/a"}
                  </span>
                  <button
                    type="button"
                    className="text-[10px] px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors"
                    onClick={() => probeChannel(channel.id)}
                  >
                    Probe
                  </button>
                </div>
                {channel.probe?.detail && (
                  <div className="text-[10px] text-muted-foreground mt-1">
                    {channel.probe.detail}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">Approvals</div>
          <div className="space-y-2 max-h-40 overflow-y-auto scrollbar-thin">
            {approvals.length === 0 && (
              <div className="text-muted-foreground">No approvals</div>
            )}
            {approvals.map((approval) => (
              <div key={approval.approval_id} className="border rounded px-2 py-1 bg-background/50">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] truncate">
                    {approval.approval_id}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {approval.status ?? "unknown"}
                  </span>
                </div>
                {approval.summary && (
                  <div className="text-[10px] text-muted-foreground">{approval.summary}</div>
                )}
                {approval.status === "pending" && (
                  <div className="flex gap-2 mt-2">
                    <button
                      type="button"
                      className="text-[10px] px-2 py-1 rounded border bg-emerald-500/10 text-emerald-500"
                      onClick={() => updateApproval(approval.approval_id, "approved")}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="text-[10px] px-2 py-1 rounded border bg-amber-500/10 text-amber-500"
                      onClick={() => updateApproval(approval.approval_id, "rejected")}
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 px-3 pb-3 text-xs">
        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">System Presence</div>
          <div className="space-y-1 max-h-36 overflow-y-auto scrollbar-thin">
            {presenceList.length === 0 && (
              <div className="text-muted-foreground">No presence updates</div>
            )}
            {presenceList.map((node) => (
              <div key={node.node_id} className="flex items-center justify-between">
                <span className="truncate font-mono">{node.node_id}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">
                  {node.status}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">System Events</div>
          <div className="space-y-1 max-h-36 overflow-y-auto scrollbar-thin">
            {mergedEvents.length === 0 && (
              <div className="text-muted-foreground">No system events</div>
            )}
            {mergedEvents.map((event) => (
              <div key={event.id} className="border rounded px-2 py-1 bg-background/50">
                <div className="flex justify-between text-[10px] text-muted-foreground">
                  <span>{event.event_type}</span>
                  <span>{event.created_at?.slice(11, 19) ?? "--:--:--"}</span>
                </div>
                <div className="font-mono text-[11px] truncate">
                  {Object.keys(event.payload || {}).join(", ") || "(no payload)"}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="px-3 pb-3 text-xs">
        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2 flex items-center justify-between">
            <span>Heartbeat</span>
            <span className="text-[10px] text-muted-foreground">{heartbeatState.status}</span>
          </div>
          {!selected && (
            <div className="text-muted-foreground">Select a session to view heartbeat.</div>
          )}
          {selected && (
            <div className="space-y-1">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Last run</span>
                <span className="font-mono text-[11px]">
                  {heartbeatState.last_run ?? "--"}
                </span>
              </div>
              <div className="text-muted-foreground">Last summary</div>
              <div className="text-[11px] font-mono whitespace-pre-wrap">
                {heartbeatState.last_summary ?? "(none)"}
              </div>
              {heartbeatState.error && (
                <div className="text-[10px] text-amber-500">{heartbeatState.error}</div>
              )}
            </div>
          )}
        </div>
      </div>

      {selected && (
        <div className="px-3 pb-3 text-xs">
          <div className="border rounded bg-background/40 p-2">
            <div className="font-semibold mb-2">Session Actions</div>
            <div className="flex gap-2">
              <button
                onClick={() => compactLogs(selected)}
                className="px-2 py-1 rounded border bg-blue-500/10 text-blue-500 hover:bg-blue-500/20"
              >
                Compact Logs
              </button>
              <button
                onClick={() => resetSession(selected)}
                className="px-2 py-1 rounded border bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"
              >
                Reset Session
              </button>
              <button
                onClick={() => deleteSession(selected)}
                className="px-2 py-1 rounded border bg-red-500/10 text-red-500 hover:bg-red-500/20"
              >
                Delete Session
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="px-3 pb-3 text-xs">
        <div className="border rounded bg-background/40 p-2">
          <div className="font-semibold mb-2">Activity Log (Preview)</div>
          <pre className="text-[10px] font-mono whitespace-pre-wrap max-h-32 overflow-y-auto scrollbar-thin bg-background/50 p-2 rounded border">
            {selected ? activityTail || "(empty)" : "Select a session"}
          </pre>
        </div>
      </div>

      <div className="border-t p-3">
        <div className="font-semibold text-xs mb-2">run.log tail</div>
        <pre className="text-[10px] font-mono whitespace-pre-wrap max-h-32 overflow-y-auto scrollbar-thin bg-background/50 p-2 rounded border">
          {selected ? logTail || "(empty)" : "Select a session"}
        </pre>
      </div>

      <div className="border-t p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="font-semibold text-xs">Ops config (ops_config.json)</div>
          <div className="text-[10px] text-muted-foreground">{opsConfigStatus}</div>
        </div>
        <textarea
          className="w-full min-h-[140px] text-[11px] font-mono p-2 rounded border bg-background/60"
          value={opsConfigText}
          onChange={(e) => setOpsConfigText(e.target.value)}
        />
        {opsConfigError && (
          <div className="text-[10px] text-red-500 mt-2">{opsConfigError}</div>
        )}
        <div className="flex gap-2 mt-2">
          <button
            type="button"
            className="text-xs px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors"
            onClick={loadOpsConfig}
          >
            Reload
          </button>
          <button
            type="button"
            className="text-xs px-2 py-1 rounded border bg-primary/20 text-primary hover:bg-primary/30 transition-colors disabled:opacity-50"
            onClick={saveOpsConfig}
            disabled={opsConfigSaving}
          >
            {opsConfigSaving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      <div className="border-t p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="font-semibold text-xs">Ops config schema</div>
          <div className="text-[10px] text-muted-foreground">{opsSchemaStatus}</div>
        </div>
        <textarea
          className="w-full min-h-[140px] text-[11px] font-mono p-2 rounded border bg-background/60"
          value={opsSchemaText}
          readOnly
        />
      </div>
    </div>
  );
}
