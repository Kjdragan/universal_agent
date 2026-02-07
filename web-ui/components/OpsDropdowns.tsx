"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAgentStore } from "@/lib/store";

const API_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8002";
const OPS_TOKEN = process.env.NEXT_PUBLIC_UA_OPS_TOKEN;

type SessionSummary = { session_id: string; status: string; last_activity?: string; workspace_dir?: string };
type SkillStatus = { name: string; enabled: boolean; available: boolean; unavailable_reason?: string | null };
type SystemEventItem = { id: string; event_type: string; payload: Record<string, unknown>; created_at?: string; session_id?: string; timestamp: number };
type ChannelStatus = { id: string; label: string; enabled: boolean; configured: boolean; note?: string; probe?: { status?: string; checked_at?: string; http_status?: number; detail?: string } };
type ApprovalRecord = { approval_id: string; status?: string; summary?: string; requested_by?: string; created_at?: number; updated_at?: number; metadata?: Record<string, unknown> };
type HeartbeatState = { status: string; busy?: boolean; last_run?: number | string; last_summary_raw?: unknown; last_summary_text?: string; error?: string };

function buildHeaders(): Record<string, string> {
  const h: Record<string, string> = {};
  if (OPS_TOKEN) h["X-UA-OPS-TOKEN"] = OPS_TOKEN;
  return h;
}

function safeJsonParse(v: string): { ok: true; data: Record<string, unknown> } | { ok: false; error: string } {
  try {
    const p = JSON.parse(v);
    if (p && typeof p === "object" && !Array.isArray(p)) return { ok: true, data: p };
    return { ok: false, error: "Config must be a JSON object" };
  } catch (e) { return { ok: false, error: (e as Error).message }; }
}

// ---- Context ----

