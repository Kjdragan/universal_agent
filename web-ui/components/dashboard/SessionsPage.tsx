"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAgentStore } from "@/lib/store";
import { getWebSocket } from "@/lib/websocket";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { formatDateTimeTz, formatTimeTz, getDisplayTimezone } from "@/lib/timezone";
import { OpsProvider, useOps } from "@/components/OpsDropdowns";

const API_BASE = "/api/dashboard/gateway";
const DISPLAY_TIMEZONE = getDisplayTimezone();

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildHeaders(): Record<string, string> {
  return {};
}

function relativeAge(isoDate: string | undefined | null): string {
  if (!isoDate) return "—";
  const ms = Date.now() - new Date(isoDate).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  if (hr < 24) return remMin > 0 ? `${hr}h ${remMin}m` : `${hr}h`;
  const days = Math.floor(hr / 24);
  const remHr = hr % 24;
  return remHr > 0 ? `${days}d ${remHr}h` : `${days}d`;
}

function ageSeconds(isoDate: string | undefined | null): number {
  if (!isoDate) return Infinity;
  const ms = Date.now() - new Date(isoDate).getTime();
  return Number.isNaN(ms) ? Infinity : ms / 1000;
}

type AgeTier = "fresh" | "aging" | "stale";

function ageTier(isoDate: string | undefined | null): AgeTier {
  const secs = ageSeconds(isoDate);
  if (secs < 3600) return "fresh";
  if (secs < 21600) return "aging";
  return "stale";
}

const AGE_COLORS: Record<AgeTier, string> = {
  fresh: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  aging: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  stale: "bg-red-500/20 text-red-400 border-red-500/30",
};

const AGE_DOTS: Record<AgeTier, string> = {
  fresh: "bg-emerald-400",
  aging: "bg-amber-400",
  stale: "bg-red-400",
};

function isActiveSession(s: { status: string; active_runs?: number }): boolean {
  const st = (s.status || "").toLowerCase();
  return st === "running" || st === "active" || (s.active_runs ?? 0) > 0;
}

const NOISE_PATTERNS = [
  /^session_hook_simone_heartbeat/i,
  /^session_hook_csi_/i,
  /^cron_/i,
];

function isNoisySession(s: {
  session_id: string;
  status: string;
  description?: string | null;
  has_checkpoint?: boolean;
  has_run_log?: boolean;
  active_runs?: number;
  last_activity?: string;
}): boolean {
  if (isActiveSession(s)) return false; // never hide active sessions
  const st = (s.status || "").toLowerCase();
  if (st !== "idle" && st !== "terminal") return false;
  // Must match a noise pattern
  const matchesNoise = NOISE_PATTERNS.some((p) => p.test(s.session_id));
  if (!matchesNoise) return false;
  // Has no description and no checkpoint → noise
  if (!s.description && !s.has_checkpoint) return true;
  // Has no run log and is older than 1hr → noise
  if (!s.has_run_log && ageSeconds(s.last_activity) > 3600) return true;
  return false;
}

function shortSessionId(id: string): string {
  // Abbreviate very long session IDs for display
  if (id.length <= 40) return id;
  return id.slice(0, 28) + "…" + id.slice(-8);
}

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

// ── Main Page Component ──────────────────────────────────────────────────────

export default function SessionsPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          <SessionsPageInner />
        </div>
      </div>
    </OpsProvider>
  );
}

