"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAgentStore } from "@/lib/store";
import { getWebSocket } from "@/lib/websocket";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { openViewer } from "@/lib/viewer/openViewer";
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
  fresh: "bg-primary/20 text-primary border-primary/20",
  aging: "bg-accent/20 text-accent border-accent/30",
  stale: "bg-red-500/20 text-red-400 border-red-500/30",
};

const AGE_DOTS: Record<AgeTier, string> = {
  fresh: "bg-primary",
  aging: "bg-amber-400",
  stale: "bg-red-400",
};

// ── Channel grouping constants ────────────────────────────────────────────────

const ACTIVE_WINDOW_MS = 36 * 3600 * 1000; // 36 hours

type ChannelKey = "interactive" | "vp_mission" | "email" | "scheduled" | "proactive" | "discord" | "infrastructure" | "system";

const CHANNEL_META: Record<ChannelKey, { icon: string; label: string; color: string; border: string; bg: string }> = {
  interactive:    { icon: "💬", label: "Interactive Chats",   color: "text-primary",       border: "border-primary/25",       bg: "bg-primary/5" },
  vp_mission:     { icon: "🤖", label: "VP Missions",        color: "text-violet-400",    border: "border-violet-500/25",    bg: "bg-violet-500/5" },
  email:          { icon: "📧", label: "Email",              color: "text-sky-400",       border: "border-sky-500/25",       bg: "bg-sky-500/5" },
  scheduled:      { icon: "⏰", label: "Scheduled / Cron",   color: "text-amber-400",     border: "border-amber-500/25",     bg: "bg-amber-500/5" },
  proactive:      { icon: "📡", label: "Proactive Signals",  color: "text-emerald-400",   border: "border-emerald-500/25",   bg: "bg-emerald-500/5" },
  discord:        { icon: "🎮", label: "Discord",            color: "text-indigo-400",    border: "border-indigo-500/25",    bg: "bg-indigo-500/5" },
  infrastructure: { icon: "🔧", label: "Infrastructure",     color: "text-muted-foreground", border: "border-border/30",     bg: "bg-card/20" },
  system:         { icon: "⚙️",  label: "System",             color: "text-muted-foreground", border: "border-border/30",     bg: "bg-card/20" },
};

const CHANNEL_ORDER: ChannelKey[] = ["interactive", "vp_mission", "email", "scheduled", "proactive", "discord", "infrastructure", "system"];

function isDaemonSession(s: { session_id: string }): boolean {
  return (s.session_id || "").startsWith("daemon_");
}

function isActiveSession(s: { session_id: string; status: string; active_runs?: number }): boolean {
  if (isDaemonSession(s)) return true; // daemon sessions are always considered active
  const st = (s.status || "").toLowerCase();
  return st === "running" || st === "active" || (s.active_runs ?? 0) > 0;
}

function isLiveAttachableSession(s: { is_live_session?: boolean } | null | undefined): boolean {
  return s?.is_live_session !== false;
}