type OpsCtx = {
  sessions: SessionSummary[]; skills: SkillStatus[]; channels: ChannelStatus[]; approvals: ApprovalRecord[];
  selected: string | null; setSelected: (id: string | null) => void;
  logTail: string; loading: boolean; heartbeatState: HeartbeatState; mergedEvents: SystemEventItem[];
  fetchSessions: () => Promise<void>; fetchSkills: () => Promise<void>; fetchChannels: () => Promise<void>;
  fetchApprovals: () => Promise<void>; probeChannel: (id: string) => Promise<void>;
  updateApproval: (id: string, status: string) => Promise<void>; fetchLogs: (id: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>; resetSession: (id: string) => Promise<void>; compactLogs: (id: string) => Promise<void>;
  opsConfigText: string; setOpsConfigText: (t: string) => void; opsConfigStatus: string;
  opsConfigError: string | null; opsConfigSaving: boolean;
  loadOpsConfig: () => Promise<void>; saveOpsConfig: () => Promise<void>;
  opsSchemaText: string; opsSchemaStatus: string;
  refreshAll: () => void;
};

const OpsContext = createContext<OpsCtx | null>(null);
function useOps() { const c = useContext(OpsContext); if (!c) throw new Error("useOps requires OpsProvider"); return c; }

export function OpsProvider({ children }: { children: React.ReactNode }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
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
  const [opsSchemaText, setOpsSchemaText] = useState("{}");
  const [opsSchemaStatus, setOpsSchemaStatus] = useState("Not loaded");
  const [heartbeatState, setHeartbeatState] = useState<HeartbeatState>({ status: "Not loaded" });

  const mergedEvents = useMemo(() => {
    const m = new Map<string, SystemEventItem>();
    for (const e of systemEvents) { if (e.id) m.set(e.id, e); }
    return Array.from(m.values()).sort((a, b) => a.timestamp - b.timestamp).slice(-200);
  }, [systemEvents]);
  useEffect(() => { sysEvRef.current = systemEvents; }, [systemEvents]);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions`, { headers: buildHeaders() });
      const d = await r.json(); const ns = d.sessions || [];
      setSessions(ns);
      if (ns.length > 0) setSelected((p) => p ?? ns[0].session_id);
    } catch (e) { console.error("Ops sessions fetch failed", e); }
    finally { setLoading(false); }
  }, []);

  const fetchSkills = useCallback(async () => {
    try { const r = await fetch(`${API_BASE}/api/v1/ops/skills`, { headers: buildHeaders() }); const d = await r.json(); setSkills(d.skills || []); }
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
    try { const r = await fetch(`${API_BASE}/api/v1/ops/logs/tail?session_id=${encodeURIComponent(sid)}&limit=120`, { headers: buildHeaders() }); const d = await r.json(); setLogTail((d.lines || []).join("\n")); }
    catch (e) { console.error("Ops logs fetch failed", e); }
  }, []);

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
      let txt: string | undefined;
      if (typeof raw === "string" || raw == null) txt = raw ?? undefined;
      else if (typeof raw === "object") txt = (raw as { text?: string }).text ?? JSON.stringify(raw, null, 2);
      else txt = String(raw);
      setHeartbeatState({ status: "OK", busy: Boolean(d.busy), last_run: d.last_run, last_summary_raw: raw, last_summary_text: txt });
    } catch (e) { setHeartbeatState({ status: "Error", error: (e as Error).message }); }
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

  const loadOpsSchema = useCallback(async () => {
    setOpsSchemaStatus("Loading...");
    try { const r = await fetch(`${API_BASE}/api/v1/ops/config/schema`, { headers: buildHeaders() }); if (!r.ok) throw new Error(`Schema load failed (${r.status})`); const d = await r.json(); setOpsSchemaText(JSON.stringify(d.schema || {}, null, 2)); setOpsSchemaStatus("Loaded"); }
    catch (e) { console.error("Ops schema fetch failed", e); setOpsSchemaStatus("Load failed"); }
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

  const refreshAll = useCallback(() => {
    fetchSessions(); fetchSkills(); fetchChannels(); fetchApprovals(); loadOpsConfig(); loadOpsSchema();
    if (selected) { fetchLogs(selected); fetchSystemEvents(selected); fetchHeartbeat(selected); }
  }, [fetchSessions, fetchSkills, fetchChannels, fetchApprovals, loadOpsConfig, loadOpsSchema, selected, fetchLogs, fetchSystemEvents, fetchHeartbeat]);

  useEffect(() => { fetchSessions(); fetchSkills(); fetchChannels(); fetchApprovals(); loadOpsConfig(); loadOpsSchema(); }, [fetchApprovals, fetchChannels, fetchSessions, fetchSkills, loadOpsConfig, loadOpsSchema]);
  useEffect(() => { if (selected) { fetchLogs(selected); fetchSystemEvents(selected); fetchHeartbeat(selected); } }, [selected, fetchLogs, fetchSystemEvents, fetchHeartbeat]);

  const val: OpsCtx = useMemo(() => ({
    sessions, skills, channels, approvals, selected, setSelected, logTail, loading, heartbeatState, mergedEvents,
    fetchSessions, fetchSkills, fetchChannels, fetchApprovals, probeChannel, updateApproval, fetchLogs,
    deleteSession, resetSession, compactLogs, opsConfigText, setOpsConfigText, opsConfigStatus, opsConfigError,
    opsConfigSaving, loadOpsConfig, saveOpsConfig, opsSchemaText, opsSchemaStatus, refreshAll,
  }), [sessions, skills, channels, approvals, selected, logTail, loading, heartbeatState, mergedEvents,
    fetchSessions, fetchSkills, fetchChannels, fetchApprovals, probeChannel, updateApproval, fetchLogs,
    deleteSession, resetSession, compactLogs, opsConfigText, opsConfigStatus, opsConfigError,
    opsConfigSaving, loadOpsConfig, saveOpsConfig, opsSchemaText, opsSchemaStatus, refreshAll]);

  return <OpsContext.Provider value={val}>{children}</OpsContext.Provider>;
}

// ---- Section Components ----

export function SessionsSection() {
  const { sessions, selected, setSelected, loading, logTail, fetchSessions, fetchLogs, deleteSession, resetSession, compactLogs } = useOps();
  return (
    <div className="p-3 text-xs space-y-3">
      <div className="border rounded bg-background/40 p-2">
        <div className="font-semibold mb-2 flex items-center justify-between">
          <span>Sessions</span>
          <button onClick={fetchSessions} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all" disabled={loading}>{loading ? "..." : "↻"}</button>
        </div>
        <div className="space-y-1 max-h-40 overflow-y-auto scrollbar-thin">
          {sessions.length === 0 && <div className="text-muted-foreground">No sessions found</div>}
          {sessions.map((s) => (
            <button key={s.session_id} onClick={() => setSelected(s.session_id)} className={`w-full text-left px-2 py-1 rounded border text-xs ${selected === s.session_id ? "border-primary text-primary" : "border-border/50 text-muted-foreground"}`}>
              <div className="font-mono truncate">{s.session_id}</div>
              <div className="flex justify-between"><span>{s.status}</span><span className="opacity-60">{s.last_activity?.slice(11, 19) ?? "--:--:--"}</span></div>
            </button>
          ))}
        </div>
      </div>
      {selected && (
        <>
          <div className="border rounded bg-background/40 p-2">
            <div className="font-semibold mb-2">Session Actions</div>
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => compactLogs(selected)} className="px-2 py-1 rounded border bg-blue-500/10 text-blue-500 hover:bg-blue-500/20">Compact Logs</button>
              <button onClick={() => resetSession(selected)} className="px-2 py-1 rounded border bg-amber-500/10 text-amber-500 hover:bg-amber-500/20">Reset</button>
              <button onClick={() => deleteSession(selected)} className="px-2 py-1 rounded border bg-red-500/10 text-red-500 hover:bg-red-500/20">Delete</button>
            </div>
          </div>
          <div className="border rounded bg-background/40 p-2">
            <div className="font-semibold mb-2">run.log tail</div>
            <pre className="text-[10px] font-mono whitespace-pre-wrap max-h-32 overflow-y-auto scrollbar-thin bg-background/50 p-2 rounded border">{logTail || "(empty)"}</pre>
          </div>
        </>
      )}
    </div>
  );
}

export function SkillsSection() {
  const { skills, fetchSkills } = useOps();
  const [selectedSkill, setSelectedSkill] = useState<SkillStatus | null>(null);
  const [docContent, setDocContent] = useState<string | null>(null);
  const [loadingDoc, setLoadingDoc] = useState(false);

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

  return (
    <div className="flex flex-col h-full min-h-[500px]">
      <div className="p-3 border-b border-border/40 flex items-center justify-between shrink-0">
        <h2 className="font-semibold text-sm">Skills Management</h2>
        <button onClick={fetchSkills} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all">↻ Refresh catalog</button>
      </div>
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Skill List */}
        <div className="w-1/3 border-r border-border/40 overflow-y-auto scrollbar-thin p-2 space-y-1 bg-background/20">
          {skills.length === 0 && <div className="text-muted-foreground p-2">No skills found</div>}
          {skills.map((s) => (
            <button
              key={s.name}
              onClick={() => setSelectedSkill(s)}
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
        {/* Right: Markdown Preview */}
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

export function ChannelsSection() {
  const { channels, fetchChannels, probeChannel } = useOps();
  return (
    <div className="p-3 text-xs">
      <div className="border rounded bg-background/40 p-2">
        <div className="font-semibold mb-2 flex items-center justify-between">
          <span>Channels</span>
          <button onClick={fetchChannels} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all">↻</button>
        </div>
        <div className="space-y-2 max-h-48 overflow-y-auto scrollbar-thin">
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

export function ApprovalsSection() {
  const { approvals, fetchApprovals, updateApproval } = useOps();
  return (
    <div className="p-3 text-xs">
      <div className="border rounded bg-background/40 p-2">
        <div className="font-semibold mb-2 flex items-center justify-between">
          <span>Approvals</span>
          <button onClick={fetchApprovals} className="text-[10px] px-2 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/60 transition-all">↻</button>
        </div>
        <div className="space-y-2 max-h-48 overflow-y-auto scrollbar-thin">
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

export function SystemEventsSection() {
  const { mergedEvents } = useOps();
  return (
    <div className="p-3 text-xs">
      <div className="border rounded bg-background/40 p-2">
        <div className="font-semibold mb-2">System Events</div>
        <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin">
          {mergedEvents.length === 0 && <div className="text-muted-foreground">No system events</div>}
          {mergedEvents.map((ev) => (
            <div key={ev.id} className="border rounded px-2 py-1 bg-background/50">
              <div className="flex justify-between text-[10px] text-muted-foreground"><span>{ev.event_type}</span><span>{ev.created_at?.slice(11, 19) ?? "--:--:--"}</span></div>
              <div className="font-mono text-[11px] truncate">{Object.keys(ev.payload || {}).join(", ") || "(no payload)"}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function OpsConfigSection() {
  const { opsConfigText, setOpsConfigText, opsConfigStatus, opsConfigError, opsConfigSaving, loadOpsConfig, saveOpsConfig, opsSchemaText, opsSchemaStatus } = useOps();
  return (
    <div className="p-3 text-xs space-y-3">
      <div className="border rounded bg-background/40 p-2">
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
      <div className="border rounded bg-background/40 p-2">
        <div className="flex items-center justify-between mb-2">
          <div className="font-semibold">Ops config schema</div>
          <div className="text-[10px] text-muted-foreground">{opsSchemaStatus}</div>
        </div>
        <textarea className="w-full min-h-[100px] text-[11px] font-mono p-2 rounded border bg-background/60" value={opsSchemaText} readOnly />
      </div>
    </div>
  );
}

export function HeartbeatWidget() {
  const { heartbeatState, selected } = useOps();
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
          {!selected && <div className="text-muted-foreground">Select a session to view heartbeat.</div>}
          {selected && (
            <>
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