function SessionsPageInner() {
  const {
    sessions,
    sessionsError,
    selected,
    setSelected,
    loading,
    logTail,
    fetchSessions,
    fetchLogs,
    deleteSession,
    resetSession,
    compactLogs,
    cancelSession,
    cancelOutstandingRuns,
    archiveSession,
  } = useOps();

  const [hideNoise, setHideNoise] = useState(true);
  const [showHistorical, setShowHistorical] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [expandLogTail, setExpandLogTail] = useState(true);
  const [selectedThread, setSelectedThread] = useState<WorkThreadRecord | null>(null);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [deliveryDecisionBusy, setDeliveryDecisionBusy] = useState(false);
  const [deliveryNoteDraft, setDeliveryNoteDraft] = useState("");
  const [deliveryStatus, setDeliveryStatus] = useState<string>("");
  const [rehydratingId, setRehydratingId] = useState<string | null>(null);

  const isVpSelected = /^vp_/i.test((selected || "").trim());

  // ── Categorized sessions ──
  const { activeSessions, historicalSessions, noiseCount, stats } = useMemo(() => {
    const sorted = [...sessions].sort((a, b) => {
      const aTs = Date.parse(a.last_activity || a.last_modified || "") || 0;
      const bTs = Date.parse(b.last_activity || b.last_modified || "") || 0;
      if (aTs !== bTs) return bTs - aTs;
      return String(b.session_id || "").localeCompare(String(a.session_id || ""));
    });

    const active: typeof sessions = [];
    const historical: typeof sessions = [];
    let noise = 0;

    for (const s of sorted) {
      if (isActiveSession(s)) {
        active.push(s);
      } else {
        if (hideNoise && isNoisySession(s)) {
          noise++;
          continue;
        }
        historical.push(s);
      }
    }

    const staleCount = sessions.filter(
      (s) => !isActiveSession(s) && ageTier(s.last_activity || s.created_at) === "stale"
    ).length;

    return {
      activeSessions: active,
      historicalSessions: historical,
      noiseCount: noise,
      stats: {
        total: sessions.length,
        active: active.length,
        idle: sessions.filter((s) => (s.status || "").toLowerCase() === "idle").length,
        stale: staleCount,
      },
    };
  }, [sessions, hideNoise]);

  // ── Work thread loading ──
  const loadWorkThread = useCallback(async (sessionId: string) => {
    setDeliveryLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/work-threads?session_id=${encodeURIComponent(sessionId)}`, { headers: buildHeaders() });
      if (!r.ok) { setSelectedThread(null); return; }
      const d = await r.json();
      const rows = Array.isArray(d.threads) ? d.threads : [];
      setSelectedThread((rows[0] as WorkThreadRecord | undefined) ?? null);
    } catch { setSelectedThread(null); }
    finally { setDeliveryLoading(false); }
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

  // ── Delivery decision ──
  const recordDeliveryDecision = useCallback(async (sessionId: string, decision: DeliveryDecision) => {
    setDeliveryDecisionBusy(true);
    const note = deliveryNoteDraft.trim();
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/work-threads/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({
          session_id: sessionId, decision, note: note || undefined,
          metadata: { vp_observer_lane: isVpSelected, source: "ops.sessions.delivery_workflow" },
        }),
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        setDeliveryStatus(`Failed (${r.status})${detail ? `: ${detail}` : ""}`);
        return;
      }
      const d = await r.json();
      setSelectedThread((d.thread as WorkThreadRecord) ?? null);
      setDeliveryStatus(
        decision === "promote" ? "Promote Now recorded." :
        decision === "iterate" ? "Open Iteration recorded." :
        "Archive Draft recorded."
      );
    } catch (e) { setDeliveryStatus(`Failed: ${(e as Error).message}`); }
    finally { setDeliveryDecisionBusy(false); }
  }, [deliveryNoteDraft, isVpSelected]);

  // ── Attach to chat ──
  const attachToChat = useCallback(async (sessionId: string) => {
    setAttaching(true);
    try {
      const sid = String(sessionId || "").trim();
      const forceViewer = /^vp_/i.test(sid) || /^session[_-]hook_/i.test(sid) || /^cron_/i.test(sid) || /^worker_/i.test(sid);
      if (typeof window !== "undefined" && window.location.pathname.startsWith("/dashboard")) {
        openOrFocusChatWindow({ sessionId: sid, attachMode: "tail", role: forceViewer ? "viewer" : "writer" });
        return;
      }
      const store = useAgentStore.getState();
      store.reset();
      store.setSessionAttachMode("tail");
      const ws = getWebSocket();
      ws.attachToSession(sessionId);
    } finally { setAttaching(false); }
  }, []);

  // ── Rehydrate ──
  const rehydrateSession = useCallback(async (sessionId: string) => {
    setRehydratingId(sessionId);
    try {
      const r = await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}`, { headers: buildHeaders() });
      if (r.ok) {
        fetchSessions();
      }
    } catch { /* silently fail */ }
    finally { setRehydratingId(null); }
  }, [fetchSessions]);

  const runningCount = useMemo(
    () => sessions.filter((s) => isActiveSession(s)).length,
    [sessions],
  );

  const hasSelectedSession = Boolean(selected);
  const selectedSession = useMemo(
    () => sessions.find((s) => s.session_id === selected),
    [sessions, selected],
  );

  // ── Auto-refresh timer for age display ──
  const [, forceRefresh] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => forceRefresh((n) => n + 1), 30000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className={`px-4 pb-4 pt-0 text-sm space-y-4 ${hasSelectedSession ? "lg:grid lg:grid-cols-[minmax(420px,560px)_1fr] lg:gap-4 lg:space-y-0" : ""}`}>

      {/* ── Top Bar ── */}
      <div className={`${hasSelectedSession ? "lg:col-span-2" : ""} flex flex-wrap items-center justify-between gap-3 pt-3`}>
        <div className="flex items-center gap-3">
          <Link href="/" className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25 transition-colors">
            Back to Home
          </Link>
          <h1 className="text-base font-semibold text-slate-100">Sessions</h1>
          <span className="text-[10px] text-slate-500 font-mono">{DISPLAY_TIMEZONE}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Stats pills */}
          <div className="flex items-center gap-1.5">
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">{stats.active} active</span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-500/15 text-slate-400 border border-slate-500/30">{stats.idle} idle</span>
            {stats.stale > 0 && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-500/15 text-red-400 border border-red-500/30 animate-pulse">
                ⚠ {stats.stale} stale
              </span>
            )}
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-700/40 text-slate-500 border border-slate-700/30">{stats.total} total</span>
          </div>

          <div className="w-px h-5 bg-slate-700" />

          <button
            onClick={cancelOutstandingRuns}
            className="text-[11px] px-2.5 py-1 rounded-md border border-orange-500/40 bg-orange-500/10 text-orange-400 hover:bg-orange-500/20 transition-all disabled:opacity-40"
            disabled={runningCount === 0}
          >
            Kill Outstanding ({runningCount})
          </button>
          <button
            onClick={fetchSessions}
            className="text-[11px] px-2.5 py-1 rounded-md border border-slate-600/60 bg-slate-800/40 text-slate-300 hover:bg-slate-700/60 transition-all"
            disabled={loading}
          >
            {loading ? "…" : "↻ Refresh"}
          </button>
          {selected && !isVpSelected && (
            <button
              onClick={() => attachToChat(selected)}
              className="text-[11px] px-2.5 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-all"
              disabled={attaching}
            >
              {attaching ? "…" : "Open Chat"}
            </button>
          )}
        </div>
      </div>

      {/* ── Left Column: Session Lists ── */}
      <div className={`${hasSelectedSession ? "lg:row-span-5 lg:max-h-[82vh] lg:overflow-y-auto" : "lg:max-w-[900px]"} space-y-3 scrollbar-thin`}>

        {/* ── Active Sessions ── */}
        <div className="border border-emerald-800/40 rounded-lg bg-emerald-950/20 overflow-hidden">
          <div className="px-3 py-2 border-b border-emerald-800/30 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="text-xs font-semibold text-emerald-300 uppercase tracking-wider">Active Sessions</span>
              <span className="text-[10px] text-emerald-500/70">({activeSessions.length})</span>
            </div>
          </div>
          <div className="p-2 space-y-1.5">
            {activeSessions.length === 0 && (
              <div className="text-[11px] text-emerald-600/60 text-center py-3 italic">No active sessions</div>
            )}
            {activeSessions.map((s) => (
              <SessionCard
                key={s.session_id}
                session={s}
                isSelected={selected === s.session_id}
                onSelect={() => setSelected(s.session_id)}
                onRehydrate={rehydrateSession}
                rehydrating={rehydratingId === s.session_id}
                isActive
              />
            ))}
          </div>
        </div>

        {/* ── Historical Sessions ── */}
        <div className="border border-slate-700/40 rounded-lg bg-slate-900/40 overflow-hidden">
          <button
            onClick={() => setShowHistorical(!showHistorical)}
            className="w-full px-3 py-2 border-b border-slate-700/30 flex items-center justify-between hover:bg-slate-800/30 transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Historical Sessions</span>
              <span className="text-[10px] text-slate-600">({historicalSessions.length})</span>
              {noiseCount > 0 && (
                <span className="text-[9px] text-slate-600 italic">+{noiseCount} hidden</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <label
                className="flex items-center gap-1.5 text-[10px] text-slate-500 cursor-pointer"
                onClick={(e) => e.stopPropagation()}
              >
                <input
                  type="checkbox"
                  checked={hideNoise}
                  onChange={(e) => setHideNoise(e.target.checked)}
                  className="rounded border-slate-600 bg-slate-800 w-3 h-3 accent-cyan-500"
                />
                Hide noise
              </label>
              <span className="text-slate-600 text-xs">{showHistorical ? "▼" : "▶"}</span>
            </div>
          </button>

          {showHistorical && (
            <div className="p-2 space-y-1.5 max-h-[50vh] overflow-y-auto scrollbar-thin">
              {historicalSessions.length === 0 && (
                <div className="text-[11px] text-slate-600 text-center py-3 italic">
                  {hideNoise ? "No interesting historical sessions (noise hidden)" : "No historical sessions"}
                </div>
              )}
              {historicalSessions.map((s) => (
                <SessionCard
                  key={s.session_id}
                  session={s}
                  isSelected={selected === s.session_id}
                  onSelect={() => setSelected(s.session_id)}
                  onRehydrate={rehydrateSession}
                  rehydrating={rehydratingId === s.session_id}
                  isActive={false}
                />
              ))}
            </div>
          )}
        </div>

        {sessionsError && (
          <div className="rounded-md border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-[11px] text-amber-400">
            {sessionsError}
          </div>
        )}
      </div>

      {/* ── Right Column: Session Detail ── */}
      {selected && selectedSession && (
        <>
          {/* VP Observer Warning */}
          {isVpSelected && (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-rose-300">
              VP Observer Mode: view-only
            </div>
          )}

          {/* Session Actions */}
          <div className="border border-slate-700/40 rounded-lg bg-slate-900/50 p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Session Detail</span>
              <span className="text-[10px] text-slate-500 font-mono">{selected}</span>
            </div>

            {/* Quick info row */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <InfoPill label="Status" value={selectedSession.status} color={
                isActiveSession(selectedSession) ? "emerald" :
                selectedSession.status === "terminal" ? "red" : "slate"
              } />
              <InfoPill label="Source" value={selectedSession.source || selectedSession.channel || "local"} color="cyan" />
              <InfoPill label="Owner" value={selectedSession.owner || "unknown"} color="violet" />
              <InfoPill label="Memory" value={selectedSession.memory_mode || "direct_only"} color="sky" />
            </div>

            {/* Age Row */}
            <div className="flex items-center gap-3 text-[10px]">
              <span className="text-slate-500">Created:</span>
              <span className="text-slate-400">{formatDateTimeTz(selectedSession.created_at, { placeholder: "—" })}</span>
              <AgeBadge isoDate={selectedSession.created_at} label="age" />
              <span className="text-slate-600">|</span>
              <span className="text-slate-500">Last activity:</span>
              <span className="text-slate-400">{relativeAge(selectedSession.last_activity)} ago</span>
            </div>

            {/* Description */}
            {selectedSession.description && (
              <div className="rounded-md bg-slate-800/50 px-3 py-2 text-[11px] text-slate-300 italic border border-slate-700/30">
                &ldquo;{selectedSession.description}&rdquo;
              </div>
            )}

            {/* Action buttons */}
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => attachToChat(selected)} className="px-2.5 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 text-xs transition-all" disabled={attaching}>
                {attaching ? "…" : isVpSelected ? "Observer Chat" : "Attach Chat (Tail)"}
              </button>
              {!isVpSelected && (
                <>
                  <button onClick={() => cancelSession(selected)} className="px-2.5 py-1 rounded-md border border-orange-500/40 bg-orange-500/10 text-orange-400 hover:bg-orange-500/20 text-xs transition-all">Cancel Run</button>
                  <button onClick={() => archiveSession(selected)} className="px-2.5 py-1 rounded-md border border-sky-500/40 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 text-xs transition-all">Archive</button>
                  <button onClick={() => compactLogs(selected)} className="px-2.5 py-1 rounded-md border border-blue-500/40 bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 text-xs transition-all">Compact Logs</button>
                  <button onClick={() => resetSession(selected)} className="px-2.5 py-1 rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 text-xs transition-all">Reset</button>
                  <button onClick={() => deleteSession(selected)} className="px-2.5 py-1 rounded-md border border-red-500/40 bg-red-500/10 text-red-400 hover:bg-red-500/20 text-xs transition-all">Delete</button>
                </>
              )}
            </div>

            {isVpSelected && (
              <div className="text-[10px] text-slate-500 italic">VP sessions are view-only. Use Simone chat for changes.</div>
            )}
          </div>

          {/* Rehydrate Status */}
          <RehydrateStatus session={selectedSession} onRehydrate={rehydrateSession} rehydrating={rehydratingId === selected} />

          {/* Log Tail */}
          <div className="border border-slate-700/40 rounded-lg bg-slate-900/50 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-300">run.log tail</span>
              <button
                onClick={() => setExpandLogTail(!expandLogTail)}
                className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600/60 bg-slate-800/40 hover:bg-slate-700/60 transition-all text-slate-400"
              >
                {expandLogTail ? "Compact" : "Expand"}
              </button>
            </div>
            <pre className={`text-[10px] font-mono whitespace-pre-wrap overflow-y-auto scrollbar-thin bg-black/30 p-2 rounded border border-slate-800/60 ${expandLogTail ? "max-h-[46vh]" : "max-h-40"}`}>
              {logTail || (isVpSelected ? "(empty VP lane log)" : "(empty)")}
            </pre>
          </div>

          {/* Delivery Workflow */}
          <div className="border border-slate-700/40 rounded-lg bg-slate-900/50 p-3 space-y-2">
            <div className="text-xs font-semibold text-slate-300">Delivery Workflow</div>
            <div className="text-[10px] text-slate-500">Choose how to handle this output: promote, iterate, or archive.</div>

            {deliveryLoading && <div className="text-[10px] text-slate-600">Loading thread…</div>}
            {selectedThread && (
              <div className="rounded-md border border-cyan-700/40 bg-cyan-900/10 px-2 py-1 text-[10px] text-cyan-200">
                Current: <span className="font-semibold uppercase">{selectedThread.decision || "pending"}</span>
                {" · "}
                {selectedThread.updated_at ? formatDateTimeTz(selectedThread.updated_at) : "recent"}
                {selectedThread.patch_version ? ` · v${selectedThread.patch_version}` : ""}
              </div>
            )}
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => selected && recordDeliveryDecision(selected, "promote")} disabled={!selected || isVpSelected || deliveryDecisionBusy} className="px-2.5 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 text-xs disabled:opacity-40 transition-all">Promote Now</button>
              <button onClick={() => selected && recordDeliveryDecision(selected, "iterate")} disabled={!selected || deliveryDecisionBusy} className="px-2.5 py-1 rounded-md border border-cyan-500/40 bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 text-xs disabled:opacity-40 transition-all">Open Iteration</button>
              <button onClick={() => selected && recordDeliveryDecision(selected, "archive")} disabled={!selected || isVpSelected || deliveryDecisionBusy} className="px-2.5 py-1 rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 text-xs disabled:opacity-40 transition-all">Archive Draft</button>
            </div>
            <textarea
              value={deliveryNoteDraft}
              onChange={(e) => setDeliveryNoteDraft(e.target.value)}
              placeholder="Optional notes (acceptance criteria, follow-up asks, risk notes)"
              className="w-full rounded-md border border-slate-700/60 bg-slate-800/40 px-2 py-1.5 text-[11px] min-h-16 text-slate-300 placeholder:text-slate-600 focus:border-cyan-600/40 focus:outline-none"
            />
            {deliveryStatus && (
              <div className="text-[10px] text-emerald-400">{deliveryStatus}</div>
            )}
          </div>
        </>
      )}

      {/* No selection state */}
      {!selected && (
        <div className="flex items-center justify-center py-16 text-slate-600 text-sm italic">
          Select a session to view details
        </div>
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SessionCard({
  session: s,
  isSelected,
  onSelect,
  onRehydrate,
  rehydrating,
  isActive,
}: {
  session: {
    session_id: string;
    status: string;
    source?: string;
    channel?: string;
    owner?: string;
    description?: string;
    created_at?: string;
    last_activity?: string;
    last_modified?: string;
    active_runs?: number;
    rehydrate_ready?: boolean;
    rehydrate_reason?: string;
    has_checkpoint?: boolean;
    checkpoint_tasks_completed?: number;
    checkpoint_artifacts_count?: number;
  };
  isSelected: boolean;
  onSelect: () => void;
  onRehydrate: (id: string) => void;
  rehydrating: boolean;
  isActive: boolean;
}) {
  const tier = ageTier(s.created_at || s.last_modified);

  return (
    <button
      onClick={onSelect}
      className={`
        w-full text-left px-3 py-2 rounded-lg border transition-all duration-150
        ${isSelected
          ? "border-cyan-500/50 bg-cyan-950/30 shadow-glow-sm"
          : "border-slate-700/40 bg-slate-800/20 hover:bg-slate-800/40 hover:border-slate-600/50"
        }
      `}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {/* Session ID */}
          <div className="font-mono text-[11px] text-slate-200 truncate">{shortSessionId(s.session_id)}</div>

          {/* Description */}
          {s.description && (
            <div className="text-[10px] text-slate-400 truncate mt-0.5 italic">{s.description}</div>
          )}

          {/* Meta row */}
          <div className="flex items-center gap-2 mt-1 text-[10px]">
            {/* Status badge */}
            {isActive ? (
              <span className="flex items-center gap-1 text-emerald-400">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-400" />
                </span>
                {s.status}
              </span>
            ) : (
              <span className="text-slate-500">{s.status}</span>
            )}
            <span className="text-slate-600">·</span>
            <span className="text-slate-500">{s.source || s.channel || "local"}</span>
            <span className="text-slate-600">·</span>
            <span className="text-slate-500">{s.owner || "unknown"}</span>
          </div>
        </div>

        {/* Right side: age + rehydrate */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          <AgeBadge isoDate={s.created_at || s.last_modified} />

          <span className="text-[9px] text-slate-600">
            {relativeAge(s.last_activity)} ago
          </span>

          {/* Rehydrate indicator */}
          {!isActive && s.rehydrate_ready && (
            <button
              onClick={(e) => { e.stopPropagation(); onRehydrate(s.session_id); }}
              className="text-[9px] px-1.5 py-0.5 rounded border border-cyan-600/40 bg-cyan-600/10 text-cyan-400 hover:bg-cyan-600/20 transition-all"
              disabled={rehydrating}
            >
              {rehydrating ? "…" : "↻ Rehydrate"}
            </button>
          )}

          {/* Stale warning */}
          {!isActive && tier === "stale" && (
            <span className="text-[9px] text-red-400/70 flex items-center gap-0.5" title="Session has been idle for 6+ hours. Reaper may have missed it.">
              ⚠ stale
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

function AgeBadge({ isoDate, label }: { isoDate?: string | null; label?: string }) {
  const tier = ageTier(isoDate);
  const age = relativeAge(isoDate);
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold border ${AGE_COLORS[tier]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${AGE_DOTS[tier]}`} />
      {label ? `${label}: ${age}` : age}
    </span>
  );
}

function InfoPill({ label, value, color }: { label: string; value: string; color: string }) {
  const colorMap: Record<string, string> = {
    emerald: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    red: "bg-red-500/10 text-red-400 border-red-500/20",
    slate: "bg-slate-500/10 text-slate-400 border-slate-500/20",
    cyan: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
    violet: "bg-violet-500/10 text-violet-400 border-violet-500/20",
    sky: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  };
  return (
    <div className={`rounded-md border px-2 py-1 ${colorMap[color] || colorMap.slate}`}>
      <div className="text-[9px] text-slate-500 uppercase">{label}</div>
      <div className="text-[11px] font-medium truncate">{value}</div>
    </div>
  );
}

function RehydrateStatus({
  session,
  onRehydrate,
  rehydrating,
}: {
  session: {
    session_id: string;
    rehydrate_ready?: boolean;
    rehydrate_reason?: string;
    has_checkpoint?: boolean;
    checkpoint_tasks_completed?: number;
    checkpoint_artifacts_count?: number;
  };
  onRehydrate: (id: string) => void;
  rehydrating: boolean;
}) {
  if (session.rehydrate_ready) {
    return (
      <div className="rounded-lg border border-emerald-700/40 bg-emerald-950/20 px-3 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] text-emerald-300">
          <span className="text-emerald-400">✓</span>
          <span>Rehydrate ready ({(session.rehydrate_reason || "").replace(/_/g, " ")})</span>
          {session.has_checkpoint && session.checkpoint_tasks_completed != null && (
            <span className="text-emerald-400/60">
              · {session.checkpoint_tasks_completed} tasks, {session.checkpoint_artifacts_count ?? 0} artifacts
            </span>
          )}
        </div>
        <button
          onClick={() => onRehydrate(session.session_id)}
          className="text-[10px] px-2 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-all"
          disabled={rehydrating}
        >
          {rehydrating ? "Rehydrating…" : "↻ Rehydrate Now"}
        </button>
      </div>
    );
  }

  const reason = session.rehydrate_reason || "";
  const hints: Record<string, string> = {
    no_run_log: "No run log found.",
    no_checkpoint: "No checkpoint file.",
    no_memory_file: "No MEMORY.md or memory directory.",
    memory_mode_direct_only: "Memory mode is direct_only.",
  };
  const parts = reason.split("; ").filter(Boolean);

  return (
    <div className="rounded-lg border border-amber-700/40 bg-amber-950/15 px-3 py-2 space-y-1">
      <div className="text-[11px] font-semibold text-amber-300">Cannot rehydrate</div>
      <ul className="list-disc list-inside text-[10px] text-amber-200/70 space-y-0.5">
        {parts.map((p) => <li key={p}>{hints[p] || p.replace(/_/g, " ")}</li>)}
      </ul>
    </div>
  );
}