const NOISE_PATTERNS = [
  /^session_hook_simone_heartbeat/i,
  /^session_hook_csi_/i,
  /^run_session_hook_csi_/i,
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
  if (isDaemonSession(s)) return false; // daemon sessions are never noise
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
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-background/70">
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
    purgeStale,
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
  const [staleFilter, setStaleFilter] = useState(false);
  const [purging, setPurging] = useState(false);

  // Dossier viewer state
  const [dossierContent, setDossierContent] = useState<string | null>(null);
  const [dossierLoading, setDossierLoading] = useState(false);
  const [dossierGenerating, setDossierGenerating] = useState<string | null>(null);
  const [showOlderSessions, setShowOlderSessions] = useState(false);

  // ── Deep-link from calendar / Task Hub / other pages ──
  // Accept BOTH `?session_id=` (canonical, used by chatWindow.ts + Task Hub +
  // gateway emitters) and `?sid=` (legacy, used by CalendarPage). Prefer the
  // canonical form so `?session_id=...&sid=...` resolves consistently.
  const searchParams = useSearchParams();
  const deepLinkSid = searchParams.get("session_id") || searchParams.get("sid");
  const deepLinkApplied = useRef(false);

  useEffect(() => {
    if (!deepLinkSid || deepLinkApplied.current) return;
    if (sessions.length === 0) return; // wait until sessions load
    deepLinkApplied.current = true;
    // Check if the session exists in the loaded sessions
    const found = sessions.find((s) => s.session_id === deepLinkSid);
    if (found) {
      setSelected(deepLinkSid);
      // If the session is historical (not active/daemon), expand the historical section
      if (!isActiveSession(found) && !isDaemonSession(found)) {
        setShowHistorical(true);
      }
    } else {
      // Session not in current list — try to rehydrate and select anyway
      setSelected(deepLinkSid);
      setShowHistorical(true);
    }
  }, [deepLinkSid, sessions, setSelected]);

  const isVpSelected = /^vp_/i.test((selected || "").trim());

  // ── Categorized sessions ──
  const { daemonSessions, activeSessions, historicalSessions, noiseCount, stats, channelGroups, olderSessions } = useMemo(() => {
    const sorted = [...sessions].sort((a, b) => {
      const aTs = Date.parse(a.last_activity || a.last_modified || "") || 0;
      const bTs = Date.parse(b.last_activity || b.last_modified || "") || 0;
      if (aTs !== bTs) return bTs - aTs;
      return String(b.session_id || "").localeCompare(String(a.session_id || ""));
    });

    const daemon: typeof sessions = [];
    const active: typeof sessions = [];
    const historical: typeof sessions = [];
    let noise = 0;

    // Channel groupings for the inbox view
    const chGroups: Record<string, typeof sessions> = {};
    const older: typeof sessions = [];
    const now = Date.now();

    for (const s of sorted) {
      if (isDaemonSession(s)) {
        daemon.push(s);
      } else if (isActiveSession(s)) {
        active.push(s);
      } else {
        if (hideNoise && isNoisySession(s)) {
          noise++;
          continue;
        }
        historical.push(s);
      }

      // Channel grouping: all non-daemon sessions within the active window
      if (!isDaemonSession(s)) {
        const ts = Date.parse(s.last_activity || s.last_modified || "") || 0;
        if (now - ts <= ACTIVE_WINDOW_MS) {
          const ch = (s.channel || "system") as ChannelKey;
          (chGroups[ch] ??= []).push(s);
        } else {
          older.push(s);
        }
      }
    }

    const staleCount = sessions.filter(
      (s) => !isActiveSession(s) && !isDaemonSession(s) && ageTier(s.last_activity || s.created_at) === "stale"
    ).length;

    return {
      daemonSessions: daemon,
      activeSessions: active,
      historicalSessions: historical,
      noiseCount: noise,
      channelGroups: chGroups as Record<ChannelKey, typeof sessions>,
      olderSessions: older,
      stats: {
        total: sessions.length,
        active: active.length + daemon.length,
        daemon: daemon.length,
        idle: sessions.filter((s) => !isDaemonSession(s) && (s.status || "").toLowerCase() === "idle").length,
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
      const target = sessions.find((item) => item.session_id === sid);
      const runId = String(target?.run_id || "").trim();
      if (target && !isLiveAttachableSession(target) && runId) {
        // Track B: run-only viewer goes through the centralized resolver,
        // which fixes the case where chatWindow.ts produced a URL that
        // required session_id and silently dropped run-only links.
        void openViewer({ run_id: runId, role: "viewer" });
        return;
      }
      const forceViewer = /^vp_/i.test(sid) || /^session[_-]hook_/i.test(sid) || /^cron_/i.test(sid) || /^worker_/i.test(sid);
      if (typeof window !== "undefined" && window.location.pathname.startsWith("/dashboard")) {
        if (forceViewer) {
          // Track B: viewer-only sessions (vp_, hook, cron, worker) read
          // through the centralized resolver. Writer-mode (live attach)
          // continues on the legacy root chat viewer until Commit 8.
          void openViewer({ session_id: sid, role: "viewer", attachMode: "tail" });
        } else {
          openOrFocusChatWindow({ sessionId: sid, attachMode: "tail", role: "writer" });
        }
        return;
      }
      const store = useAgentStore.getState();
      store.reset();
      store.setSessionAttachMode("tail");
      const ws = getWebSocket();
      ws.attachToSession(sessionId);
    } finally { setAttaching(false); }
  }, [sessions]);

  // ── Rehydrate ──
  const rehydrateSession = useCallback(async (sessionId: string) => {
    setRehydratingId(sessionId);
    try {
      const r = await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}`, { headers: buildHeaders() });
      if (r.ok) {
        fetchSessions();
        // Navigate to the three-panel chat view with this session attached
        openOrFocusChatWindow({ sessionId, attachMode: "tail", role: "writer" });
      }
    } catch { /* silently fail */ }
    finally { setRehydratingId(null); }
  }, [fetchSessions]);

  // ── Dossier viewer ──
  const fetchDossier = useCallback(async (sessionId: string) => {
    setDossierLoading(true);
    setDossierContent(null);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${encodeURIComponent(sessionId)}/context-brief`, { headers: buildHeaders() });
      if (r.ok) {
        const d = await r.json();
        setDossierContent(d.context_brief || null);
      }
    } catch { /* silently fail */ }
    finally { setDossierLoading(false); }
  }, []);

  // ── Generate dossier for a session ──
  const generateDossier = useCallback(async (sessionId: string) => {
    setDossierGenerating(sessionId);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${encodeURIComponent(sessionId)}/generate-dossier`, {
        method: "POST",
        headers: buildHeaders(),
      });
      if (r.ok) {
        fetchSessions(); // Refresh to pick up has_context_brief
        void fetchDossier(sessionId); // Load the new dossier
      }
    } catch { /* silently fail */ }
    finally { setDossierGenerating(null); }
  }, [fetchSessions, fetchDossier]);

  // ── Chat with Simone (context handoff) ──
  const chatWithSimone = useCallback(async (sessionId: string) => {
    // For rehydratable sessions, just open the chat directly
    const target = sessions.find((s) => s.session_id === sessionId);
    if (target && isLiveAttachableSession(target)) {
      openOrFocusChatWindow({ sessionId, attachMode: "tail", role: "writer" });
      return;
    }
    // For non-rehydratable: build a context handoff message from the dossier
    let briefText = dossierContent;
    if (!briefText) {
      try {
        const r = await fetch(`${API_BASE}/api/v1/ops/sessions/${encodeURIComponent(sessionId)}/context-brief`, { headers: buildHeaders() });
        if (r.ok) {
          const d = await r.json();
          briefText = d.context_brief;
        }
      } catch { /* silently fail */ }
    }
    const handoffMessage = briefText
      ? `I'm following up on a previous session (${sessionId}). Here's the context brief:\n\n${briefText}\n\nPlease review and let me know the status.`
      : `I'm following up on a previous session (${sessionId}). Can you check what happened and give me a summary?`;

    openOrFocusChatWindow({ newSession: true, message: handoffMessage, autoSend: true });
  }, [sessions, dossierContent]);

  // Clear dossier when selection changes
  useEffect(() => {
    setDossierContent(null);
  }, [selected]);

  const runningCount = useMemo(
    () => sessions.filter((s) => isActiveSession(s)).length,
    [sessions],
  );

  const hasSelectedSession = Boolean(selected);
  const selectedSession = useMemo(
    () => sessions.find((s) => s.session_id === selected),
    [sessions, selected],
  );
  const selectedIsLiveSession = isLiveAttachableSession(selectedSession);
  const selectedRunViewerId = String(selectedSession?.run_id || "").trim();

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
          <Link href="/" className="rounded-lg border border-primary/30 bg-primary/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-primary/90 hover:bg-primary/25 transition-colors">
            Back to Home
          </Link>
          <h1 className="text-base font-semibold text-foreground">Sessions</h1>
          <span className="text-[10px] text-muted-foreground font-mono">{DISPLAY_TIMEZONE}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Stats pills */}
          <div className="flex items-center gap-1.5">
            {stats.daemon > 0 && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-violet-500/15 text-violet-400 border border-violet-500/25">{stats.daemon} daemon</span>
            )}
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-primary/15 text-primary border border-primary/20">{stats.active} active</span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-muted-foreground/15 text-muted-foreground border border-muted-foreground/30">{stats.idle} idle</span>
            {stats.stale > 0 && (
              <button
                onClick={() => setStaleFilter(!staleFilter)}
                className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border transition-all cursor-pointer ${
                  staleFilter
                    ? "bg-red-500/30 text-red-400 border-red-400/60 ring-2 ring-red-500/40 shadow-[0_0_8px_rgba(239,68,68,0.3)]"
                    : "bg-red-500/15 text-red-400 border-red-500/30 animate-pulse hover:bg-red-500/25"
                }`}
              >
                ⚠ {stats.stale} stale
              </button>
            )}
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-card/50/40 text-muted-foreground border border-border/30">{stats.total} total</span>
          </div>

          <div className="w-px h-5 bg-card/50" />

          <button
            onClick={cancelOutstandingRuns}
            className="text-[11px] px-2.5 py-1 rounded-md border border-accent/25 bg-accent/10 text-accent hover:bg-accent/20 transition-all disabled:opacity-40"
            disabled={runningCount === 0}
          >
            Kill Outstanding ({runningCount})
          </button>
          {staleFilter && stats.stale > 0 && (
            <button
              onClick={async () => {
                if (!confirm(`Permanently purge ${stats.stale} stale sessions?\n\nMemory captures will be preserved.`)) return;
                setPurging(true);
                const result = await purgeStale(6);
                setPurging(false);
                if (result) {
                  setStaleFilter(false);
                  alert(`Purged ${result.deleted_count} stale sessions.`);
                }
              }}
              className="text-[11px] px-2.5 py-1 rounded-md border border-red-500/50 bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-all disabled:opacity-40 font-semibold"
              disabled={purging}
            >
              {purging ? "Purging…" : `🗑 Purge ${stats.stale} Stale`}
            </button>
          )}
          <button
            onClick={fetchSessions}
            className="text-[11px] px-2.5 py-1 rounded-md border border-border/60 bg-card/40 text-foreground/80 hover:bg-card/50/60 transition-all"
            disabled={loading}
          >
            {loading ? "…" : "↻ Refresh"}
          </button>
          {selected && !isVpSelected && (
            <button
              onClick={() => attachToChat(selected)}
              className="text-[11px] px-2.5 py-1 rounded-md border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 transition-all"
              disabled={attaching || (!selectedIsLiveSession && !selectedRunViewerId)}
            >
              {attaching ? "…" : selectedIsLiveSession ? "Open Chat" : "Open Run Viewer"}
            </button>
          )}
        </div>
      </div>

      {/* ── Left Column: Session Lists ── */}
      <div className={`${hasSelectedSession ? "lg:row-span-5 lg:max-h-[82vh] lg:overflow-y-auto" : "lg:max-w-[900px]"} space-y-3 scrollbar-thin`}>

        {/* ── Daemon Agent Sessions ── */}
        {daemonSessions.length > 0 && (
          <div className="border border-violet-500/25 rounded-lg bg-violet-500/5 overflow-hidden">
            <div className="px-3 py-2 border-b border-violet-500/15 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="animate-pulse absolute inline-flex h-full w-full rounded-full bg-violet-400 opacity-60" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-violet-400" />
                </span>
                <span className="text-xs font-semibold text-violet-400 uppercase tracking-wider">Daemon Agents</span>
                <span className="text-[10px] text-violet-400/70">({daemonSessions.length}) — always on</span>
              </div>
            </div>
            <div className="p-2 space-y-1.5">
              {daemonSessions.map((s) => {
                const agentName = s.session_id.replace(/^daemon_/, "");
                const isRunning = (s.active_runs ?? 0) > 0;
                return (
                  <button
                    key={s.session_id}
                    onClick={() => setSelected(s.session_id)}
                    className={`
                      w-full text-left px-3 py-2 rounded-lg border transition-all duration-150
                      ${selected === s.session_id
                        ? "border-violet-400/40 bg-violet-500/15 shadow-glow-sm"
                        : "border-violet-500/20 bg-violet-500/5 hover:bg-violet-500/10 hover:border-violet-500/30"
                      }
                    `}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${isRunning ? "bg-primary animate-pulse" : "bg-violet-400"}`} />
                        <span className="font-semibold text-sm text-foreground capitalize">{agentName}</span>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold uppercase tracking-wider ${
                          isRunning
                            ? "bg-primary/20 text-primary border border-primary/30"
                            : "bg-violet-500/15 text-violet-400/80 border border-violet-500/20"
                        }`}>
                          {isRunning ? "working" : "standby"}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                        <span>{s.source || "daemon"}</span>
                        <span className="text-muted">·</span>
                        <span>last activity {relativeAge(s.last_activity)} ago</span>
                      </div>
                    </div>
                    {s.description && (
                      <div className="mt-1 text-[10px] text-muted-foreground italic truncate pl-4">{s.description}</div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Active Sessions ── */}
        <div className="border border-primary/25 rounded-lg bg-primary/10 overflow-hidden">
          <div className="px-3 py-2 border-b border-primary/20 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
              </span>
              <span className="text-xs font-semibold text-primary uppercase tracking-wider">Active Sessions</span>
              <span className="text-[10px] text-primary/70">({activeSessions.length})</span>
            </div>
          </div>
          <div className="p-2 space-y-1.5">
            {activeSessions.length === 0 && (
              <div className="text-[11px] text-primary/60 text-center py-3 italic">No active sessions</div>
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

        {/* ── Channel-Grouped Inbox ── */}
        {CHANNEL_ORDER.map((chKey) => {
          const items = channelGroups[chKey];
          if (!items || items.length === 0) return null;
          const meta = CHANNEL_META[chKey];
          return (
            <div key={chKey} className={`border ${meta.border} rounded-lg ${meta.bg} overflow-hidden`}>
              <div className={`px-3 py-2 border-b ${meta.border} flex items-center justify-between`}>
                <div className="flex items-center gap-2">
                  <span className="text-xs">{meta.icon}</span>
                  <span className={`text-xs font-semibold ${meta.color} uppercase tracking-wider`}>{meta.label}</span>
                  <span className={`text-[10px] ${meta.color} opacity-70`}>({items.length})</span>
                </div>
              </div>
              <div className="p-2 space-y-1.5">
                {items.map((s) => (
                  <SessionCard
                    key={s.session_id}
                    session={s}
                    isSelected={selected === s.session_id}
                    onSelect={() => setSelected(s.session_id)}
                    onRehydrate={rehydrateSession}
                    rehydrating={rehydratingId === s.session_id}
                    isActive={isActiveSession(s)}
                    onChatWithSimone={chatWithSimone}
                    onGenerateDossier={generateDossier}
                    dossierGenerating={dossierGenerating === s.session_id}
                  />
                ))}
              </div>
            </div>
          );
        })}

        {/* ── Older Sessions (beyond 36h window) ── */}
        {olderSessions.length > 0 && (
          <div className="border border-border/40 rounded-lg bg-background/40 overflow-hidden">
            <button
              onClick={() => setShowOlderSessions(!showOlderSessions)}
              className="w-full px-3 py-2 border-b border-border/30 flex items-center justify-between hover:bg-card/30 transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Older Sessions</span>
                <span className="text-[10px] text-muted">({olderSessions.length})</span>
                {noiseCount > 0 && (
                  <span className="text-[9px] text-muted italic">+{noiseCount} hidden</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <label
                  className="flex items-center gap-1.5 text-[10px] text-muted-foreground cursor-pointer"
                  onClick={(e) => e.stopPropagation()}
                >
                  <input
                    type="checkbox"
                    checked={hideNoise}
                    onChange={(e) => setHideNoise(e.target.checked)}
                    className="rounded border-border bg-card w-3 h-3 accent-cyan-500"
                  />
                  Hide noise
                </label>
                <span className="text-muted text-xs">{showOlderSessions ? "▼" : "▶"}</span>
              </div>
            </button>

            {showOlderSessions && (
              <div className="p-2 space-y-1.5 max-h-[50vh] overflow-y-auto scrollbar-thin">
                {olderSessions.map((s) => (
                  <SessionCard
                    key={s.session_id}
                    session={s}
                    isSelected={selected === s.session_id}
                    onSelect={() => setSelected(s.session_id)}
                    onRehydrate={rehydrateSession}
                    rehydrating={rehydratingId === s.session_id}
                    isActive={false}
                    hidden={staleFilter && ageTier(s.last_activity || s.created_at) !== "stale"}
                    onChatWithSimone={chatWithSimone}
                    onGenerateDossier={generateDossier}
                    dossierGenerating={dossierGenerating === s.session_id}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {sessionsError && (
          <div className="rounded-md border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-[11px] text-accent">
            {sessionsError}
          </div>
        )}
      </div>

      {/* ── Right Column: Session Detail ── */}
      {selected && selectedSession && (
        <>
          {/* VP Observer Warning */}
          {isVpSelected && (
            <div className="rounded-md border border-red-400/25 bg-red-400/10 px-3 py-2 text-[10px] uppercase tracking-wider text-secondary">
              VP Observer Mode: view-only
            </div>
          )}

          {/* Session Actions */}
          <div className="border border-border/40 rounded-lg bg-background/50 p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-foreground/80 uppercase tracking-wider">Session Detail</span>
              <span className="text-[10px] text-muted-foreground font-mono">{selected}</span>
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
              <span className="text-muted-foreground">Created:</span>
              <span className="text-muted-foreground">{formatDateTimeTz(selectedSession.created_at, { placeholder: "—" })}</span>
              <AgeBadge isoDate={selectedSession.created_at} label="age" />
              <span className="text-muted">|</span>
              <span className="text-muted-foreground">Last activity:</span>
              <span className="text-muted-foreground">{relativeAge(selectedSession.last_activity)} ago</span>
            </div>

            {/* Description */}
            {selectedSession.description && (
              <div className="rounded-md bg-card/50 px-3 py-2 text-[11px] text-foreground/80 italic border border-border/30">
                &ldquo;{selectedSession.description}&rdquo;
              </div>
            )}

            {/* Action buttons */}
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={() => attachToChat(selected)}
                className="px-2.5 py-1 rounded-md border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 text-xs transition-all"
                disabled={attaching || (!selectedIsLiveSession && !selectedRunViewerId)}
              >
                {attaching ? "…" : isVpSelected ? "Observer Chat" : selectedIsLiveSession ? "Attach Chat (Tail)" : "Open Run Viewer"}
              </button>
              {/* Chat with Simone - context handoff */}
              <button
                onClick={() => chatWithSimone(selected)}
                className="px-2.5 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 text-xs transition-all font-semibold"
              >
                💬 Chat with Simone
              </button>
              {!isVpSelected && (
                <>
                  <button onClick={() => cancelSession(selected)} className="px-2.5 py-1 rounded-md border border-accent/25 bg-accent/10 text-accent hover:bg-accent/20 text-xs transition-all">Cancel Run</button>
                  <button onClick={() => archiveSession(selected)} className="px-2.5 py-1 rounded-md border border-sky-500/40 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 text-xs transition-all">Archive</button>
                  <button onClick={() => compactLogs(selected)} className="px-2.5 py-1 rounded-md border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 text-xs transition-all">Compact Logs</button>
                  <button onClick={() => resetSession(selected)} className="px-2.5 py-1 rounded-md border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 text-xs transition-all">Reset</button>
                  <button onClick={() => deleteSession(selected)} className="px-2.5 py-1 rounded-md border border-red-500/40 bg-red-500/10 text-red-400 hover:bg-red-500/20 text-xs transition-all">Delete</button>
                </>
              )}
            </div>

            {isVpSelected && (
              <div className="text-[10px] text-muted-foreground italic">VP sessions are view-only. Use Simone chat for changes.</div>
            )}
          </div>

          {/* Dossier Viewer */}
          <div className="border border-border/40 rounded-lg bg-background/50 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-foreground/80 uppercase tracking-wider">Context Brief</span>
              <div className="flex items-center gap-2">
                {selectedSession.has_context_brief ? (
                  <button
                    onClick={() => fetchDossier(selected)}
                    className="text-[10px] px-2 py-0.5 rounded-md border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 transition-all"
                    disabled={dossierLoading}
                  >
                    {dossierLoading ? "Loading…" : dossierContent ? "↻ Reload" : "📄 View Dossier"}
                  </button>
                ) : (
                  <button
                    onClick={() => generateDossier(selected)}
                    className="text-[10px] px-2 py-0.5 rounded-md border border-amber-500/25 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-all animate-pulse"
                    disabled={dossierGenerating === selected}
                  >
                    {dossierGenerating === selected ? "Generating…" : "⚡ Generate Dossier"}
                  </button>
                )}
              </div>
            </div>
            {dossierContent && (
              <div className="bg-black/20 rounded-md border border-border/40 p-3 max-h-[40vh] overflow-y-auto scrollbar-thin">
                <pre className="text-[10px] font-mono whitespace-pre-wrap text-foreground/80">{dossierContent}</pre>
              </div>
            )}
            {!dossierContent && !dossierLoading && (
              <div className="text-[10px] text-muted italic text-center py-2">
                {selectedSession.has_context_brief
                  ? "Click \"View Dossier\" to load the context brief."
                  : "No dossier available. Click \"Generate Dossier\" to create one."}
              </div>
            )}
          </div>

          {/* Rehydrate Status */}
          <RehydrateStatus session={selectedSession} onRehydrate={rehydrateSession} rehydrating={rehydratingId === selected} />

          {/* Log Tail */}
          <div className="border border-border/40 rounded-lg bg-background/50 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-foreground/80">run.log tail</span>
              <button
                onClick={() => setExpandLogTail(!expandLogTail)}
                className="text-[10px] px-1.5 py-0.5 rounded border border-border/60 bg-card/40 hover:bg-card/50/60 transition-all text-muted-foreground"
              >
                {expandLogTail ? "Compact" : "Expand"}
              </button>
            </div>
            <pre className={`text-[10px] font-mono whitespace-pre-wrap overflow-y-auto scrollbar-thin bg-black/30 p-2 rounded border border-border/60 ${expandLogTail ? "max-h-[46vh]" : "max-h-40"}`}>
              {logTail || (isVpSelected ? "(empty VP lane log)" : "(empty)")}
            </pre>
          </div>

          {/* Delivery Workflow */}
          <div className="border border-border/40 rounded-lg bg-background/50 p-3 space-y-2">
            <div className="text-xs font-semibold text-foreground/80">Delivery Workflow</div>
            <div className="text-[10px] text-muted-foreground">Choose how to handle this output: promote, iterate, or archive.</div>

            {deliveryLoading && <div className="text-[10px] text-muted">Loading thread…</div>}
            {selectedThread && (
              <div className="rounded-md border border-primary/30/40 bg-primary/10 px-2 py-1 text-[10px] text-primary/80">
                Current: <span className="font-semibold uppercase">{selectedThread.decision || "pending"}</span>
                {" · "}
                {selectedThread.updated_at ? formatDateTimeTz(selectedThread.updated_at) : "recent"}
                {selectedThread.patch_version ? ` · v${selectedThread.patch_version}` : ""}
              </div>
            )}
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => selected && recordDeliveryDecision(selected, "promote")} disabled={!selected || isVpSelected || deliveryDecisionBusy} className="px-2.5 py-1 rounded-md border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 text-xs disabled:opacity-40 transition-all">Promote Now</button>
              <button onClick={() => selected && recordDeliveryDecision(selected, "iterate")} disabled={!selected || deliveryDecisionBusy} className="px-2.5 py-1 rounded-md border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 text-xs disabled:opacity-40 transition-all">Open Iteration</button>
              <button onClick={() => selected && recordDeliveryDecision(selected, "archive")} disabled={!selected || isVpSelected || deliveryDecisionBusy} className="px-2.5 py-1 rounded-md border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 text-xs disabled:opacity-40 transition-all">Archive Draft</button>
            </div>
            <textarea
              value={deliveryNoteDraft}
              onChange={(e) => setDeliveryNoteDraft(e.target.value)}
              placeholder="Optional notes (acceptance criteria, follow-up asks, risk notes)"
              className="w-full rounded-md border border-border/60 bg-card/40 px-2 py-1.5 text-[11px] min-h-16 text-foreground/80 placeholder:text-muted focus:border-primary/25 focus:outline-none"
            />
            {deliveryStatus && (
              <div className="text-[10px] text-primary">{deliveryStatus}</div>
            )}
          </div>
        </>
      )}

      {/* No selection state */}
      {!selected && (
        <div className="flex items-center justify-center py-16 text-muted text-sm italic">
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
  hidden,
  onChatWithSimone,
  onGenerateDossier,
  dossierGenerating,
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
    has_context_brief?: boolean;
    checkpoint_tasks_completed?: number;
    checkpoint_artifacts_count?: number;
  };
  isSelected: boolean;
  onSelect: () => void;
  onRehydrate: (id: string) => void;
  rehydrating: boolean;
  isActive: boolean;
  hidden?: boolean;
  onChatWithSimone?: (id: string) => void;
  onGenerateDossier?: (id: string) => void;
  dossierGenerating?: boolean;
}) {
  if (hidden) return null;
  const tier = ageTier(s.created_at || s.last_modified);
  const chKey = (s.channel || "system") as ChannelKey;
  const chMeta = CHANNEL_META[chKey];

  return (
    <button
      onClick={onSelect}
      className={`
        w-full text-left px-3 py-2 rounded-lg border transition-all duration-150
        ${isSelected
          ? "border-primary/30 bg-primary/10 shadow-glow-sm"
          : "border-border/40 bg-card/20 hover:bg-card/40 hover:border-border/50"
        }
      `}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {/* Session ID */}
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[11px] text-foreground truncate">{shortSessionId(s.session_id)}</span>
            {s.has_context_brief && (
              <span className="text-[9px] shrink-0" title="Context brief available">📄</span>
            )}
          </div>

          {/* Description */}
          {s.description && (
            <div className="text-[10px] text-muted-foreground truncate mt-0.5 italic">{s.description}</div>
          )}

          {/* Meta row */}
          <div className="flex items-center gap-2 mt-1 text-[10px]">
            {/* Status badge */}
            {isActive ? (
              <span className="flex items-center gap-1 text-primary">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                  <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-primary" />
                </span>
                {s.status}
              </span>
            ) : (
              <span className="text-muted-foreground">{s.status}</span>
            )}
            <span className="text-muted">·</span>
            <span className={`${chMeta?.color || "text-muted-foreground"}`}>{chMeta?.icon} {s.channel || s.source || "local"}</span>
            <span className="text-muted">·</span>
            <span className="text-muted-foreground">{s.owner || "unknown"}</span>
          </div>

          {/* Inline action buttons for non-rehydratable completed sessions */}
          {!isActive && !s.rehydrate_ready && onChatWithSimone && (
            <div className="flex items-center gap-1.5 mt-1.5" onClick={(e) => e.stopPropagation()}>
              <button
                onClick={() => onChatWithSimone(s.session_id)}
                className="text-[9px] px-1.5 py-0.5 rounded border border-emerald-500/25 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-all"
              >
                💬 Chat
              </button>
              {!s.has_context_brief && onGenerateDossier && (
                <button
                  onClick={() => onGenerateDossier(s.session_id)}
                  className="text-[9px] px-1.5 py-0.5 rounded border border-amber-500/25 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-all"
                  disabled={dossierGenerating}
                >
                  {dossierGenerating ? "…" : "⚡ Dossier"}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Right side: age + rehydrate */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          <AgeBadge isoDate={s.created_at || s.last_modified} />

          <span className="text-[9px] text-muted">
            {relativeAge(s.last_activity)} ago
          </span>

          {/* Rehydrate indicator */}
          {!isActive && s.rehydrate_ready && (
            <button
              onClick={(e) => { e.stopPropagation(); onRehydrate(s.session_id); }}
              className="text-[9px] px-1.5 py-0.5 rounded border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 transition-all"
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
    emerald: "bg-primary/10 text-primary border-primary/15",
    red: "bg-red-500/10 text-red-400 border-red-500/20",
    slate: "bg-muted-foreground/10 text-muted-foreground border-muted-foreground/20",
    cyan: "bg-primary/10 text-primary border-primary/15",
    violet: "bg-secondary/10 text-secondary border-secondary/15",
    sky: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  };
  return (
    <div className={`rounded-md border px-2 py-1 ${colorMap[color] || colorMap.slate}`}>
      <div className="text-[9px] text-muted-foreground uppercase">{label}</div>
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
      <div className="rounded-lg border border-primary/30/40 bg-primary/10 px-3 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] text-primary">
          <span className="text-primary">✓</span>
          <span>Rehydrate ready ({(session.rehydrate_reason || "").replace(/_/g, " ")})</span>
          {session.has_checkpoint && session.checkpoint_tasks_completed != null && (
            <span className="text-primary/60">
              · {session.checkpoint_tasks_completed} tasks, {session.checkpoint_artifacts_count ?? 0} artifacts
            </span>
          )}
        </div>
        <button
          onClick={() => onRehydrate(session.session_id)}
          className="text-[10px] px-2 py-1 rounded-md border border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 transition-all"
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
      <div className="text-[11px] font-semibold text-accent">Cannot rehydrate</div>
      <ul className="list-disc list-inside text-[10px] text-amber-200/70 space-y-0.5">
        {parts.map((p) => <li key={p}>{hints[p] || p.replace(/_/g, " ")}</li>)}
      </ul>
    </div>
  );
}
