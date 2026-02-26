"use client";

import Link from "next/link";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAgentStore } from "@/lib/store";
import { getWebSocket } from "@/lib/websocket";
import { openOrFocusChatWindow } from "@/lib/chatWindow";

const API_BASE = "/api/dashboard/gateway";
const VPS_API_BASE = "";
const SCHED_PUSH_ENABLED = (process.env.NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED ?? "1").trim().toLowerCase() !== "0";
const RESULT_STATUSES = new Set(["success", "failed", "missed"]);

type SessionSummary = {
  session_id: string;
  status: string;
  source?: string;
  channel?: string;
  owner?: string;
  memory_mode?: string;
  last_activity?: string;
  last_modified?: string;
  workspace_dir?: string;
  active_connections?: number;
  active_runs?: number;
};
type SkillStatus = { name: string; enabled: boolean; available: boolean; unavailable_reason?: string | null };
type SystemEventItem = { id: string; event_type: string; payload: Record<string, unknown>; created_at?: string; session_id?: string; timestamp: number };
type ChannelStatus = { id: string; label: string; enabled: boolean; configured: boolean; note?: string; probe?: { status?: string; checked_at?: string; http_status?: number; detail?: string } };
type ApprovalRecord = { approval_id: string; status?: string; summary?: string; requested_by?: string; created_at?: number; updated_at?: number; metadata?: Record<string, unknown> };
type HeartbeatState = {
  status: string;
  busy?: boolean;
  last_run?: number | string;
  last_summary_raw?: unknown;
  last_summary_text?: string;
  skip_marker?: string;
  error?: string;
};
type SessionContinuityMetrics = {
  started_at?: string;
  sessions_created?: number;
  ws_attach_attempts?: number;
  ws_attach_successes?: number;
  ws_attach_failures?: number;
  resume_attempts?: number;
  resume_successes?: number;
  resume_failures?: number;
  turn_busy_rejected?: number;
  turn_duplicate_in_progress?: number;
  turn_duplicate_completed?: number;
  duplicate_turn_prevention_count?: number;
  resume_success_rate?: number | null;
  attach_success_rate?: number | null;
  transport_status?: "ok" | "degraded";
  runtime_status?: "ok" | "degraded";
  window_seconds?: number;
  window_started_at?: string;
  window_event_count?: number;
  runtime_faults?: number;
  window?: {
    resume_attempts?: number;
    resume_successes?: number;
    resume_failures?: number;
    resume_success_rate?: number | null;
    ws_attach_attempts?: number;
    ws_attach_successes?: number;
    ws_attach_failures?: number;
    attach_success_rate?: number | null;
  };
  alerts?: { code?: string; severity?: string; message?: string; actual?: number; threshold?: number; scope?: string }[];
};
type SessionContinuityState = {
  status: string;
  metrics?: SessionContinuityMetrics;
  updated_at?: string;
  error?: string;
};
type SchedulingPushState = {
  status: string;
  seq: number;
  projection_version: number;
  updated_at?: string;
  error?: string;
};
type CalendarEventItem = {
  event_id: string;
  source: "cron" | "heartbeat";
  source_ref: string;
  owner_id?: string;
  session_id?: string;
  channel?: string;
  title: string;
  description?: string;
  category?: string;
  color_key?: string;
  status: "scheduled" | "running" | "success" | "failed" | "missed" | "paused" | "disabled";
  scheduled_at_utc: string;
  scheduled_at_local: string;
  scheduled_at_epoch: number;
  timezone_display?: string;
  always_running?: boolean;
  actions?: string[];
};
type CalendarFeedResponse = {
  timezone: string;
  view: string;
  start_utc: string;
  end_utc: string;
  start_local: string;
  end_local: string;
  events: CalendarEventItem[];
  always_running: CalendarEventItem[];
  stasis_queue: Array<Record<string, unknown>>;
  legend?: Record<string, string>;
};

type DeliveryDecision = "promote" | "iterate" | "archive";
type WorkThreadRecord = {
  thread_id: string;
  session_id: string;
  status?: string;
  decision?: DeliveryDecision;
  decision_note?: string;
  patch_version?: number;
  updated_at?: number;
  history?: Array<{ decided_at?: number }>;
};
type SectionVariant = "compact" | "full";

function buildHeaders(): Record<string, string> {
  return {};
}

function safeJsonParse(v: string): { ok: true; data: Record<string, unknown> } | { ok: false; error: string } {
  try {
    const p = JSON.parse(v);
    if (p && typeof p === "object" && !Array.isArray(p)) return { ok: true, data: p };
    return { ok: false, error: "Config must be a JSON object" };
  } catch (e) { return { ok: false, error: (e as Error).message }; }
}

function tailTextLines(value: string, maxLines: number): string {
  const lines = String(value || "").split(/\r?\n/);
  if (lines.length <= maxLines) return value;
  return lines.slice(-maxLines).join("\n");
}

function parseHeartbeatSummary(raw: unknown): { text?: string; skipMarker?: string } {
  if (typeof raw === "string" || raw == null) {
    return { text: raw ?? undefined };
  }
  if (typeof raw !== "object") {
    return { text: String(raw) };
  }

  const summary = raw as { text?: string; suppressed_reason?: string };
  const suppressedReason = String(summary.suppressed_reason ?? "").trim().toLowerCase();
  let skipMarker: string | undefined;
  if (suppressedReason === "empty_content") {
    skipMarker = "Heartbeat skipped: empty HEARTBEAT.md content.";
  }
  const text = summary.text ?? skipMarker ?? JSON.stringify(raw, null, 2);
  return { text, skipMarker };
}

// ---- Context ----

type OpsCtx = {
  sessions: SessionSummary[]; skills: SkillStatus[]; channels: ChannelStatus[]; approvals: ApprovalRecord[];
  sessionsError: string | null;
  selected: string | null; setSelected: (id: string | null) => void;
  logTail: string; loading: boolean; heartbeatState: HeartbeatState; continuityState: SessionContinuityState; mergedEvents: SystemEventItem[];
  schedulingPushState: SchedulingPushState;
  fetchSessions: () => Promise<void>; fetchSkills: () => Promise<void>; fetchChannels: () => Promise<void>;
  fetchSessionContinuityMetrics: () => Promise<void>;
  fetchApprovals: () => Promise<void>; probeChannel: (id: string) => Promise<void>;
  updateApproval: (id: string, status: string) => Promise<void>; fetchLogs: (id: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>; resetSession: (id: string) => Promise<void>; compactLogs: (id: string) => Promise<void>;
  cancelSession: (id: string) => Promise<void>; cancelOutstandingRuns: () => Promise<void>; archiveSession: (id: string) => Promise<void>;
  opsConfigText: string; setOpsConfigText: (t: string) => void; opsConfigStatus: string;
  opsConfigError: string | null; opsConfigSaving: boolean;
  loadOpsConfig: () => Promise<void>; saveOpsConfig: () => Promise<void>;
  remoteSyncEnabled: boolean; remoteSyncStatus: string; remoteSyncError: string | null; remoteSyncSaving: boolean;
  loadRemoteSync: () => Promise<void>; setRemoteSync: (enabled: boolean) => Promise<void>;
  opsSchemaText: string; opsSchemaStatus: string;
  refreshAll: () => void;
};

const OpsContext = createContext<OpsCtx | null>(null);
function useOps() { const c = useContext(OpsContext); if (!c) throw new Error("useOps requires OpsProvider"); return c; }

export function OpsProvider({ children }: { children: React.ReactNode }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillStatus[]>([]);
  const [channels, setChannels] = useState<ChannelStatus[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [logTail, setLogTail] = useState("");
  const [loading, setLoading] = useState(false);
  const systemEvents = useAgentStore((s) => s.systemEvents);
  const addSystemEvent = useAgentStore((s) => s.addSystemEvent);
  const sysEvRef = useRef(systemEvents);
  const [opsConfigText, setOpsConfigText] = useState("{}");
  const [opsConfigHash, setOpsConfigHash] = useState<string | null>(null);
  const [opsConfigStatus, setOpsConfigStatus] = useState("Not loaded");
  const [opsConfigError, setOpsConfigError] = useState<string | null>(null);
  const [opsConfigSaving, setOpsConfigSaving] = useState(false);
  const [remoteSyncEnabled, setRemoteSyncEnabled] = useState(false);
  const [remoteSyncStatus, setRemoteSyncStatus] = useState("Not loaded");
  const [remoteSyncError, setRemoteSyncError] = useState<string | null>(null);
  const [remoteSyncSaving, setRemoteSyncSaving] = useState(false);
  const [opsSchemaText, setOpsSchemaText] = useState("{}");
  const [opsSchemaStatus, setOpsSchemaStatus] = useState("Not loaded");
  const [heartbeatState, setHeartbeatState] = useState<HeartbeatState>({ status: "Not loaded" });
  const [continuityState, setContinuityState] = useState<SessionContinuityState>({ status: "Not loaded" });
  const [schedulingPushState, setSchedulingPushState] = useState<SchedulingPushState>({
    status: "Not connected",
    seq: 0,
    projection_version: 0,
  });
  const currentChatSessionId = useAgentStore((s) => s.currentSession?.session_id ?? null);

  const mergedEvents = useMemo(() => {
    const m = new Map<string, SystemEventItem>();
    for (const e of systemEvents) { if (e.id) m.set(e.id, e); }
    return Array.from(m.values()).sort((a, b) => a.timestamp - b.timestamp).slice(-200);
  }, [systemEvents]);
  useEffect(() => { sysEvRef.current = systemEvents; }, [systemEvents]);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setSessionsError(null);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions`, { headers: buildHeaders() });
      if (!r.ok) {
        const dt = await r.text().catch(() => "");
        setSessionsError(dt || `Ops sessions fetch failed (${r.status})`);
        return;
      }
      const d = await r.json(); const ns = d.sessions || [];
      setSessions(ns);
      if (ns.length > 0) setSelected((p) => p ?? ns[0].session_id);
    } catch (e) {
      // Avoid console.error here: Next dev overlays console errors, and this
      // can happen transiently during local startup.
      setSessionsError((e as Error).message || "Ops sessions fetch failed");
    }
    finally { setLoading(false); }
  }, []);

  const fetchSkills = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/skills`, { headers: buildHeaders() });
      if (!r.ok) {
        throw new Error(`Ops skills fetch failed (${r.status})`);
      }
      const d = await r.json();
      setSkills(d.skills || []);
    }
    catch (e) { console.error("Ops skills fetch failed", e); }
  }, []);

  const fetchChannels = useCallback(async () => {
    try { const r = await fetch(`${API_BASE}/api/v1/ops/channels`, { headers: buildHeaders() }); const d = await r.json(); setChannels(d.channels || []); }
    catch (e) { console.error("Ops channels fetch failed", e); }
  }, []);

  const probeChannel = useCallback(async (cid: string) => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/channels/${cid}/probe`, { method: "POST", headers: buildHeaders() });
      const d = await r.json();
      setChannels((p) => p.map((c) => (c.id === cid ? { ...c, probe: d.probe } : c)));
    } catch (e) { console.error("Channel probe failed", e); }
  }, []);

  const fetchApprovals = useCallback(async () => {
    try { const r = await fetch(`${API_BASE}/api/v1/ops/approvals`, { headers: buildHeaders() }); const d = await r.json(); setApprovals(d.approvals || []); }
    catch (e) { console.error("Ops approvals fetch failed", e); }
  }, []);

  const updateApproval = useCallback(async (aid: string, status: string) => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/approvals/${aid}`, { method: "PATCH", headers: { "Content-Type": "application/json", ...buildHeaders() }, body: JSON.stringify({ status }) });
      if (!r.ok) throw new Error(`Approval update failed (${r.status})`);
      const d = await r.json();
      setApprovals((p) => p.map((i) => (i.approval_id === aid ? (d.approval as ApprovalRecord) : i)));
    } catch (e) { console.error("Approval update failed", e); }
  }, []);

  const fetchLogs = useCallback(async (sid: string) => {
    try {
      const r = await fetch(
        `${API_BASE}/api/v1/ops/logs/tail?session_id=${encodeURIComponent(sid)}&limit=120`,
        { headers: buildHeaders() },
      );
      const d = await r.json();
      const directTail = (d.lines || []).join("\n");

      if (!/^vp_/i.test(String(sid || "")) || directTail.trim()) {
        setLogTail(directTail);
        return;
      }

      // VP lanes often delegate to vp-mission-* child directories; fall back to
      // the latest mission run.log when the lane root log is empty.
      const lane = sessions.find((s) => s.session_id === sid);
      const wsHint = String(lane?.workspace_dir || "").replace(/\\/g, "/");
      const baseCandidates = new Set<string>([sid]);
      const marker = "AGENT_RUN_WORKSPACES/";
      const idx = wsHint.indexOf(marker);
      if (idx >= 0) {
        const relative = wsHint.slice(idx + marker.length).replace(/^\/+|\/+$/g, "");
        if (relative) baseCandidates.add(relative);
      }

      for (const basePath of baseCandidates) {
        const listResp = await fetch(
          `${VPS_API_BASE}/api/vps/files?scope=workspaces&path=${encodeURIComponent(basePath)}`,
          { headers: buildHeaders() },
        );
        if (!listResp.ok) continue;
        const listData = await listResp.json();
        const rows = Array.isArray(listData?.files) ? listData.files : [];
        const missions = rows
          .filter((row: any) => Boolean(row?.is_dir) && /^vp-mission-/i.test(String(row?.name || "")))
          .sort((a: any, b: any) => Number(b?.modified || 0) - Number(a?.modified || 0));
        if (!missions.length) continue;

        const latestMissionPath = `${basePath.replace(/\/+$/g, "")}/${String(missions[0].name)}/run.log`;
        const runLogResp = await fetch(
          `${VPS_API_BASE}/api/vps/file?scope=workspaces&path=${encodeURIComponent(latestMissionPath)}`,
          { headers: buildHeaders() },
        );
        if (!runLogResp.ok) continue;
        const missionText = await runLogResp.text();
        if (!missionText.trim()) continue;
        setLogTail(tailTextLines(missionText, 120));
        return;
      }

      setLogTail("");
    }
    catch (e) { console.error("Ops logs fetch failed", e); }
  }, [sessions]);

  const fetchSystemEvents = useCallback(async (sid: string) => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/system/events?session_id=${encodeURIComponent(sid)}`);
      const d = await r.json(); const evts = d.events || [];
      const existing = new Set(sysEvRef.current.map((e) => e.id));
      evts.forEach((e: Record<string, unknown>) => {
        const eid = (e.event_id as string) ?? "";
        if (eid && existing.has(eid)) return;
        addSystemEvent({ event_type: (e.type as string) ?? "system_event", payload: (e.payload as Record<string, unknown>) ?? {}, created_at: (e.created_at as string) ?? undefined, session_id: sid });
      });
    } catch (e) { console.error("System events fetch failed", e); }
  }, [addSystemEvent]);

  const fetchHeartbeat = useCallback(async (sid: string) => {
    setHeartbeatState({ status: "Loading..." });
    try {
      const r = await fetch(`${API_BASE}/api/v1/heartbeat/last?session_id=${encodeURIComponent(sid)}`);
      if (!r.ok) { const dt = await r.text(); setHeartbeatState({ status: r.status === 400 ? "Disabled" : `Unavailable (${r.status})`, error: dt || `Heartbeat not available (${r.status})` }); return; }
      const d = await r.json(); const raw = d.last_summary;
      const summary = parseHeartbeatSummary(raw);
      setHeartbeatState({
        status: "OK",
        busy: Boolean(d.busy),
        last_run: d.last_run,
        last_summary_raw: raw,
        last_summary_text: summary.text,
        skip_marker: summary.skipMarker,
      });
    } catch (e) { setHeartbeatState({ status: "Error", error: (e as Error).message }); }
  }, []);

  const fetchSessionContinuityMetrics = useCallback(async () => {
    setContinuityState((prev) => ({ ...prev, status: "Loading..." }));
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/metrics/session-continuity`, { headers: buildHeaders() });
      if (!r.ok) {
        const dt = await r.text();
        setContinuityState({
          status: `Unavailable (${r.status})`,
          error: dt || `Metrics endpoint unavailable (${r.status})`,
          updated_at: new Date().toISOString(),
        });
        return;
      }
      const d = await r.json();
      setContinuityState({
        status: "OK",
        metrics: (d.metrics || {}) as SessionContinuityMetrics,
        updated_at: new Date().toISOString(),
      });
    } catch (e) {
      setContinuityState({
        status: "Error",
        error: (e as Error).message,
        updated_at: new Date().toISOString(),
      });
    }
  }, []);

  const deleteSession = useCallback(async (sid: string) => {
    if (!sid || !confirm(`Permanently delete session ${sid}?`)) return;
    try { const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${sid}?confirm=true`, { method: "DELETE", headers: buildHeaders() }); if (r.ok) { setSelected(null); fetchSessions(); } else alert("Delete failed: " + r.statusText); }
    catch (e) { console.error("Delete session failed", e); alert("Delete failed"); }
  }, [fetchSessions]);

  const resetSession = useCallback(async (sid: string) => {
    if (!sid || !confirm(`Reset session ${sid}? This will archive state.`)) return;
    try { const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${sid}/reset`, { method: "POST", headers: { "Content-Type": "application/json", ...buildHeaders() }, body: JSON.stringify({ clear_logs: true }) }); if (r.ok) { fetchSessions(); alert("Session reset successfully"); } else alert("Reset failed: " + r.statusText); }
    catch (e) { console.error("Reset session failed", e); alert("Reset failed"); }
  }, [fetchSessions]);

  const compactLogs = useCallback(async (sid: string) => {
    if (!sid) return;
    try { const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${sid}/compact`, { method: "POST", headers: { "Content-Type": "application/json", ...buildHeaders() }, body: JSON.stringify({ max_lines: 500, max_bytes: 250000 }) }); if (r.ok) { alert("Logs compacted"); if (selected === sid) fetchLogs(sid); } else alert("Compact failed: " + r.statusText); }
    catch (e) { console.error("Compact logs failed", e); alert("Compact failed"); }
  }, [selected, fetchLogs]);

  const cancelSession = useCallback(async (sid: string) => {
    if (!sid) return;
    if (!confirm(`Cancel active work for session ${sid}?`)) return;
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${sid}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({ reason: "Cancelled from ops session controls" }),
      });
      if (r.ok) {
        fetchSessions();
        alert("Cancel request sent");
      } else {
        alert("Cancel failed: " + r.statusText);
      }
    } catch (e) {
      console.error("Cancel session failed", e);
      alert("Cancel failed");
    }
  }, [fetchSessions]);

  const cancelOutstandingRuns = useCallback(async () => {
    if (!confirm("Cancel all currently running session work?")) return;
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({ reason: "Cancelled from ops bulk session controls" }),
      });
      if (r.ok) {
        const data = await r.json();
        fetchSessions();
        const count = Array.isArray(data.sessions_cancelled) ? data.sessions_cancelled.length : 0;
        alert(`Cancel request sent for ${count} session(s)`);
      } else {
        alert("Cancel all failed: " + r.statusText);
      }
    } catch (e) {
      console.error("Cancel all sessions failed", e);
      alert("Cancel all failed");
    }
  }, [fetchSessions]);

  const archiveSession = useCallback(async (sid: string) => {
    if (!sid) return;
    if (!confirm(`Archive logs for session ${sid}?`)) return;
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${sid}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({ clear_memory: false, clear_work_products: false }),
      });
      if (r.ok) {
        fetchSessions();
        alert("Session archived");
      } else {
        alert("Archive failed: " + r.statusText);
      }
    } catch (e) {
      console.error("Archive session failed", e);
      alert("Archive failed");
    }
  }, [fetchSessions]);

  const loadOpsSchema = useCallback(async () => {
    setOpsSchemaStatus("Loading...");
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/config/schema`, { headers: buildHeaders() });
      if (!r.ok) {
        setOpsSchemaText("{}");
        setOpsSchemaStatus(`Unavailable (${r.status})`);
        return;
      }
      const d = await r.json();
      setOpsSchemaText(JSON.stringify(d.schema || {}, null, 2));
      setOpsSchemaStatus("Loaded");
    } catch {
      setOpsSchemaText("{}");
      setOpsSchemaStatus("Unavailable");
    }
  }, []);

  const loadOpsConfig = useCallback(async () => {
    setOpsConfigError(null);
    try { const r = await fetch(`${API_BASE}/api/v1/ops/config`, { headers: buildHeaders() }); if (!r.ok) throw new Error(`Config load failed (${r.status})`); const d = await r.json(); setOpsConfigText(JSON.stringify(d.config || {}, null, 2)); setOpsConfigHash(d.base_hash || null); setOpsConfigStatus("Loaded"); }
    catch (e) { setOpsConfigError((e as Error).message); setOpsConfigStatus("Load failed"); }
  }, []);

  const saveOpsConfig = useCallback(async () => {
    const p = safeJsonParse(opsConfigText); if (!p.ok) { setOpsConfigError(p.error); return; }
    setOpsConfigSaving(true); setOpsConfigError(null);
    try { const r = await fetch(`${API_BASE}/api/v1/ops/config`, { method: "POST", headers: { "Content-Type": "application/json", ...buildHeaders() }, body: JSON.stringify({ config: p.data, base_hash: opsConfigHash }) }); if (!r.ok) { const dt = await r.text(); throw new Error(dt || `Config save failed (${r.status})`); } const d = await r.json(); setOpsConfigText(JSON.stringify(d.config || {}, null, 2)); setOpsConfigHash(d.base_hash || null); setOpsConfigStatus("Saved"); }
    catch (e) { setOpsConfigError((e as Error).message); setOpsConfigStatus("Save failed"); }
    finally { setOpsConfigSaving(false); }
  }, [opsConfigHash, opsConfigText]);

  const loadRemoteSync = useCallback(async () => {
    setRemoteSyncError(null);
    setRemoteSyncStatus("Loading...");
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/remote-sync`, { headers: buildHeaders() });
      if (!r.ok) throw new Error(`Remote sync load failed (${r.status})`);
      const d = await r.json();
      const enabled = Boolean(d.enabled);
      setRemoteSyncEnabled(enabled);
      setRemoteSyncStatus(enabled ? "Enabled" : "Disabled");
    } catch (e) {
      setRemoteSyncEnabled(false);
      setRemoteSyncStatus("Unavailable");
      setRemoteSyncError((e as Error).message);
    }
  }, []);

  const setRemoteSync = useCallback(async (enabled: boolean) => {
    setRemoteSyncSaving(true);
    setRemoteSyncError(null);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/remote-sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) {
        const dt = await r.text();
        throw new Error(dt || `Remote sync update failed (${r.status})`);
      }
      const d = await r.json();
      const nextEnabled = Boolean(d.enabled);
      setRemoteSyncEnabled(nextEnabled);
      setRemoteSyncStatus(nextEnabled ? "Enabled" : "Disabled");
      // Keep raw config text aligned after toggle updates.
      loadOpsConfig();
    } catch (e) {
      setRemoteSyncError((e as Error).message);
      setRemoteSyncStatus("Update failed");
    } finally {
      setRemoteSyncSaving(false);
    }
  }, [loadOpsConfig]);

  const refreshAll = useCallback(() => {
    fetchSessions(); fetchSkills(); fetchChannels(); fetchApprovals(); loadOpsConfig(); loadOpsSchema(); loadRemoteSync();
    fetchSessionContinuityMetrics();
    if (selected) { fetchLogs(selected); fetchSystemEvents(selected); }
    const heartbeatSessionId = currentChatSessionId || selected;
    if (heartbeatSessionId) fetchHeartbeat(heartbeatSessionId);
  }, [fetchSessions, fetchSkills, fetchChannels, fetchApprovals, loadOpsConfig, loadOpsSchema, loadRemoteSync, fetchSessionContinuityMetrics, selected, currentChatSessionId, fetchLogs, fetchSystemEvents, fetchHeartbeat]);

  useEffect(() => { fetchSessions(); fetchSkills(); fetchChannels(); fetchApprovals(); loadOpsConfig(); loadOpsSchema(); loadRemoteSync(); fetchSessionContinuityMetrics(); }, [fetchApprovals, fetchChannels, fetchSessions, fetchSkills, loadOpsConfig, loadOpsSchema, loadRemoteSync, fetchSessionContinuityMetrics]);
  useEffect(() => {
    if (selected) {
      fetchLogs(selected);
      fetchSystemEvents(selected);
    }
    const heartbeatSessionId = currentChatSessionId || selected;
    if (heartbeatSessionId) {
      fetchHeartbeat(heartbeatSessionId);
    }
  }, [selected, currentChatSessionId, fetchLogs, fetchSystemEvents, fetchHeartbeat]);

  useEffect(() => {
    if (!SCHED_PUSH_ENABLED) {
      setSchedulingPushState((prev) => ({
        ...prev,
        status: "Disabled",
        updated_at: new Date().toISOString(),
        error: undefined,
      }));
      return;
    }
    let cancelled = false;
    let es: EventSource | null = null;
    let reconnectTimer: number | null = null;
    let heartbeatTimer: number | null = null;
    let continuityTimer: number | null = null;
    let sinceSeq = 0;

    const queueHeartbeatRefresh = () => {
      if (heartbeatTimer !== null) return;
      heartbeatTimer = window.setTimeout(() => {
        heartbeatTimer = null;
        const sid = currentChatSessionId || selected;
        if (sid) fetchHeartbeat(sid);
      }, 300);
    };

    const queueContinuityRefresh = () => {
      if (continuityTimer !== null) return;
      continuityTimer = window.setTimeout(() => {
        continuityTimer = null;
        fetchSessionContinuityMetrics();
      }, 750);
    };

    const connect = () => {
      const params = new URLSearchParams({
        since_seq: String(sinceSeq),
        heartbeat_seconds: "20",
        limit: "500",
      });
      const url = `${API_BASE}/api/v1/ops/scheduling/stream?${params.toString()}`;

      setSchedulingPushState((prev) => ({
        ...prev,
        status: "Connecting",
        error: undefined,
      }));
      es = new EventSource(url);

      es.onopen = () => {
        setSchedulingPushState((prev) => ({
          ...prev,
          status: "Connected",
          updated_at: new Date().toISOString(),
          error: undefined,
        }));
      };

      es.onmessage = (message) => {
        let payload: Record<string, unknown> = {};
        try {
          payload = JSON.parse(message.data || "{}") as Record<string, unknown>;
        } catch {
          return;
        }
        const kind = String(payload.kind || "");
        const projectionVersion = Number(payload.projection_version || 0);
        const event = (payload.event && typeof payload.event === "object") ? payload.event as Record<string, unknown> : null;
        const seq = Number((event && event.seq) || payload.seq || 0);
        if (Number.isFinite(seq) && seq > sinceSeq) {
          sinceSeq = seq;
        }
        setSchedulingPushState((prev) => ({
          ...prev,
          status: "Connected",
          seq: Math.max(prev.seq, Number.isFinite(seq) ? seq : prev.seq),
          projection_version: Math.max(prev.projection_version, Number.isFinite(projectionVersion) ? projectionVersion : prev.projection_version),
          updated_at: new Date().toISOString(),
          error: undefined,
        }));

        if (kind === "event" && event) {
          const source = String(event.source || "");
          if (source === "heartbeat") queueHeartbeatRefresh();
          if (source === "heartbeat" || source === "cron") queueContinuityRefresh();
        }
      };

      es.onerror = () => {
        if (es) {
          es.close();
          es = null;
        }
        setSchedulingPushState((prev) => ({
          ...prev,
          status: "Disconnected",
          updated_at: new Date().toISOString(),
          error: "Scheduling push stream disconnected",
        }));
        if (!cancelled) {
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connect();
          }, 3000);
        }
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (heartbeatTimer !== null) window.clearTimeout(heartbeatTimer);
      if (continuityTimer !== null) window.clearTimeout(continuityTimer);
      if (es) es.close();
    };
  }, [currentChatSessionId, selected, fetchHeartbeat, fetchSessionContinuityMetrics]);

  // Degraded-mode watchdog polling only when push is unavailable.
  useEffect(() => {
    if (schedulingPushState.status === "Connected") return;
    const heartbeatSessionId = currentChatSessionId || selected;
    const heartbeatTimer = window.setInterval(() => {
      if (!heartbeatSessionId) return;
      fetchHeartbeat(heartbeatSessionId);
    }, 20000);
    const continuityTimer = window.setInterval(() => {
      fetchSessionContinuityMetrics();
    }, 45000);
    return () => {
      window.clearInterval(heartbeatTimer);
      window.clearInterval(continuityTimer);
    };
  }, [schedulingPushState.status, currentChatSessionId, selected, fetchHeartbeat, fetchSessionContinuityMetrics]);

  const val: OpsCtx = useMemo(() => ({
    sessions, sessionsError, skills, channels, approvals, selected, setSelected, logTail, loading, heartbeatState, continuityState, mergedEvents, schedulingPushState,
    fetchSessions, fetchSkills, fetchChannels, fetchSessionContinuityMetrics, fetchApprovals, probeChannel, updateApproval, fetchLogs,
    deleteSession, resetSession, compactLogs, cancelSession, cancelOutstandingRuns, archiveSession, opsConfigText, setOpsConfigText, opsConfigStatus, opsConfigError,
    opsConfigSaving, loadOpsConfig, saveOpsConfig, remoteSyncEnabled, remoteSyncStatus, remoteSyncError, remoteSyncSaving, loadRemoteSync, setRemoteSync, opsSchemaText, opsSchemaStatus, refreshAll,
  }), [sessions, sessionsError, skills, channels, approvals, selected, logTail, loading, heartbeatState, continuityState, mergedEvents, schedulingPushState,
    fetchSessions, fetchSkills, fetchChannels, fetchSessionContinuityMetrics, fetchApprovals, probeChannel, updateApproval, fetchLogs,
    deleteSession, resetSession, compactLogs, cancelSession, cancelOutstandingRuns, archiveSession, opsConfigText, opsConfigStatus, opsConfigError,
    opsConfigSaving, loadOpsConfig, saveOpsConfig, remoteSyncEnabled, remoteSyncStatus, remoteSyncError, remoteSyncSaving, loadRemoteSync, setRemoteSync, opsSchemaText, opsSchemaStatus, refreshAll]);

  return <OpsContext.Provider value={val}>{children}</OpsContext.Provider>;
}

// ---- Section Components ----

export function SessionsSection({
  variant = "compact",
  showBackToHome = false,
}: {
  variant?: SectionVariant;
  showBackToHome?: boolean;
} = {}) {
  const isFull = variant === "full";
  const { sessions, sessionsError, selected, setSelected, loading, logTail, fetchSessions, fetchLogs, deleteSession, resetSession, compactLogs, cancelSession, cancelOutstandingRuns, archiveSession } = useOps();
  const [attaching, setAttaching] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"all" | "running" | "idle" | "terminal">("all");
  const [sourceFilter, setSourceFilter] = useState<"all" | "chat" | "telegram" | "api" | "local">("all");
  const [memoryModeFilter, setMemoryModeFilter] = useState<"all" | "off" | "direct_only" | "all_scope" | "memory_only">("all");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [showList, setShowList] = useState(isFull);
  const [expandLogTail, setExpandLogTail] = useState(true);
  const [selectedThread, setSelectedThread] = useState<WorkThreadRecord | null>(null);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [deliveryDecisionBusy, setDeliveryDecisionBusy] = useState(false);
  const [deliveryNoteDraft, setDeliveryNoteDraft] = useState("");
  const [deliveryStatus, setDeliveryStatus] = useState<string>("");
  const isVpSelected = /^vp_/i.test((selected || "").trim());

  const loadWorkThread = useCallback(async (sessionId: string) => {
    setDeliveryLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/work-threads?session_id=${encodeURIComponent(sessionId)}`, { headers: buildHeaders() });
      if (!r.ok) {
        setSelectedThread(null);
        return;
      }
      const d = await r.json();
      const rows = Array.isArray(d.threads) ? d.threads : [];
      setSelectedThread((rows[0] as WorkThreadRecord | undefined) ?? null);
    } catch {
      setSelectedThread(null);
    } finally {
      setDeliveryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selected) {
      setSelectedThread(null);
      setDeliveryNoteDraft("");
      setDeliveryStatus("");
      return;
    }
    void loadWorkThread(selected);
    setDeliveryStatus("");
  }, [selected, loadWorkThread]);

  useEffect(() => {
    setDeliveryNoteDraft(selectedThread?.decision_note || "");
  }, [selectedThread]);

  useEffect(() => {
    if (isFull) setShowList(true);
  }, [isFull]);

  const recordDeliveryDecision = useCallback(async (sessionId: string, decision: DeliveryDecision) => {
    setDeliveryDecisionBusy(true);
    const note = deliveryNoteDraft.trim();
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/work-threads/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({
          session_id: sessionId,
          decision,
          note: note || undefined,
          metadata: {
            vp_observer_lane: isVpSelected,
            source: "ops.sessions.delivery_workflow",
          },
        }),
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        setDeliveryStatus(`Delivery decision failed (${r.status})${detail ? `: ${detail}` : ""}`);
        return;
      }
      const d = await r.json();
      setSelectedThread((d.thread as WorkThreadRecord) ?? null);
      setDeliveryStatus(
        decision === "promote"
          ? "Promote Now recorded to Work Thread."
          : decision === "iterate"
            ? "Open Iteration recorded. Continue refinement in Simone chat."
            : "Archive Draft recorded to Work Thread."
      );
    } catch (e) {
      setDeliveryStatus(`Delivery decision failed: ${(e as Error).message}`);
    } finally {
      setDeliveryDecisionBusy(false);
    }
  }, [deliveryNoteDraft, isVpSelected]);

  const selectedDecision = selectedThread;

  const attachToChat = async (sessionId: string) => {
    setAttaching(true);
    try {
      const dashboardRoute =
        typeof window !== "undefined" && window.location.pathname.startsWith("/dashboard");
      if (dashboardRoute) {
        openOrFocusChatWindow({
          sessionId,
          attachMode: "tail",
          role: /^vp_/i.test((sessionId || "").trim()) ? "viewer" : "writer",
        });
        return;
      }
      const store = useAgentStore.getState();
      store.reset();
      store.setSessionAttachMode("tail");
      const ws = getWebSocket();
      ws.attachToSession(sessionId);
    } finally {
      setAttaching(false);
    }
  };

  const filteredSessions = useMemo(() => {
    const filtered = sessions.filter((s) => {
      const statusMatch = statusFilter === "all" || (s.status || "").toLowerCase() === statusFilter;
      const source = (s.source || s.channel || "local").toLowerCase();
      const sourceMatch = sourceFilter === "all" || source === sourceFilter;
      const memoryMode = (s.memory_mode || "direct_only").toLowerCase();
      const filterValue = memoryModeFilter === "all_scope" ? "all" : memoryModeFilter;
      const memoryModeMatch = memoryModeFilter === "all" || memoryMode === filterValue;
      const owner = (s.owner || "").toLowerCase();
      const ownerMatch = !ownerFilter.trim() || owner === ownerFilter.trim().toLowerCase();
      return statusMatch && sourceMatch && memoryModeMatch && ownerMatch;
    });

    filtered.sort((a, b) => {
      const aTs = Date.parse(a.last_activity || a.last_modified || "") || 0;
      const bTs = Date.parse(b.last_activity || b.last_modified || "") || 0;
      if (aTs !== bTs) return bTs - aTs;
      return String(b.session_id || "").localeCompare(String(a.session_id || ""));
    });

    return filtered;
  }, [sessions, statusFilter, sourceFilter, memoryModeFilter, ownerFilter]);
  const runningCount = useMemo(
    () =>
      sessions.filter((s) => {
        const status = (s.status || "").toLowerCase();
        const activeRuns = Number(s.active_runs || 0);
        return status === "running" || status === "active" || activeRuns > 0;
      }).length,
    [sessions],
  );
  const hasSelectedSession = Boolean(selected);

  return (
    <div
      className={
        isFull
          ? hasSelectedSession
            ? "px-4 pb-4 pt-0 text-sm space-y-3 lg:grid lg:grid-cols-[minmax(420px,560px)_1fr] lg:gap-3 lg:space-y-0"
            : "px-4 pb-4 pt-0 text-sm space-y-3"
          : "p-3 text-xs space-y-3"
      }
    >
      {isFull && (
        <div className={`${hasSelectedSession ? "lg:col-span-2" : ""} flex flex-wrap items-center justify-between gap-2`}>
          <div>
            {showBackToHome && (
              <Link
                href="/"
                className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
              >
                Back to Home
              </Link>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button onClick={cancelOutstandingRuns} className="text-[11px] px-2 py-1 rounded border border-orange-500/40 bg-orange-500/10 text-orange-500 hover:bg-orange-500/20 transition-all" disabled={runningCount === 0}>Kill Outstanding Runs ({runningCount})</button>
            <button onClick={fetchSessions} className="text-[11px] px-2 py-1 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all" disabled={loading}>{loading ? "..." : "↻ Refresh"}</button>
            {selected && !isVpSelected && (
              <button onClick={() => attachToChat(selected)} className="text-[11px] px-2 py-1 rounded border bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20" disabled={attaching}>
                {attaching ? "Attaching..." : "Open Chat"}
              </button>
            )}
          </div>
        </div>
      )}
      <div
        className={`border rounded bg-background/40 p-2 ${isFull
            ? hasSelectedSession
              ? "lg:row-span-5 lg:max-h-[78vh] lg:overflow-y-auto"
              : "lg:max-w-[860px]"
            : ""
          }`}
      >
        <div className="font-semibold mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span>{isFull ? "Session List" : "Sessions"}</span>
            {!isFull && (
              <>
                <button onClick={() => setShowList(!showList)} className="text-[10px] px-1.5 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all" title={showList ? "Collapse List" : "Expand List"}>
                  {showList ? "▼" : "▶"}
                </button>
                {!showList && (
                  <button onClick={() => setShowList(true)} className="ml-2 text-[10px] px-2 py-0.5 rounded border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20">
                    ← Back to List
                  </button>
                )}
              </>
            )}
          </div>
          {!isFull && (
            <div className="flex items-center gap-1">
              <button onClick={cancelOutstandingRuns} className="text-[10px] px-2 py-0.5 rounded border border-orange-500/40 bg-orange-500/10 text-orange-500 hover:bg-orange-500/20 transition-all" disabled={runningCount === 0}>Kill Outstanding Runs ({runningCount})</button>
              <button onClick={fetchSessions} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all" disabled={loading}>{loading ? "..." : "↻"}</button>
            </div>
          )}
        </div>
        {showList && (
          <>
            <div className="mb-2 grid grid-cols-2 md:grid-cols-4 gap-1">
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)} className="rounded border border-border/60 bg-card/40 px-1 py-1 text-[10px]">
                <option value="all">status: all</option>
                <option value="running">running</option>
                <option value="idle">idle</option>
                <option value="terminal">terminal</option>
              </select>
              <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value as typeof sourceFilter)} className="rounded border border-border/60 bg-card/40 px-1 py-1 text-[10px]">
                <option value="all">source: all</option>
                <option value="chat">chat</option>
                <option value="telegram">telegram</option>
                <option value="api">api</option>
                <option value="local">local</option>
              </select>
              <input
                value={ownerFilter}
                onChange={(e) => setOwnerFilter(e.target.value)}
                placeholder="owner"
                className="rounded border border-border/60 bg-card/40 px-1 py-1 text-[10px]"
              />
              <select value={memoryModeFilter} onChange={(e) => setMemoryModeFilter(e.target.value as typeof memoryModeFilter)} className="rounded border border-border/60 bg-card/40 px-1 py-1 text-[10px]">
                <option value="all">memory: all</option>
                <option value="off">off</option>
                <option value="memory_only">memory_only</option>
                <option value="direct_only">direct_only</option>
                <option value="all_scope">all</option>
              </select>
            </div>
            <div className={`space-y-1 overflow-y-auto scrollbar-thin ${isFull ? "max-h-[62vh]" : "max-h-40"}`}>
              {sessionsError && (
                <div className="text-[10px] text-amber-400 whitespace-pre-wrap">
                  {sessionsError}
                </div>
              )}
              {filteredSessions.length === 0 && <div className="text-muted-foreground">No sessions found</div>}
              {filteredSessions.map((s) => (
                <button key={s.session_id} onClick={() => { setSelected(s.session_id); if (!isFull) setShowList(false); }} className={`w-full text-left px-2 py-1 rounded border text-xs ${selected === s.session_id ? "border-primary text-primary" : "border-border/50 text-muted-foreground"}`}>
                  <div className="font-mono truncate">{s.session_id}</div>
                  <div className="flex justify-between"><span>{s.status}</span><span className="opacity-60">{s.last_activity?.slice(11, 19) ?? "--:--:--"}</span></div>
                  <div className="flex justify-between opacity-70">
                    <span>{s.source || s.channel || "local"}</span>
                    <span>{s.owner || "unknown"}</span>
                  </div>
                  <div className="opacity-60 text-[10px]">memory: {s.memory_mode || "direct_only"}</div>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* If list is hidden and we have a selection, show a mini header to re-expand */}
      {!showList && selected && !isFull && (
        <div className="flex items-center justify-between px-2 py-1 bg-background/40 border rounded text-[10px]">
          <span className="font-mono">{selected}</span>
          <button onClick={() => setShowList(true)} className="text-primary hover:underline">Show List</button>
        </div>
      )}

      {selected && (
        <>
          {isVpSelected && (
            <div className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-2 text-[10px] uppercase tracking-wider text-rose-300">
              VP Observer Mode: This lane is controlled by Simone. Session actions are view-only.
            </div>
          )}
          <div className="border rounded bg-background/40 p-2">
            <div className="font-semibold mb-2">Session Actions</div>
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => attachToChat(selected)} className="px-2 py-1 rounded border bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20" disabled={attaching}>{attaching ? "Attaching..." : isVpSelected ? "Open Observer Chat" : "Attach To Chat (Tail)"}</button>
              {!isVpSelected && (
                <>
                  <button onClick={() => cancelSession(selected)} className="px-2 py-1 rounded border bg-orange-500/10 text-orange-500 hover:bg-orange-500/20">Cancel Run</button>
                  <button onClick={() => archiveSession(selected)} className="px-2 py-1 rounded border bg-sky-500/10 text-sky-500 hover:bg-sky-500/20">Archive</button>
                  <button onClick={() => compactLogs(selected)} className="px-2 py-1 rounded border bg-blue-500/10 text-blue-500 hover:bg-blue-500/20">Compact Logs</button>
                  <button onClick={() => resetSession(selected)} className="px-2 py-1 rounded border bg-amber-500/10 text-amber-500 hover:bg-amber-500/20">Reset</button>
                  <button onClick={() => deleteSession(selected)} className="px-2 py-1 rounded border bg-red-500/10 text-red-500 hover:bg-red-500/20">Delete</button>
                </>
              )}
            </div>
            {isVpSelected && (
              <div className="mt-2 text-[10px] text-slate-400">
                Direct control actions are disabled for VP sessions. Use the primary Simone chat to request CODIE changes.
              </div>
            )}
          </div>
          <div className="border rounded bg-background/40 p-2">
            <div className="font-semibold mb-2 flex items-center justify-between">
              <span>run.log tail</span>
              <button
                type="button"
                onClick={() => setExpandLogTail((prev) => !prev)}
                className="text-[10px] px-1.5 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all"
              >
                {expandLogTail ? "Compact" : "Expand"}
              </button>
            </div>
            <pre className={`text-[10px] font-mono whitespace-pre-wrap overflow-y-auto scrollbar-thin bg-background/50 p-2 rounded border ${expandLogTail ? (isFull ? "max-h-[46vh]" : "max-h-80") : "max-h-40"}`}>{logTail || (isVpSelected ? "(empty lane log; no recent root VP lane output)" : "(empty)")}</pre>
          </div>
          <div className="border rounded bg-background/40 p-2 space-y-2">
            <div className="font-semibold">Delivery Workflow</div>
            <div className="text-[10px] text-slate-400">
              Choose how to handle this CODIE output: promote, continue iteration, or archive as draft.
            </div>
            {deliveryLoading && (
              <div className="text-[10px] text-slate-500">Loading thread state...</div>
            )}
            {selectedDecision && (
              <div className="rounded border border-cyan-700/40 bg-cyan-900/10 px-2 py-1 text-[10px] text-cyan-200">
                Current: <span className="font-semibold uppercase">{selectedDecision.decision || "pending"}</span>
                {" · "}
                {selectedDecision.updated_at ? new Date(selectedDecision.updated_at * 1000).toLocaleString() : "recent"}
                {selectedDecision.patch_version ? ` · v${selectedDecision.patch_version}` : ""}
              </div>
            )}
            <div className="flex gap-2 flex-wrap">
              <button
                type="button"
                onClick={() => selected && recordDeliveryDecision(selected, "promote")}
                disabled={!selected || isVpSelected || deliveryDecisionBusy}
                className="px-2 py-1 rounded border bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50"
              >
                Promote Now
              </button>
              <button
                type="button"
                onClick={() => selected && recordDeliveryDecision(selected, "iterate")}
                disabled={!selected || deliveryDecisionBusy}
                className="px-2 py-1 rounded border bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 disabled:opacity-50"
              >
                Open Iteration
              </button>
              <button
                type="button"
                onClick={() => selected && recordDeliveryDecision(selected, "archive")}
                disabled={!selected || isVpSelected || deliveryDecisionBusy}
                className="px-2 py-1 rounded border bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 disabled:opacity-50"
              >
                Archive Draft
              </button>
            </div>
            <textarea
              value={deliveryNoteDraft}
              onChange={(e) => setDeliveryNoteDraft(e.target.value)}
              placeholder="Optional notes (acceptance criteria, follow-up asks, risk notes)"
              className="w-full rounded border border-border/60 bg-card/40 px-2 py-1.5 text-[11px] min-h-16"
            />
            {isVpSelected && (
              <div className="text-[10px] text-rose-300">
                VP lane is observer-only. Use Simone chat to execute promote/archive actions.
              </div>
            )}
            {deliveryStatus && (
              <div className="text-[10px] text-emerald-300">{deliveryStatus}</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export function CalendarSection({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const isFull = variant === "full";
  const { schedulingPushState } = useOps();
  const [view, setView] = useState<"week" | "day">("week");
  const [sourceFilter, setSourceFilter] = useState<"all" | "cron" | "heartbeat">("all");
  const [anchorDate, setAnchorDate] = useState<Date>(new Date());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<CalendarEventItem[]>([]);
  const [alwaysRunning, setAlwaysRunning] = useState<CalendarEventItem[]>([]);
  const [stasisQueue, setStasisQueue] = useState<Array<{ event_id?: string; status?: string; created_at?: string; event?: CalendarEventItem }>>([]);
  const [tz, setTz] = useState<string>("America/Chicago");

  const attachToChat = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    const dashboardRoute =
      typeof window !== "undefined" && window.location.pathname.startsWith("/dashboard");
    if (dashboardRoute) {
      openOrFocusChatWindow({ sessionId, attachMode: "tail" });
      return;
    }
    const store = useAgentStore.getState();
    store.reset();
    store.setSessionAttachMode("tail");
    const ws = getWebSocket();
    ws.attachToSession(sessionId);
  }, []);

  const startOfWeek = (d: Date) => {
    const copy = new Date(d);
    const daysSinceSunday = copy.getDay();
    copy.setDate(copy.getDate() - daysSinceSunday);
    copy.setHours(0, 0, 0, 0);
    return copy;
  };
  const addDays = (d: Date, n: number) => {
    const copy = new Date(d);
    copy.setDate(copy.getDate() + n);
    return copy;
  };
  const range = useMemo(() => {
    if (view === "day") {
      const start = new Date(anchorDate);
      start.setHours(0, 0, 0, 0);
      const end = addDays(start, 1);
      return { start, end };
    }
    const start = startOfWeek(anchorDate);
    const end = addDays(start, 7);
    return { start, end };
  }, [anchorDate, view]);

  const fetchCalendar = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const browserTz = "America/Chicago";
      const params = new URLSearchParams({
        view,
        start: range.start.toISOString(),
        end: range.end.toISOString(),
        source: sourceFilter,
        timezone_name: browserTz,
      });
      const r = await fetch(`${API_BASE}/api/v1/ops/calendar/events?${params.toString()}`, { headers: buildHeaders() });
      if (!r.ok) {
        const msg = await r.text();
        throw new Error(msg || `Calendar load failed (${r.status})`);
      }
      const data = (await r.json()) as CalendarFeedResponse;
      setEvents(data.events || []);
      setAlwaysRunning(data.always_running || []);
      setStasisQueue((data.stasis_queue as Array<{ event_id?: string; status?: string; created_at?: string; event?: CalendarEventItem }>) || []);
      setTz(data.timezone || browserTz);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [range.end, range.start, sourceFilter, view]);

  useEffect(() => {
    fetchCalendar();
  }, [fetchCalendar]);

  useEffect(() => {
    if (schedulingPushState.seq <= 0) return;
    const timer = window.setTimeout(() => {
      fetchCalendar();
    }, 300);
    return () => window.clearTimeout(timer);
  }, [schedulingPushState.seq, fetchCalendar]);

  // Fallback polling when push stream is disconnected.
  useEffect(() => {
    if (schedulingPushState.status === "Connected") return;
    const timer = window.setInterval(() => {
      fetchCalendar();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [schedulingPushState.status, fetchCalendar]);

  const performAction = useCallback(async (event: CalendarEventItem, action: string) => {
    if (action === "request_change") {
      // Backward compatibility: older feeds may still emit this pseudo-action.
      return;
    }
    const payload: Record<string, unknown> = { action };
    if (action === "reschedule") {
      const requested = prompt("Reschedule for when? (e.g. 'in 30m' or ISO timestamp)");
      if (!requested || !requested.trim()) return;
      payload.run_at = requested.trim();
    }
    const r = await fetch(`${API_BASE}/api/v1/ops/calendar/events/${encodeURIComponent(event.event_id)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildHeaders() },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const msg = await r.text();
      throw new Error(msg || `Action failed (${r.status})`);
    }
    const data = await r.json();
    if (action === "open_logs" && data.path) {
      const href = String(data.path);
      const full = href.startsWith("http") ? href : `${API_BASE}${href.startsWith("/") ? "" : "/"}${href}`;
      window.open(full, "_blank", "noopener,noreferrer");
    }
    if (action === "open_session") {
      const sid = String(data.session_id || event.session_id || "");
      if (sid) await attachToChat(sid);
    }
    await fetchCalendar();
  }, [attachToChat, fetchCalendar]);

  const requestChange = useCallback(async (event: CalendarEventItem) => {
    const instruction = prompt("Describe the schedule change:");
    if (!instruction || !instruction.trim()) return;
    const propose = await fetch(`${API_BASE}/api/v1/ops/calendar/events/${encodeURIComponent(event.event_id)}/change-request`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildHeaders() },
      body: JSON.stringify({ instruction: instruction.trim() }),
    });
    if (!propose.ok) {
      const msg = await propose.text();
      throw new Error(msg || `Change request failed (${propose.status})`);
    }
    const proposalPayload = await propose.json();
    const proposal = proposalPayload.proposal;
    const warningText = Array.isArray(proposal?.warnings) && proposal.warnings.length > 0
      ? `\nWarnings:\n- ${proposal.warnings.join("\n- ")}`
      : "";
    const ok = confirm(
      `Apply this change?\n\n${proposal?.summary || "No summary"}\nConfidence: ${proposal?.confidence || "unknown"}${warningText}`,
    );
    const confirmResp = await fetch(`${API_BASE}/api/v1/ops/calendar/events/${encodeURIComponent(event.event_id)}/change-request/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildHeaders() },
      body: JSON.stringify({ proposal_id: proposal?.proposal_id, approve: ok }),
    });
    if (!confirmResp.ok) {
      const msg = await confirmResp.text();
      throw new Error(msg || `Confirm failed (${confirmResp.status})`);
    }
    await fetchCalendar();
  }, [fetchCalendar]);

  const weekDays = useMemo(() => {
    if (view === "day") return [new Date(range.start)];
    return Array.from({ length: 7 }, (_, i) => addDays(range.start, i));
  }, [range.start, view]);

  const { scheduledByDay, resultsByDay } = useMemo(() => {
    const sMap = new Map<string, CalendarEventItem[]>();
    const rMap = new Map<string, CalendarEventItem[]>();
    for (const day of weekDays) {
      sMap.set(day.toDateString(), []);
      rMap.set(day.toDateString(), []);
    }
    for (const event of events) {
      const key = new Date(event.scheduled_at_local).toDateString();
      const isResult = RESULT_STATUSES.has(event.status);
      const target = isResult ? rMap : sMap;
      if (!target.has(key)) target.set(key, []);
      target.get(key)!.push(event);
    }
    for (const [, bucket] of sMap) bucket.sort((a, b) => a.scheduled_at_epoch - b.scheduled_at_epoch);
    for (const [, bucket] of rMap) bucket.sort((a, b) => a.scheduled_at_epoch - b.scheduled_at_epoch);
    return { scheduledByDay: sMap, resultsByDay: rMap };
  }, [events, weekDays]);

  const hasAnyResults = useMemo(() => {
    for (const [, bucket] of resultsByDay) {
      if (bucket.length > 0) return true;
    }
    return false;
  }, [resultsByDay]);

  const statusBadge = (event: CalendarEventItem) => {
    // Heartbeat should read as low-stakes background activity (light blue),
    // not an error condition (red).
    if (event.source === "heartbeat") return "bg-sky-500/15 border-sky-400/40 text-sky-200";
    if (event.status === "missed") return "bg-amber-500/20 border-amber-500/50 text-amber-300";
    if (event.status === "failed") return "bg-rose-500/20 border-rose-500/50 text-rose-300";
    if (event.status === "success") return "bg-emerald-500/20 border-emerald-500/50 text-emerald-300";
    if (event.status === "disabled") return "bg-slate-500/20 border-slate-500/50 text-slate-300";
    return "bg-blue-500/20 border-blue-500/50 text-blue-200";
  };

  const shiftWindow = (days: number) => {
    const next = new Date(anchorDate);
    next.setDate(next.getDate() + days);
    setAnchorDate(next);
  };

  return (
    <div className={`${isFull ? "h-full p-4 text-sm space-y-4" : "p-3 text-xs space-y-3"}`}>
      <div className={`border rounded bg-background/40 space-y-2 ${isFull ? "p-3" : "p-2"}`}>
        <div className="font-semibold flex items-center justify-between gap-2">
          <span>Calendar</span>
          <div className="flex items-center gap-1">
            <button onClick={() => shiftWindow(view === "day" ? -1 : -7)} className="px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60">◀</button>
            <button onClick={() => setAnchorDate(new Date())} className="px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60">Today</button>
            <button onClick={() => shiftWindow(view === "day" ? 1 : 7)} className="px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60">▶</button>
            <button onClick={fetchCalendar} className="px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60">{loading ? "..." : "↻"}</button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={view} onChange={(e) => setView(e.target.value as typeof view)} className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[10px] flex-1 md:flex-none min-w-[60px]">
            <option value="week">week</option>
            <option value="day">day</option>
          </select>
          <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value as typeof sourceFilter)} className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[10px]">
            <option value="all">source: all</option>
            <option value="cron">chron</option>
            <option value="heartbeat">heartbeat</option>
          </select>
          <span className="text-[10px] text-muted-foreground">timezone: {tz}</span>
          <span className="text-[10px] text-muted-foreground">
            push: {schedulingPushState.status.toLowerCase()} (v{schedulingPushState.projection_version})
          </span>
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded bg-sky-400/80" />heartbeat</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded bg-blue-500/80" />scheduled</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded bg-emerald-500/80" />success</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded bg-rose-500/80" />failed</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded bg-amber-500/80" />missed</span>
          </div>
        </div>
        {error && <div className="text-[10px] text-rose-400">{error}</div>}
      </div>

      <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
        <div className="font-semibold mb-1">Always Running</div>
        {alwaysRunning.length === 0 && <div className="text-muted-foreground text-[10px]">No always-running entries</div>}
        <div className="flex flex-wrap gap-1">
          {alwaysRunning.map((item) => (
            <button
              key={item.event_id}
              onClick={() => performAction(item, "delete")}
              className="px-2 py-1 rounded border border-sky-400/40 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20"
              title="Disable heartbeat delivery for this session (calendar will stop showing it)."
            >
              Delete • {item.title}
            </button>
          ))}
        </div>
      </div>

      <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
        <div className="font-semibold mb-1">Missed Event Stasis Queue</div>
        {stasisQueue.length === 0 && <div className="text-muted-foreground text-[10px]">No pending missed events</div>}
        <div className="space-y-1">
          {stasisQueue.map((entry) => {
            const ev = entry.event;
            if (!ev) return null;
            return (
              <div key={entry.event_id || ev.event_id} className="rounded border border-amber-500/40 bg-amber-500/10 p-2">
                <div className="font-semibold text-amber-200">{ev.title}</div>
                <div className="text-[10px] text-amber-100/90">
                  missed at {new Date(ev.scheduled_at_local).toLocaleString()} • status: {entry.status || "pending"}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  <button onClick={() => performAction(ev, "approve_backfill_run")} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">Approve & Run</button>
                  <button onClick={() => performAction(ev, "reschedule")} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">Reschedule</button>
                  <button onClick={() => performAction(ev, "delete_missed")} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">Delete</button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Row 1: Scheduled / Upcoming */}
      <div className={`${isFull ? "block" : "hidden md:block"} space-y-1`}>
        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-1">Scheduled</div>
        <div className="grid grid-cols-7 gap-2">
          {weekDays.map((day) => {
            const key = day.toDateString();
            const bucket = scheduledByDay.get(key) || [];
            return (
              <div key={key} className="border rounded bg-background/40 p-2 min-h-[180px]">
                <div className="font-semibold text-[11px] mb-2">{day.toLocaleDateString(undefined, { weekday: "short", month: "numeric", day: "numeric" })}</div>
                <div className="space-y-1">
                  {bucket.length === 0 && <div className="text-[10px] text-muted-foreground">—</div>}
                  {bucket.map((event) => (
                    <div key={event.event_id} className={`rounded border px-2 py-1 ${statusBadge(event)}`}>
                      <div className="font-semibold truncate">{event.title}</div>
                      <div className="text-[10px] opacity-90">{new Date(event.scheduled_at_local).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} • {event.status}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {event.source === "heartbeat" ? (
                          <button
                            onClick={() => performAction(event, "delete")}
                            className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]"
                            title="Disable heartbeat delivery for this session (calendar will stop showing it)."
                          >
                            Delete
                          </button>
                        ) : (
                          <>
                            {(event.actions || []).map((action) => (
                              <button key={`${event.event_id}-${action}`} onClick={() => performAction(event, action)} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">
                                {action}
                              </button>
                            ))}
                            <button onClick={() => requestChange(event)} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">change</button>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Row 2: Results (completed / failed / missed) */}
      {hasAnyResults && (
        <div className={`${isFull ? "block" : "hidden md:block"} space-y-1`}>
          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-1">Results</div>
          <div className="grid grid-cols-7 gap-2">
            {weekDays.map((day) => {
              const key = day.toDateString();
              const bucket = resultsByDay.get(key) || [];
              return (
                <div key={`result-${key}`} className="border rounded bg-background/40 p-2 min-h-[80px]">
                  <div className="font-semibold text-[11px] mb-1 text-muted-foreground">{day.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })}</div>
                  <div className="space-y-1">
                    {bucket.length === 0 && <div className="text-[10px] text-muted-foreground">—</div>}
                    {bucket.map((event) => (
                      <div key={event.event_id} className={`rounded border px-2 py-1 ${statusBadge(event)} cursor-pointer hover:opacity-80`} onClick={() => event.session_id && attachToChat(event.session_id)} title={event.session_id ? `Open session ${event.session_id}` : undefined}>
                        <div className="font-semibold truncate">{event.title}</div>
                        <div className="text-[10px] opacity-90">{new Date(event.scheduled_at_local).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} • {event.status}</div>
                        {event.session_id && <div className="text-[9px] opacity-70 truncate mt-0.5">📎 {event.session_id.slice(0, 24)}…</div>}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className={`${isFull ? "hidden" : "md:hidden"} border rounded bg-background/40 p-2`}>
        <div className="font-semibold mb-2">{view === "day" ? "Day" : "Events"}</div>
        <div className="space-y-2 max-h-72 overflow-y-auto scrollbar-thin">
          {events.length === 0 && <div className="text-[10px] text-muted-foreground">No events</div>}
          {events.map((event) => (
            <div key={event.event_id} className={`rounded border px-2 py-1 ${statusBadge(event)}`}>
              <div className="font-semibold">{event.title}</div>
              <div className="text-[10px] opacity-90">
                {new Date(event.scheduled_at_local).toLocaleString()} • {event.status}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {event.source === "heartbeat" ? (
                  <button
                    onClick={() => performAction(event, "delete")}
                    className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]"
                    title="Disable heartbeat delivery for this session (calendar will stop showing it)."
                  >
                    Delete
                  </button>
                ) : (
                  <>
                    {(event.actions || []).map((action) => (
                      <button key={`${event.event_id}-${action}`} onClick={() => performAction(event, action)} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">
                        {action}
                      </button>
                    ))}
                    <button onClick={() => requestChange(event)} className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 hover:bg-background/60 text-[9px]">change</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SkillsSection({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const { skills, fetchSkills } = useOps();
  const [selectedSkill, setSelectedSkill] = useState<SkillStatus | null>(null);
  const [docContent, setDocContent] = useState<string | null>(null);
  const [loadingDoc, setLoadingDoc] = useState(false);
  const isFull = variant === "full";
  const [showList, setShowList] = useState(isFull);
  const [searchTerm, setSearchTerm] = useState("");

  useEffect(() => {
    if (skills.length > 0 && !selectedSkill) {
      setSelectedSkill(skills[0]);
    }
  }, [skills, selectedSkill]);

  useEffect(() => {
    async function loadDoc() {
      if (!selectedSkill) return;
      setLoadingDoc(true);
      try {
        const r = await fetch(`${API_BASE}/api/v1/ops/skills/${encodeURIComponent(selectedSkill.name)}/doc`, { headers: buildHeaders() });
        if (r.ok) {
          const d = await r.json();
          setDocContent(d.content);
        } else {
          setDocContent("Documentation not found.");
        }
      } catch (e) {
        setDocContent("Error loading documentation.");
      } finally {
        setLoadingDoc(false);
      }
    }
    loadDoc();
  }, [selectedSkill]);

  const filteredSkills = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((skill) => skill.name.toLowerCase().includes(q));
  }, [searchTerm, skills]);

  return (
    <div className={`flex flex-col h-full ${isFull ? "min-h-0" : "min-h-[500px]"}`}>
      <div className="p-3 border-b border-border/40 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold text-sm">Skills Management</h2>
          <button onClick={() => setShowList(!showList)} className="text-[10px] px-1.5 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all" title={showList ? "Collapse List" : "Expand List"}>
            {showList ? "▼" : "▶"}
          </button>
          {/* Mobile-friendly Back button when list is collapsed */}
          {!showList && (
            <button onClick={() => setShowList(true)} className="ml-2 text-[10px] px-2 py-0.5 rounded border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20">
              ← Back to List
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search skills"
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[11px]"
          />
          <button onClick={fetchSkills} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all">↻ Refresh catalog</button>
        </div>
      </div>
      <div className="flex-1 flex flex-col md:flex-row overflow-hidden">
        {/* Left: Skill List */}
        {showList && (
          <div className={`${isFull ? "w-full md:w-[360px] h-[40%] md:h-auto" : "w-full md:w-1/3 h-1/3 md:h-auto"} border-b md:border-b-0 md:border-r border-border/40 overflow-y-auto scrollbar-thin p-2 space-y-1 bg-background/20 shrink-0`}>
            {filteredSkills.length === 0 && <div className="text-muted-foreground p-2">No skills found</div>}
            {filteredSkills.map((s) => (
              <button
                key={s.name}
                onClick={() => { setSelectedSkill(s); setShowList(false); }}
                className={`w-full text-left p-2 rounded-lg border transition-all ${selectedSkill?.name === s.name
                  ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-200"
                  : "border-transparent hover:bg-slate-800/40 text-slate-400"
                  }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium truncate">{s.name}</span>
                  <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${s.enabled && s.available ? "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20" : "bg-amber-500/10 text-amber-500 border border-amber-500/20"}`}>
                    {s.enabled && s.available ? "active" : "disabled"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4 bg-slate-950/30">
          {loadingDoc ? (
            <div className="h-full flex items-center justify-center text-slate-500 text-xs italic">Loading documentation...</div>
          ) : docContent ? (
            <article className="prose prose-invert prose-sm max-w-none">
              <div className="mb-4 flex items-center justify-between border-b border-slate-800 pb-2">
                <h3 className="text-lg font-bold text-slate-100 m-0">{selectedSkill?.name} Documentation</h3>
                <span className="text-[10px] text-slate-500 font-mono">SKILL.md</span>
              </div>
              <div className="markdown-content">
                {(() => {
                  try {
                    const content = docContent || "";
                    let finalContent = content;

                    // formatting: if frontmatter exists, wrap it in a yaml code block for display
                    if (content.startsWith("---")) {
                      const match = content.match(/^---([\s\S]*?)---\s*(.*)$/s);
                      if (match) {
                        const frontmatter = match[1];
                        const body = match[2];
                        finalContent = "```yaml\n" + frontmatter + "\n```\n\n" + body;
                      }
                    }

                    return (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          code: ({ node, className, children, ...props }: any) => {
                            const match = /language-(\w+)/.exec(className || '')
                            return match ? (
                              <div className="bg-slate-900 rounded p-2 my-2 border border-slate-700 overflow-x-auto">
                                <code className={className} {...props}>
                                  {children}
                                </code>
                              </div>
                            ) : (
                              <code className="bg-slate-800 px-1 py-0.5 rounded text-cyan-300 font-mono text-sm" {...props}>
                                {children}
                              </code>
                            )
                          },
                          pre: ({ node, ...props }: any) => <div {...props} />, // let code component handle styling
                          table: ({ node, ...props }: any) => <table className="border-collapse border border-slate-700 w-full mb-4" {...props} />,
                          th: ({ node, ...props }: any) => <th className="border border-slate-700 p-2 bg-slate-800 text-slate-200 text-left" {...props} />,
                          td: ({ node, ...props }: any) => <td className="border border-slate-700 p-2 text-slate-300" {...props} />,
                          a: ({ node, ...props }: any) => <a className="text-cyan-400 hover:underline" target="_blank" rel="noopener noreferrer" {...props} />
                        }}
                      >
                        {finalContent}
                      </ReactMarkdown>
                    );
                  } catch (e) {
                    return <pre className="whitespace-pre-wrap">{docContent}</pre>;
                  }
                })()}
              </div>
            </article>
          ) : (
            <div className="h-full flex items-center justify-center text-slate-500 text-xs italic">Select a skill to view details.</div>
          )}
        </div>
      </div>
    </div>
  );
}

export function ChannelsSection({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const isFull = variant === "full";
  const { channels, fetchChannels, probeChannel } = useOps();
  return (
    <div className={`${isFull ? "p-4 text-sm" : "p-3 text-xs"}`}>
      <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
        <div className="font-semibold mb-2 flex items-center justify-between">
          <span>Channels</span>
          <button onClick={fetchChannels} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all">↻</button>
        </div>
        <div className={`space-y-2 overflow-y-auto scrollbar-thin ${isFull ? "max-h-[65vh]" : "max-h-48"}`}>
          {channels.length === 0 && <div className="text-muted-foreground">No channels found</div>}
          {channels.map((ch) => (
            <div key={ch.id} className="border rounded px-2 py-1 bg-background/50">
              <div className="flex items-center justify-between">
                <span className="font-mono">{ch.id}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${ch.enabled ? "bg-emerald-500/10 text-emerald-500" : "bg-amber-500/10 text-amber-500"}`}>{ch.enabled ? "enabled" : "disabled"}</span>
              </div>
              <div className="text-[10px] text-muted-foreground">{ch.note}</div>
              <div className="flex items-center justify-between mt-1">
                <span className="text-[10px] text-muted-foreground">probe: {ch.probe?.status ?? "n/a"}</span>
                <button type="button" className="text-[10px] px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors" onClick={() => probeChannel(ch.id)}>Probe</button>
              </div>
              {ch.probe?.detail && <div className="text-[10px] text-muted-foreground mt-1">{ch.probe.detail}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ApprovalsSection({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const isFull = variant === "full";
  const { approvals, fetchApprovals, updateApproval } = useOps();
  return (
    <div className={`${isFull ? "p-4 text-sm" : "p-3 text-xs"}`}>
      <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
        <div className="font-semibold mb-2 flex items-center justify-between">
          <span>Approvals</span>
          <button onClick={fetchApprovals} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all">↻</button>
        </div>
        <div className={`space-y-2 overflow-y-auto scrollbar-thin ${isFull ? "max-h-[65vh]" : "max-h-48"}`}>
          {approvals.length === 0 && <div className="text-muted-foreground">No approvals</div>}
          {approvals.map((a) => (
            <div key={a.approval_id} className="border rounded px-2 py-1 bg-background/50">
              <div className="flex items-center justify-between">
                <span className="font-mono text-[11px] truncate">{a.approval_id}</span>
                <span className="text-[10px] text-muted-foreground">{a.status ?? "unknown"}</span>
              </div>
              {a.summary && <div className="text-[10px] text-muted-foreground">{a.summary}</div>}
              {a.status === "pending" && (
                <div className="flex gap-2 mt-2">
                  <button type="button" className="text-[10px] px-2 py-1 rounded border bg-emerald-500/10 text-emerald-500" onClick={() => updateApproval(a.approval_id, "approved")}>Approve</button>
                  <button type="button" className="text-[10px] px-2 py-1 rounded border bg-amber-500/10 text-amber-500" onClick={() => updateApproval(a.approval_id, "rejected")}>Reject</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SystemEventsSection({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const isFull = variant === "full";
  const { mergedEvents } = useOps();
  const [eventFilter, setEventFilter] = useState("");
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);

  const filteredEvents = useMemo(() => {
    const q = eventFilter.trim().toLowerCase();
    const rows = [...mergedEvents].sort((a, b) => b.timestamp - a.timestamp);
    if (!q) return rows;
    return rows.filter((ev) => ev.event_type.toLowerCase().includes(q));
  }, [eventFilter, mergedEvents]);

  const selectedEvent = useMemo(
    () => filteredEvents.find((ev) => ev.id === selectedEventId) ?? null,
    [filteredEvents, selectedEventId],
  );

  return (
    <div className={`${isFull ? "p-4 text-sm" : "p-3 text-xs"}`}>
      <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
        <div className="mb-2 flex items-center justify-between">
          <div className="font-semibold">System Events</div>
          <input
            value={eventFilter}
            onChange={(e) => setEventFilter(e.target.value)}
            placeholder="Filter event type"
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[11px]"
          />
        </div>
        <div className={`${isFull ? "grid gap-3 lg:grid-cols-[1.1fr_0.9fr]" : ""}`}>
          <div className={`space-y-1 overflow-y-auto scrollbar-thin ${isFull ? "max-h-[60vh]" : "max-h-48"}`}>
            {filteredEvents.length === 0 && <div className="text-muted-foreground">No system events</div>}
            {filteredEvents.map((ev) => (
              <button
                key={ev.id}
                type="button"
                onClick={() => setSelectedEventId(ev.id)}
                className={`w-full border rounded px-2 py-1 bg-background/50 text-left ${selectedEventId === ev.id ? "border-cyan-500/60" : "border-border/50"}`}
              >
                <div className="flex justify-between text-[10px] text-muted-foreground"><span>{ev.event_type}</span><span>{ev.created_at?.slice(11, 19) ?? "--:--:--"}</span></div>
                <div className="font-mono text-[11px] truncate">{Object.keys(ev.payload || {}).join(", ") || "(no payload)"}</div>
              </button>
            ))}
          </div>
          {isFull && (
            <div className="rounded border border-border/60 bg-background/50 p-2 min-h-[180px]">
              <div className="mb-2 text-[11px] uppercase tracking-wider text-slate-400">Payload</div>
              {selectedEvent ? (
                <pre className="text-[11px] whitespace-pre-wrap font-mono max-h-[55vh] overflow-auto">{JSON.stringify(selectedEvent.payload || {}, null, 2)}</pre>
              ) : (
                <div className="text-slate-500 text-[11px]">Select an event to inspect payload.</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function OpsConfigSection({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const isFull = variant === "full";
  const {
    opsConfigText,
    setOpsConfigText,
    opsConfigStatus,
    opsConfigError,
    opsConfigSaving,
    loadOpsConfig,
    saveOpsConfig,
    remoteSyncEnabled,
    remoteSyncStatus,
    remoteSyncError,
    remoteSyncSaving,
    loadRemoteSync,
    setRemoteSync,
    opsSchemaText,
    opsSchemaStatus,
  } = useOps();
  const [purgeRemoteSaving, setPurgeRemoteSaving] = useState(false);

  const purgeRemoteData = useCallback(async () => {
    if (!confirm("⚠️ DANGER: This will delete ALL session workspaces and artifacts on the specific remote VPS.\n\nRunning sessions may fail. Local files are NOT affected.\n\nAre you sure you want to PURGE ALL REMOTE DATA?")) {
      return;
    }
    try {
      setPurgeRemoteSaving(true);
      const r = await fetch(`${API_BASE}/api/v1/ops/workspaces/purge?confirm=true`, {
        method: "POST",
        headers: buildHeaders(),
      });
      if (!r.ok) {
        const msg = await r.text();
        throw new Error(msg || `Purge failed (${r.status})`);
      }
      const data = await r.json();
      alert(`Purge Complete!\n\nDeleted Workspaces: ${data.deleted_workspaces}\nDeleted Artifact Items: ${data.deleted_artifacts_items}\n\n${data.errors.length > 0 ? "Errors:\n" + data.errors.join("\n") : "No errors."}`);
    } catch (e) {
      alert(`Purge failed: ${(e as Error).message}`);
    } finally {
      setPurgeRemoteSaving(false);
    }
  }, []);

  return (
    <div className={`${isFull ? "p-4 text-sm space-y-4" : "p-3 text-xs space-y-3"}`}>
      <div className={`${isFull ? "grid gap-4 xl:grid-cols-2" : "space-y-3"}`}>
        <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
          <div className="flex items-center justify-between mb-2">
            <div className="font-semibold">Remote To Local Debug Sync</div>
            <div className="text-[10px] text-muted-foreground">{remoteSyncStatus}</div>
          </div>
          <div className="text-[11px] text-muted-foreground mb-2">
            Controls whether local debug mirror jobs should run when configured to respect the remote toggle.
            Default is OFF when not set.
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={`text-xs px-2 py-1 rounded border transition-colors disabled:opacity-50 ${remoteSyncEnabled ? "bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20" : "bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"}`}
              onClick={() => setRemoteSync(!remoteSyncEnabled)}
              disabled={remoteSyncSaving}
            >
              {remoteSyncSaving ? "Saving..." : remoteSyncEnabled ? "Sync ON" : "Sync OFF"}
            </button>
            <button
              type="button"
              className="text-xs px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors"
              onClick={loadRemoteSync}
              disabled={remoteSyncSaving}
            >
              Refresh
            </button>
          </div>
          {remoteSyncError && <div className="text-[10px] text-red-500 mt-2">{remoteSyncError}</div>}

          <div className="mt-4 pt-3 border-t border-border/40">
            <div className="font-semibold text-rose-400 mb-1">Danger Zone</div>
            <div className="text-[10px] text-muted-foreground mb-2">
              Permanently delete all session workspaces and artifacts on the remote server.
              This does not affect your local files.
            </div>
            <button
              type="button"
              className="text-xs px-2 py-1 rounded border border-rose-500/50 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20 transition-colors disabled:opacity-50"
              onClick={purgeRemoteData}
              disabled={remoteSyncSaving || purgeRemoteSaving}
            >
              {purgeRemoteSaving ? "Purging..." : "Purge All Remote Data"}
            </button>
          </div>
        </div>
        <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
          <div className="flex items-center justify-between mb-2">
            <div className="font-semibold">Ops config (ops_config.json)</div>
            <div className="text-[10px] text-muted-foreground">{opsConfigStatus}</div>
          </div>
          <textarea className="w-full min-h-[140px] text-[11px] font-mono p-2 rounded border bg-background/60" value={opsConfigText} onChange={(e) => setOpsConfigText(e.target.value)} />
          {opsConfigError && <div className="text-[10px] text-red-500 mt-2">{opsConfigError}</div>}
          <div className="flex gap-2 mt-2">
            <button type="button" className="text-xs px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors" onClick={loadOpsConfig}>Reload</button>
            <button type="button" className="text-xs px-2 py-1 rounded border bg-primary/20 text-primary hover:bg-primary/30 transition-colors disabled:opacity-50" onClick={saveOpsConfig} disabled={opsConfigSaving}>{opsConfigSaving ? "Saving..." : "Save"}</button>
          </div>
        </div>
        <div className={`border rounded bg-background/40 ${isFull ? "p-3" : "p-2"}`}>
          <div className="flex items-center justify-between mb-2">
            <div className="font-semibold">Ops config schema</div>
            <div className="text-[10px] text-muted-foreground">{opsSchemaStatus}</div>
          </div>
          <textarea className="w-full min-h-[100px] text-[11px] font-mono p-2 rounded border bg-background/60" value={opsSchemaText} readOnly />
        </div>
      </div>
    </div>
  );
}

export function SessionContinuityWidget({ variant = "compact" }: { variant?: SectionVariant } = {}) {
  const isFull = variant === "full";
  const { continuityState, fetchSessionContinuityMetrics } = useOps();
  const [isCollapsed, setIsCollapsed] = useState(false);
  const metrics = continuityState.metrics || {};
  const windowMetrics = metrics.window || {};

  const resumeRate =
    typeof windowMetrics.resume_success_rate === "number"
      ? `${(windowMetrics.resume_success_rate * 100).toFixed(1)}%`
      : "--";
  const attachRate =
    typeof windowMetrics.attach_success_rate === "number"
      ? `${(windowMetrics.attach_success_rate * 100).toFixed(1)}%`
      : "--";
  const alerts = metrics.alerts || [];
  const windowSeconds = Number(metrics.window_seconds || 0);
  const windowLabel =
    windowSeconds > 0
      ? `${Math.max(1, Math.round(windowSeconds / 60))}m`
      : "--";
  const transportStatus = metrics.transport_status || "--";
  const runtimeStatus = metrics.runtime_status || "--";

  return (
    <div className={isFull ? "rounded-xl border border-slate-800 bg-slate-900/70" : `flex flex-col border-t border-border/40 transition-all duration-300 ${isCollapsed ? "h-10 shrink-0 overflow-hidden" : ""}`}>
      <div className="p-3 bg-card/30 border-b border-border/40 cursor-pointer hover:bg-card/40 flex items-center justify-between" onClick={() => !isFull && setIsCollapsed(!isCollapsed)}>
        <h2 className="text-[10px] font-bold text-muted-foreground/80 uppercase tracking-widest flex items-center gap-2">
          <span className="text-primary/60">📈</span> Continuity
          <span className="text-[9px] text-muted-foreground/60 font-normal font-mono">({continuityState.status})</span>
        </h2>
        {!isFull && <span className={`text-[9px] text-primary/60 transition-transform duration-200 ${isCollapsed ? "rotate-180" : ""}`}>▼</span>}
      </div>
      {(isFull || !isCollapsed) && (
        <div className="p-3 text-xs space-y-1">
          <div className="flex items-center justify-between mb-2">
            <span className="text-muted-foreground">Session continuity metrics (rolling {windowLabel})</span>
            <button
              type="button"
              className="text-[10px] px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors"
              onClick={(e) => {
                e.stopPropagation();
                fetchSessionContinuityMetrics();
              }}
            >
              Refresh
            </button>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Runtime status</span>
            <span className={`font-mono text-[11px] ${runtimeStatus === "degraded" ? "text-rose-500" : "text-emerald-500"}`}>
              {runtimeStatus}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Transport status</span>
            <span className={`font-mono text-[11px] ${transportStatus === "degraded" ? "text-amber-500" : "text-emerald-500"}`}>
              {transportStatus}
            </span>
          </div>
          <div className="flex justify-between"><span className="text-muted-foreground">Resume success</span><span className="font-mono text-[11px]">{resumeRate}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Attach success</span><span className="font-mono text-[11px]">{attachRate}</span></div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Window resume fail</span>
            <span className="font-mono text-[11px]">{windowMetrics.resume_failures ?? 0}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Window attach fail</span>
            <span className="font-mono text-[11px]">{windowMetrics.ws_attach_failures ?? 0}</span>
          </div>
          <div className="flex justify-between"><span className="text-muted-foreground">Dupes prevented (lifetime)</span><span className="font-mono text-[11px]">{metrics.duplicate_turn_prevention_count ?? 0}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Sessions created (lifetime)</span><span className="font-mono text-[11px]">{metrics.sessions_created ?? 0}</span></div>
          {alerts.length > 0 && (
            <div className="text-[10px] text-amber-500 space-y-1 pt-1">
              {alerts.slice(0, 4).map((alert, idx) => (
                <div key={`${alert.code || "alert"}-${idx}`}>{alert.message || alert.code || "continuity alert"}{alert.scope ? ` (${alert.scope})` : ""}</div>
              ))}
            </div>
          )}
          {continuityState.updated_at && (
            <div className="text-[10px] text-muted-foreground/70 pt-1">
              Updated: {new Date(continuityState.updated_at).toLocaleTimeString()}
            </div>
          )}
          {continuityState.error && (
            <div className="text-[10px] text-amber-500">{continuityState.error}</div>
          )}
        </div>
      )}
    </div>
  );
}

export function HeartbeatWidget() {
  const { heartbeatState, selected } = useOps();
  const currentChatSessionId = useAgentStore((s) => s.currentSession?.session_id ?? null);
  const heartbeatSessionId = currentChatSessionId || selected;
  const [isCollapsed, setIsCollapsed] = useState(false);
  return (
    <div className={`flex flex-col border-t border-border/40 transition-all duration-300 ${isCollapsed ? "h-10 shrink-0 overflow-hidden" : ""}`}>
      <div className="p-3 bg-card/30 border-b border-border/40 cursor-pointer hover:bg-card/40 flex items-center justify-between" onClick={() => setIsCollapsed(!isCollapsed)}>
        <h2 className="text-[10px] font-bold text-muted-foreground/80 uppercase tracking-widest flex items-center gap-2">
          <span className="text-primary/60">💓</span> Heartbeat
          <span className="text-[9px] text-muted-foreground/60 font-normal font-mono">({heartbeatState.status})</span>
        </h2>
        <span className={`text-[9px] text-primary/60 transition-transform duration-200 ${isCollapsed ? "rotate-180" : ""}`}>▼</span>
      </div>
      {!isCollapsed && (
        <div className="p-3 text-xs space-y-1">
          {!heartbeatSessionId && <div className="text-muted-foreground">Select a session to view heartbeat.</div>}
          {heartbeatSessionId && (
            <>
              <div className="flex justify-between"><span className="text-muted-foreground">Session</span><span className="font-mono text-[11px] truncate max-w-[180px]" title={heartbeatSessionId}>{heartbeatSessionId}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Last run</span><span className="font-mono text-[11px]">{typeof heartbeatState.last_run === "number" ? new Date(heartbeatState.last_run * 1000).toLocaleString() : heartbeatState.last_run ?? "--"}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Running</span><span className="font-mono text-[11px]">{heartbeatState.busy ? "yes" : "no"}</span></div>
              {(() => {
                const raw = heartbeatState.last_summary_raw;
                if (!raw || typeof raw !== "object") return null;
                const summary = raw as { sent?: boolean; suppressed_reason?: string };
                return (<>
                  <div className="flex justify-between"><span className="text-muted-foreground">Delivered</span><span className="font-mono text-[11px]">{summary.sent ? "yes" : "no"}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Suppressed</span><span className="font-mono text-[11px]">{summary.suppressed_reason ?? "--"}</span></div>
                </>);
              })()}
              <div className="text-muted-foreground">Last summary</div>
              {heartbeatState.skip_marker && (
                <div className="text-[10px] text-amber-400">{heartbeatState.skip_marker}</div>
              )}
              <div className="text-[11px] font-mono whitespace-pre-wrap max-h-24 overflow-y-auto scrollbar-thin">{heartbeatState.last_summary_text ?? "(none)"}</div>
              {(() => {
                const raw = heartbeatState.last_summary_raw;
                if (!raw || typeof raw !== "object") return null;
                const artifacts = (raw as { artifacts?: { writes?: string[]; work_products?: string[] } }).artifacts;
                const writes = artifacts?.writes; const wp = artifacts?.work_products;
                if ((!writes || writes.length === 0) && (!wp || wp.length === 0)) return null;
                return (
                  <div className="space-y-1">
                    <div className="text-muted-foreground">Artifacts</div>
                    {writes && writes.length > 0 && <div className="text-[10px] font-mono whitespace-pre-wrap">{"writes:\n" + writes.slice(-5).join("\n")}</div>}
                    {wp && wp.length > 0 && <div className="text-[10px] font-mono whitespace-pre-wrap">{"work_products:\n" + wp.slice(-5).join("\n")}</div>}
                  </div>
                );
              })()}
              {heartbeatState.error && <div className="text-[10px] text-amber-500">{heartbeatState.error}</div>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
