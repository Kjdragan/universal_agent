"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import SystemCommandBar from "@/components/dashboard/SystemCommandBar";

/* ─── API plumbing ─────────────────────────────────────────── */
const API_BASE = "/api/dashboard/gateway";

function buildHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("ops_token");
    if (token) headers["X-Ops-Token"] = token;
  }
  return headers;
}

/* ─── Types ────────────────────────────────────────────────── */
interface CalendarEvent {
  event_id: string;
  title: string;
  description?: string;
  source: string;
  status: string;
  scheduled_at_epoch: number;
  scheduled_at_local: string;
  label?: string;
  owner?: string;
  cron_expression?: string;
  actions?: string[];
  meta?: Record<string, unknown>;
}

interface CalendarStats {
  scheduled_today: number;
  active_cron: number;
  pending_tasks: number;
  overdue_tasks: number;
  next_event?: string;
}

interface CalendarFeedResponse {
  timezone: string;
  view: string;
  start_utc: string;
  end_utc: string;
  start_local: string;
  end_local: string;
  events: CalendarEvent[];
  always_running: CalendarEvent[];
  overdue: CalendarEvent[];
  stats: CalendarStats;
  stasis_queue: Array<{
    event_id?: string;
    status?: string;
    created_at?: string;
    event?: CalendarEvent;
  }>;
  legend: Record<string, string>;
}

type SourceFilter = "all" | "cron" | "heartbeat" | "task" | "overdue";
type ViewMode = "week" | "day";

/* ─── Date helpers ─────────────────────────────────────────── */
function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

function startOfWeek(d: Date): Date {
  const r = new Date(d);
  r.setDate(r.getDate() - r.getDay());
  r.setHours(0, 0, 0, 0);
  return r;
}

function formatDateKey(d: Date | string): string {
  const date = typeof d === "string" ? new Date(d) : d;
  return date.toISOString().slice(0, 10);
}

function formatTime(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function formatDayLabel(d: Date): string {
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function isToday(d: Date): boolean {
  const now = new Date();
  return formatDateKey(d) === formatDateKey(now);
}

/* ─── Status badge colors (Gruvbox-inspired) ───────────────── */
function sourceBadgeClasses(source: string, status: string): string {
  if (source === "task" && status === "overdue")
    return "border-red-500/60 bg-red-500/15 text-red-300";
  if (source === "task" && status === "active")
    return "border-accent/30 bg-accent/15 text-accent";
  if (source === "task" && status === "completed")
    return "border-primary/30 bg-primary/15 text-primary";
  if (source === "task")
    return "border-accent/30 bg-accent/10 text-accent/80";
  if (source === "heartbeat")
    return "border-sky-400/40 bg-sky-500/15 text-sky-200";
  if (status === "missed")
    return "border-amber-500/50 bg-amber-500/15 text-amber-300";
  if (status === "failed")
    return "border-red-400/30 bg-red-400/15 text-red-400";
  if (status === "success")
    return "border-primary/30 bg-primary/15 text-primary";
  if (status === "disabled")
    return "border-muted-foreground/50 bg-muted-foreground/15 text-foreground/80";
  return "border-primary/50 bg-primary/15 text-primary/80";
}

function sourceIcon(source: string): string {
  switch (source) {
    case "heartbeat": return "💓";
    case "cron": return "⏰";
    case "task": return "📋";
    default: return "📌";
  }
}

/* ─── Action button config per source ──────────────────────── */
interface ActionDef {
  action: string;
  icon: string;
  label: string;
  color: string;
  /** Only show when event.status matches one of these (empty = always) */
  showWhen?: string[];
  /** Hide when status matches */
  hideWhen?: string[];
}

const CRON_ACTIONS: ActionDef[] = [
  { action: "run_now", icon: "▶", label: "Run", color: "text-primary hover:bg-primary/20", hideWhen: ["disabled"] },
  { action: "pause", icon: "⏸", label: "Pause", color: "text-amber-300 hover:bg-amber-500/20", hideWhen: ["disabled"] },
  { action: "resume", icon: "▶", label: "Resume", color: "text-primary hover:bg-primary/20", showWhen: ["disabled"] },
  { action: "disable", icon: "⏻", label: "Disable", color: "text-muted-foreground hover:bg-muted-foreground/20", hideWhen: ["disabled"] },
  { action: "update_category", icon: "✏️", label: "Edit Description", color: "text-primary hover:bg-primary/20" },
  { action: "open_logs", icon: "📋", label: "Logs", color: "text-primary hover:bg-primary/20" },
  { action: "open_session", icon: "💬", label: "Session", color: "text-primary hover:bg-primary/20" },
];

const HEARTBEAT_ACTIONS: ActionDef[] = [
  { action: "open_logs", icon: "📋", label: "Logs", color: "text-primary hover:bg-primary/20" },
  { action: "open_session", icon: "💬", label: "Session", color: "text-primary hover:bg-primary/20" },
  { action: "delete", icon: "✕", label: "Remove", color: "text-red-400 hover:bg-red-400/20" },
];

const TASK_ACTIONS: ActionDef[] = [
  { action: "complete", icon: "✓", label: "Complete", color: "text-primary hover:bg-primary/20", hideWhen: ["completed"] },
  { action: "prod_agent", icon: "⚡", label: "Nudge", color: "text-accent hover:bg-accent/20", showWhen: ["overdue", "active"] },
  { action: "reschedule", icon: "↻", label: "Reschedule", color: "text-amber-300 hover:bg-amber-500/20", hideWhen: ["completed"] },
  { action: "dismiss", icon: "✕", label: "Dismiss", color: "text-muted-foreground hover:bg-muted-foreground/20", hideWhen: ["completed"] },
  { action: "reopen", icon: "↺", label: "Reopen", color: "text-accent hover:bg-accent/20", showWhen: ["completed"] },
];

function getActionsForEvent(event: CalendarEvent): ActionDef[] {
  let defs: ActionDef[];
  switch (event.source) {
    case "cron": defs = CRON_ACTIONS; break;
    case "heartbeat": defs = HEARTBEAT_ACTIONS; break;
    case "task": defs = TASK_ACTIONS; break;
    default: defs = []; break;
  }
  return defs.filter((d) => {
    if (d.showWhen && d.showWhen.length > 0 && !d.showWhen.includes(event.status)) return false;
    if (d.hideWhen && d.hideWhen.includes(event.status)) return false;
    return true;
  });
}

/* ─── Toast notification ───────────────────────────────────── */
function Toast({ message, type, onClose }: { message: string; type: "success" | "error" | "info"; onClose: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3500);
    return () => clearTimeout(timer);
  }, [onClose]);

  const colors = {
    success: "bg-primary/20 border-primary/25 text-primary/80",
    error: "bg-red-400/20 border-red-400/25 text-red-400/80",
    info: "bg-primary/20 border-primary/40 text-primary/80",
  };

  return (
    <div className={`fixed top-4 right-4 z-50 rounded-lg border px-4 py-2.5 text-xs font-medium shadow-lg backdrop-blur-md animate-in slide-in-from-top-2 duration-200 ${colors[type]}`}>
      {message}
    </div>
  );
}

/* ─── Stat Card ────────────────────────────────────────────── */
function StatCard({
  label,
  value,
  accent,
  icon,
  pulse,
}: {
  label: string;
  value: number | string;
  accent: string;
  icon: string;
  pulse?: boolean;
}) {
  return (
    <div className={`tactical-panel rounded-lg p-3 flex items-center gap-3 min-w-0 transition-all hover:scale-[1.02]`}>
      <div className={`text-xl ${pulse ? "animate-pulse" : ""}`}>{icon}</div>
      <div className="min-w-0">
        <div className={`text-xl font-bold tabular-nums ${accent}`}>{value}</div>
        <div className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium truncate">{label}</div>
      </div>
    </div>
  );
}

/* ─── Event Card ───────────────────────────────────────────── */
function EventCard({
  event,
  onAction,
  compact,
}: {
  event: CalendarEvent;
  onAction: (event: CalendarEvent, action: string) => void;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const badge = sourceBadgeClasses(event.source, event.status);
  const actions = getActionsForEvent(event);

  // Close on Escape key
  useEffect(() => {
    if (!expanded) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [expanded]);

  return (
    <>
      {/* Collapsed card */}
      <div
        className={`rounded-lg border ${badge} p-3 transition-all hover:brightness-110 cursor-pointer ${compact ? "p-2" : ""}`}
        onClick={() => setExpanded(true)}
      >
        <div className="flex items-start gap-2">
          <span className="text-sm mt-0.5">{sourceIcon(event.source)}</span>
          <div className="min-w-0 flex-1">
            <div className={`font-semibold truncate ${compact ? "text-xs" : "text-sm"}`}>
              {event.title}
            </div>
            <div className="flex items-center gap-2 text-[10px] opacity-80 mt-0.5">
              <span>{formatTime(event.scheduled_at_local)}</span>
              <span className={`px-1.5 py-0.5 rounded-full text-[9px] font-medium uppercase tracking-wider ${
                event.status === "overdue" ? "bg-red-500/30 text-red-200" :
                event.status === "success" ? "bg-primary/30 text-primary/80" :
                event.status === "failed" ? "bg-red-400/30 text-red-400/80" :
                event.status === "missed" ? "bg-amber-500/30 text-amber-200" :
                "bg-white/10"
              }`}>
                {event.status}
              </span>
            </div>
            {/* Streamlined action buttons */}
            {actions.length > 0 && (
              <div className="flex items-center gap-0.5 mt-1.5">
                {actions.map((def) => (
                  <button
                    key={`${event.event_id}-${def.action}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onAction(event, def.action);
                    }}
                    title={def.label}
                    className={`px-1.5 py-0.5 rounded text-[10px] transition-colors border border-transparent ${def.color}`}
                  >
                    {compact ? def.icon : `${def.icon} ${def.label}`}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Expanded fly-out overlay */}
      {expanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-150"
          onClick={() => setExpanded(false)}
        >
          <div
            className={`relative w-full max-w-lg mx-4 rounded-xl border ${badge} bg-card/95 backdrop-blur-lg shadow-2xl p-5 animate-in zoom-in-95 slide-in-from-bottom-2 duration-200`}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close button */}
            <button
              onClick={() => setExpanded(false)}
              className="absolute top-3 right-3 text-muted-foreground hover:text-foreground transition-colors text-lg leading-none"
              title="Close (Esc)"
            >
              ✕
            </button>

            {/* Header */}
            <div className="flex items-start gap-3 mb-4">
              <span className="text-xl mt-0.5">{sourceIcon(event.source)}</span>
              <div className="min-w-0 flex-1 pr-6">
                <div className="font-bold text-base text-foreground leading-snug">
                  {event.title}
                </div>
                <div className="flex items-center gap-2 mt-1.5">
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                    event.status === "overdue" ? "bg-red-500/30 text-red-200" :
                    event.status === "success" ? "bg-primary/30 text-primary/80" :
                    event.status === "failed" ? "bg-red-400/30 text-red-400/80" :
                    event.status === "missed" ? "bg-amber-500/30 text-amber-200" :
                    event.status === "running" ? "bg-sky-500/30 text-sky-200" :
                    "bg-white/10 text-foreground/60"
                  }`}>
                    {event.status}
                  </span>
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                    {event.source}
                  </span>
                </div>
              </div>
            </div>

            {/* Detail rows */}
            <div className="space-y-2.5 text-sm">
              {/* Scheduled time */}
              <div className="flex items-start gap-2">
                <span className="text-muted-foreground text-[10px] uppercase tracking-wider font-semibold w-20 shrink-0 mt-0.5">Scheduled</span>
                <span className="text-foreground/90 font-mono text-xs">
                  {new Date(event.scheduled_at_local).toLocaleString(undefined, {
                    weekday: "short",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                    hour12: true,
                  })}
                </span>
              </div>

              {/* Description / Command (full, untruncated) */}
              {event.description && (
                <div className="flex items-start gap-2">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider font-semibold w-20 shrink-0 mt-0.5">Command</span>
                  <div className="text-foreground/80 text-xs leading-relaxed whitespace-pre-wrap break-words max-h-48 overflow-y-auto scrollbar-thin">
                    {event.description || event.title}
                  </div>
                </div>
              )}

              {/* Cron expression */}
              {event.cron_expression && (
                <div className="flex items-start gap-2">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider font-semibold w-20 shrink-0 mt-0.5">Cron</span>
                  <span className="text-foreground/80 font-mono text-xs bg-white/5 px-2 py-0.5 rounded">
                    {event.cron_expression}
                  </span>
                </div>
              )}

              {/* Owner */}
              {event.owner && (
                <div className="flex items-start gap-2">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider font-semibold w-20 shrink-0 mt-0.5">Owner</span>
                  <span className="text-foreground/80 text-xs">{event.owner}</span>
                </div>
              )}

              {/* Label */}
              {event.label && (
                <div className="flex items-start gap-2">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider font-semibold w-20 shrink-0 mt-0.5">Label</span>
                  <span className="text-foreground/80 text-xs">{event.label}</span>
                </div>
              )}

              {/* Meta fields */}
              {event.meta && Object.keys(event.meta).length > 0 && (
                <div className="flex items-start gap-2">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider font-semibold w-20 shrink-0 mt-0.5">Meta</span>
                  <div className="flex-1 text-[11px] text-foreground/70 font-mono bg-white/5 rounded p-2 max-h-32 overflow-y-auto scrollbar-thin">
                    {Object.entries(event.meta).map(([k, v]) => (
                      <div key={k} className="flex gap-1.5">
                        <span className="text-muted-foreground">{k}:</span>
                        <span className="break-all">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Actions */}
            {actions.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 mt-4 pt-3 border-t border-white/10">
                {actions.map((def) => (
                  <button
                    key={`${event.event_id}-expanded-${def.action}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onAction(event, def.action);
                      setExpanded(false);
                    }}
                    title={def.label}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border border-white/10 ${def.color}`}
                  >
                    {def.icon} {def.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/* ─── Overdue Alert Card ───────────────────────────────────── */
function OverdueCard({
  event,
  onAction,
}: {
  event: CalendarEvent;
  onAction: (event: CalendarEvent, action: string) => void;
}) {
  const dueDate = new Date(event.scheduled_at_local);
  const now = new Date();
  const ageDays = Math.floor((now.getTime() - dueDate.getTime()) / (1000 * 60 * 60 * 24));
  const ageLabel = ageDays === 0 ? "today" : ageDays === 1 ? "1 day ago" : `${ageDays} days ago`;

  return (
    <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-3 transition-all hover:bg-red-500/15">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-sm text-red-200 truncate">{event.title}</div>
          <div className="text-[10px] text-red-300/80 mt-0.5">
            Due: {formatTime(event.scheduled_at_local)} •{" "}
            <span className={`font-bold ${ageDays >= 3 ? "text-red-400" : ageDays >= 1 ? "text-accent" : "text-accent"}`}>
              {ageLabel}
            </span>
          </div>
        </div>
        <span className={`shrink-0 px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider ${
          ageDays >= 3 ? "bg-red-500/40 text-red-100" :
          ageDays >= 1 ? "bg-accent/15 text-accent/80" :
          "bg-accent/25 text-accent/80"
        }`}>
          {ageLabel}
        </span>
      </div>
      <div className="flex flex-wrap gap-1 mt-2">
        {(event.actions || []).map((action) => (
          <button
            key={action}
            onClick={() => onAction(event, action)}
            className="px-2 py-0.5 rounded border border-red-400/30 bg-red-500/10 hover:bg-red-500/25 text-[9px] font-medium uppercase tracking-wider text-red-200 transition-colors"
          >
            {action.replace(/_/g, " ")}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN COMPONENT
   ═══════════════════════════════════════════════════════════════ */
export default function CalendarPage() {
  /* ─── State ─────────────────────────────── */
  const [view, setView] = useState<ViewMode>("week");
  const [anchorDate, setAnchorDate] = useState<Date>(new Date());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [alwaysRunning, setAlwaysRunning] = useState<CalendarEvent[]>([]);
  const [overdue, setOverdue] = useState<CalendarEvent[]>([]);
  const [stats, setStats] = useState<CalendarStats>({
    scheduled_today: 0,
    active_cron: 0,
    pending_tasks: 0,
    overdue_tasks: 0,
  });
  const [stasisQueue, setStasisQueue] = useState<CalendarFeedResponse["stasis_queue"]>([]);
  const [tz, setTz] = useState("America/Chicago");
  const [showCommandBar, setShowCommandBar] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);
  const [nudging, setNudging] = useState(false);
  const [checkedCategories, setCheckedCategories] = useState<Set<string>>(new Set());
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);

  /* ─── Computed date range ────────────────── */
  const range = useMemo(() => {
    if (view === "day") {
      const start = new Date(anchorDate);
      start.setHours(0, 0, 0, 0);
      return { start, end: addDays(start, 1) };
    }
    const start = startOfWeek(anchorDate);
    return { start, end: addDays(start, 7) };
  }, [anchorDate, view]);

  /* ─── Fetch calendar data ────────────────── */
  const fetchCalendar = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Chicago";
      const params = new URLSearchParams({
        view,
        start: range.start.toISOString(),
        end: range.end.toISOString(),
        source: "all",
        timezone_name: browserTz,
      });
      const r = await fetch(`${API_BASE}/api/v1/ops/calendar/events?${params.toString()}`, {
        headers: buildHeaders(),
      });
      if (!r.ok) throw new Error(await r.text() || `Calendar load failed (${r.status})`);
      const data = (await r.json()) as CalendarFeedResponse;
      setEvents(data.events || []);
      setAlwaysRunning(data.always_running || []);
      setOverdue(data.overdue || []);
      setStats(data.stats || { scheduled_today: 0, active_cron: 0, pending_tasks: 0, overdue_tasks: 0 });
      setStasisQueue(data.stasis_queue || []);
      setTz(data.timezone || browserTz);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [range.start, range.end, view]);

  const fetchPreferences = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/preferences`, { headers: buildHeaders() });
      if (r.ok) {
        const data = await r.json();
        const categories = data.preferences?.calendar_checked_categories;
        if (Array.isArray(categories)) {
          setCheckedCategories(new Set(categories));
        }
      }
    } catch (e) {
      console.error("Failed to load preferences", e);
    } finally {
      setPreferencesLoaded(true);
    }
  }, []);

  const toggleCategory = useCallback(async (category: string) => {
    setCheckedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      
      const arr = Array.from(next);
      // Fire-and-forget sync to backend
      fetch(`${API_BASE}/api/v1/ops/preferences`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
        body: JSON.stringify({ preferences: { calendar_checked_categories: arr } })
      }).catch(e => console.error("Failed to save preference", e));

      return next;
    });
  }, []);

  useEffect(() => {
    fetchPreferences();
  }, [fetchPreferences]);

  useEffect(() => {
    if (!preferencesLoaded) return;
    fetchCalendar();
  }, [fetchCalendar, preferencesLoaded]);

  // Auto-poll every 30 seconds
  useEffect(() => {
    const timer = setInterval(fetchCalendar, 30000);
    return () => clearInterval(timer);
  }, [fetchCalendar]);

  /* ─── Action handler (smart: handles navigation, prompts, toasts) ── */
  const performAction = useCallback(async (event: CalendarEvent, action: string) => {
    try {
      // Pre-action: handle actions that need user input
      const payload: Record<string, unknown> = { action };
      if (action === "reschedule") {
        const requested = prompt("Reschedule for when? (e.g. 'in 30m', 'tomorrow 9am', or ISO timestamp)");
        if (!requested || !requested.trim()) return;
        payload.run_at = requested.trim();
      } else if (action === "update_category") {
        const requested = prompt("Enter new description for this job:");
        if (requested === null) return;
        payload.note = requested.trim();
      }

      const r = await fetch(
        `${API_BASE}/api/v1/ops/calendar/events/${encodeURIComponent(event.event_id)}/action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...buildHeaders() },
          body: JSON.stringify(payload),
        }
      );
      if (!r.ok) {
        const txt = await r.text();
        setToast({ message: `Action failed: ${txt || r.statusText}`, type: "error" });
        return;
      }
      const data = await r.json();

      // Post-action: handle response-dependent navigation
      if (action === "open_logs" && data.path) {
        const href = String(data.path);
        const full = href.startsWith("http") ? href : `${API_BASE}${href.startsWith("/") ? "" : "/"}${href}`;
        window.open(full, "_blank", "noopener,noreferrer");
        setToast({ message: "Opening logs…", type: "info" });
        return;
      }
      if (action === "open_session") {
        const sid = String(data.session_id || "");
        if (sid) {
          // Navigate to the sessions page with the session pre-selected
          window.location.href = `/dashboard/sessions?sid=${encodeURIComponent(sid)}`;
          setToast({ message: `Opening session ${sid.slice(0, 12)}…`, type: "info" });
        } else {
          setToast({ message: "No session found for this event", type: "error" });
        }
        return;
      }

      // Success toast for mutating actions
      const labels: Record<string, string> = {
        run_now: "Job triggered",
        pause: "Job paused",
        resume: "Job resumed",
        disable: "Job disabled",
        delete: "Entry removed",
        delete_missed: "Missed event dismissed",
        approve_backfill_run: "Backfill approved & running",
        reschedule: "Rescheduled successfully",
        complete: "Task completed",
        dismiss: "Task dismissed",
        reopen: "Task reopened",
        prod_agent: "Agent nudged — heartbeats prodded",
        update_category: "Category updated",
      };
      setToast({ message: labels[action] || `Action '${action}' completed`, type: "success" });
      await fetchCalendar();
    } catch (e) {
      setToast({ message: `Error: ${(e as Error).message}`, type: "error" });
    }
  }, [fetchCalendar]);

  /* ─── Nudge All Overdue handler ──────────── */
  const nudgeAllOverdue = useCallback(async () => {
    setNudging(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/calendar/nudge-overdue`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildHeaders() },
      });
      if (!r.ok) {
        const txt = await r.text();
        setToast({ message: `Nudge failed: ${txt || r.statusText}`, type: "error" });
        return;
      }
      const data = await r.json();
      setToast({
        message: `Nudged ${data.prodded_count || 0} overdue task(s)`,
        type: data.prodded_count > 0 ? "success" : "info",
      });
      await fetchCalendar();
    } catch (e) {
      setToast({ message: `Error: ${(e as Error).message}`, type: "error" });
    } finally {
      setNudging(false);
    }
  }, [fetchCalendar]);

  /* ─── Compute day columns ────────────────── */
  const weekDays = useMemo(() => {
    if (view === "day") return [new Date(range.start)];
    return Array.from({ length: 7 }, (_, i) => addDays(range.start, i));
  }, [range.start, view]);

  const eventsByDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const day of weekDays) {
      map.set(formatDateKey(day), []);
    }
    for (const event of events) {
      if (event.source === "heartbeat" && !checkedCategories.has("Heartbeats")) {
        continue;
      }
      if (event.source === "cron") {
        const cat = event.title || event.description || "Unknown Cron";
        if (!checkedCategories.has(cat)) continue;
      }
      const key = formatDateKey(event.scheduled_at_local);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(event);
    }
    for (const [, bucket] of map) {
      bucket.sort((a, b) => a.scheduled_at_epoch - b.scheduled_at_epoch);
    }
    return map;
  }, [events, weekDays, checkedCategories]);

  const filterCategories = useMemo(() => {
    const cats = new Set<string>();
    cats.add("Heartbeats");
    for (const e of events) {
      if (e.source === "cron") {
        cats.add(e.title || e.description || "Unknown Cron");
      }
    }
    return Array.from(cats).sort((a, b) => {
      // Heartbeats at the bottom
      if (a === "Heartbeats") return 1;
      if (b === "Heartbeats") return -1;
      return a.localeCompare(b);
    });
  }, [events]);

  /* ─── Navigation ─────────────────────────── */
  const shiftWindow = (days: number) => {
    setAnchorDate((prev) => addDays(prev, days));
  };

  const rangeLabel = view === "day"
    ? formatDayLabel(range.start)
    : `${formatDayLabel(range.start)} — ${formatDayLabel(addDays(range.end, -1))}`;

  /* ═══ RENDER ═══════════════════════════════ */
  return (
    <>
    {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    <div className="flex h-full flex-col space-y-4 p-1">
      {/* ─── HEADER ──────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            <span className="text-primary">⬡</span> Calendar
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Unified scheduling — Cron · Heartbeat · Tasks · Overdue
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Navigation */}
          <div className="flex items-center gap-1 tactical-panel rounded-lg px-1 py-0.5">
            <button
              onClick={() => shiftWindow(view === "day" ? -1 : -7)}
              className="px-2.5 py-1 rounded hover:bg-white/10 text-foreground/80 transition-colors"
              title="Previous"
            >
              ◀
            </button>
            <button
              onClick={() => setAnchorDate(new Date())}
              className="px-3 py-1 rounded hover:bg-white/10 text-xs font-semibold text-primary transition-colors"
            >
              Today
            </button>
            <button
              onClick={() => shiftWindow(view === "day" ? 1 : 7)}
              className="px-2.5 py-1 rounded hover:bg-white/10 text-foreground/80 transition-colors"
              title="Next"
            >
              ▶
            </button>
          </div>

          {/* View toggle */}
          <div className="flex items-center tactical-panel rounded-lg overflow-hidden">
            {(["day", "week"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                  view === v
                    ? "bg-primary/20 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-white/5"
                }`}
              >
                {v}
              </button>
            ))}
          </div>

          {/* Refresh */}
          <button
            onClick={fetchCalendar}
            disabled={loading}
            className="tactical-panel rounded-lg px-3 py-1.5 text-xs font-semibold text-foreground/80 hover:text-primary transition-colors disabled:opacity-50"
          >
            {loading ? "⟳" : "↻"} Refresh
          </button>

          {/* Schedule button */}
          <button
            onClick={() => setShowCommandBar(!showCommandBar)}
            className={`rounded-lg px-3 py-1.5 text-xs font-bold uppercase tracking-wider transition-all ${
              showCommandBar
                ? "bg-primary/25 text-primary border border-primary/40"
                : "tactical-panel text-foreground/80 hover:text-primary"
            }`}
          >
            + Schedule
          </button>
        </div>
      </div>

      {/* ─── COMMAND BAR (collapsible) ────────── */}
      {showCommandBar && (
        <div className="tactical-panel rounded-lg p-3 animate-in slide-in-from-top-2 duration-200">
          <div className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold mb-2">
            Schedule a new task, cron job, or reminder
          </div>
          <SystemCommandBar sourcePage="calendar" onSuccess={fetchCalendar} />
        </div>
      )}

      {/* ─── DATE RANGE + TIMEZONE ────────────── */}
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">{rangeLabel}</span>
        <span className="font-mono text-[10px]">{tz}</span>
      </div>


      {error && (
        <div className="tactical-panel rounded-lg p-3 border-red-500/40 text-red-300 text-xs">
          ⚠️ {error}
        </div>
      )}

      {/* ─── LEGEND TOOLBAR ───────────── */}
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-sky-400" />heartbeat</span>
          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-primary" />cron</span>
          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-accent" />task</span>
          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-primary" />success</span>
          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-400" />failed</span>
        </div>
      </div>

      {/* ─── MAIN CONTENT AREA ───────────────── */}
      <div className="flex-1 min-h-0 flex gap-4 overflow-hidden">
        {/* LEFT: Day columns / Timeline */}
        <div className="flex-1 min-w-0 overflow-y-auto scrollbar-thin">
          <div className={`grid gap-3 ${view === "day" ? "grid-cols-1" : "grid-cols-7"}`}>
            {weekDays.map((day) => {
              const key = formatDateKey(day);
              const bucket = eventsByDay.get(key) || [];
              const today = isToday(day);

              return (
                <div
                  key={key}
                  className={`rounded-lg border transition-colors min-h-[200px] ${
                    today
                      ? "border-primary/40 bg-primary/5"
                      : "border-border/40 bg-card/30"
                  }`}
                >
                  {/* Day header */}
                  <div className={`px-3 py-2 border-b ${
                    today ? "border-primary/30 bg-primary/10" : "border-border/20"
                  }`}>
                    <div className={`text-xs font-bold ${today ? "text-primary" : "text-foreground/80"}`}>
                      {day.toLocaleDateString(undefined, { weekday: "short" })}
                    </div>
                    <div className={`text-lg font-bold tabular-nums ${today ? "text-primary" : "text-foreground"}`}>
                      {day.getDate()}
                    </div>
                    {bucket.length > 0 && (
                      <div className="text-[9px] text-muted-foreground mt-0.5">
                        {bucket.length} event{bucket.length !== 1 ? "s" : ""}
                      </div>
                    )}
                  </div>

                  {/* Event list */}
                  <div className="p-2 space-y-1.5">
                    {bucket.length === 0 && (
                      <div className="text-[10px] text-muted text-center py-4">No events</div>
                    )}
                    {bucket.map((event) => (
                      <EventCard
                        key={event.event_id}
                        event={event}
                        onAction={performAction}
                        compact={view === "week"}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* RIGHT: Sidebar */}
        <div className="hidden lg:flex flex-col w-80 shrink-0 gap-3 overflow-y-auto scrollbar-thin">
          {/* Stats List */}
          <div className="flex flex-col gap-2">
            <StatCard
              label="Scheduled Today"
              value={stats.scheduled_today}
              accent="text-primary"
              icon="📅"
            />
            <StatCard
              label="Active Cron"
              value={stats.active_cron}
              accent="text-primary"
              icon="⏰"
            />
            <StatCard
              label="Pending Tasks"
              value={stats.pending_tasks}
              accent="text-accent"
              icon="📋"
            />
            <StatCard
              label="Overdue"
              value={stats.overdue_tasks}
              accent={stats.overdue_tasks > 0 ? "text-red-400" : "text-primary"}
              icon={stats.overdue_tasks > 0 ? "🔴" : "✅"}
              pulse={stats.overdue_tasks > 0}
            />
          </div>

          {/* Event Visibility Toggle List */}
          {filterCategories.length > 0 && (
            <div className="tactical-panel rounded-lg p-3 space-y-2">
              <div className="text-xs font-bold text-primary uppercase tracking-wider flex items-center justify-between">
                <span>👁️ Event Visibility</span>
              </div>
              <div className="text-[10px] text-muted-foreground mb-2">
                Toggle categories to show/hide in the calendar view
              </div>
              <div className="space-y-1 max-h-64 overflow-y-auto scrollbar-thin">
                {filterCategories.map((cat) => {
                  const isChecked = checkedCategories.has(cat);
                  return (
                    <div
                      key={cat}
                      onClick={() => toggleCategory(cat)}
                      className={`flex items-center justify-between rounded border px-2 py-1.5 cursor-pointer transition-colors ${
                        !isChecked 
                          ? "border-muted/20 bg-muted/5 hover:bg-muted/10 opacity-60" 
                          : "border-primary/20 bg-primary/5 hover:bg-primary/10"
                      }`}
                    >
                      <div className="min-w-0 flex-1 flex flex-col">
                        <span className={`text-[11px] truncate font-medium ${!isChecked ? "text-muted-foreground line-through" : "text-primary"}`}>
                          {cat}
                        </span>
                      </div>
                      <div className={`ml-2 shrink-0 w-6 h-3 rounded-full flex items-center p-0.5 transition-colors ${!isChecked ? "bg-muted-foreground/30" : "bg-primary"}`}>
                        <div className={`w-2 h-2 rounded-full bg-white transition-transform ${!isChecked ? "translate-x-0" : "translate-x-3"}`} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {/* Overdue Panel */}
          {overdue.length > 0 && (
            <div className="tactical-panel rounded-lg p-3 border-red-500/30 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-red-400 font-bold text-sm">🔴 Overdue Tasks</span>
                  <span className="px-2 py-0.5 rounded-full bg-red-500/25 text-red-300 text-[10px] font-bold">
                    {overdue.length}
                  </span>
                </div>
                <button
                  onClick={nudgeAllOverdue}
                  disabled={nudging}
                  className="px-2 py-1 rounded-md border border-accent/25 bg-accent/10 hover:bg-accent/25 text-[10px] font-bold uppercase tracking-wider text-accent/80 transition-all disabled:opacity-50"
                  title="Prod all overdue agent-ready tasks via heartbeat"
                >
                  {nudging ? "⟳ Nudging…" : "⚡ Nudge All"}
                </button>
              </div>
              <div className="space-y-1.5">
                {overdue.map((evt) => (
                  <OverdueCard key={evt.event_id} event={evt} onAction={performAction} />
                ))}
              </div>
            </div>
          )}

          {/* Always Running */}
          <div className="tactical-panel rounded-lg p-3 space-y-2">
            <div className="text-xs font-bold text-sky-300 uppercase tracking-wider">
              💓 Always Running
            </div>
            {alwaysRunning.length === 0 && (
              <div className="text-[10px] text-muted-foreground">No always-running entries</div>
            )}
            <div className="space-y-1">
              {alwaysRunning.map((item) => (
                <div
                  key={item.event_id}
                  className="flex items-center justify-between rounded border border-sky-400/20 bg-sky-500/5 px-2 py-1.5"
                >
                  <span className="text-xs text-sky-200 truncate">{item.title}</span>
                  <button
                    onClick={() => performAction(item, "delete")}
                    className="ml-2 shrink-0 px-1.5 py-0.5 rounded text-[9px] text-sky-400 hover:bg-sky-500/20 transition-colors"
                    title="Disable heartbeat delivery"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Stasis Queue */}
          {stasisQueue.length > 0 && (
            <div className="tactical-panel rounded-lg p-3 space-y-2">
              <div className="text-xs font-bold text-amber-300 uppercase tracking-wider">
                ⚡ Missed Event Stasis
              </div>
              <div className="space-y-1.5">
                {stasisQueue.map((entry) => {
                  const ev = entry.event;
                  if (!ev) return null;
                  return (
                    <div
                      key={entry.event_id || ev.event_id}
                      className="rounded border border-amber-500/30 bg-amber-500/5 p-2"
                    >
                      <div className="font-semibold text-xs text-amber-200 truncate">{ev.title}</div>
                      <div className="text-[9px] text-amber-300/80 mt-0.5">
                        missed at {formatTime(ev.scheduled_at_local)} • {entry.status || "pending"}
                      </div>
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        <button
                          onClick={() => performAction(ev, "approve_backfill_run")}
                          className="px-1.5 py-0.5 rounded text-[9px] border border-amber-500/30 hover:bg-amber-500/15 text-amber-200 transition-colors"
                        >
                          Approve & Run
                        </button>
                        <button
                          onClick={() => performAction(ev, "reschedule")}
                          className="px-1.5 py-0.5 rounded text-[9px] border border-amber-500/30 hover:bg-amber-500/15 text-amber-200 transition-colors"
                        >
                          Reschedule
                        </button>
                        <button
                          onClick={() => performAction(ev, "delete_missed")}
                          className="px-1.5 py-0.5 rounded text-[9px] border border-amber-500/30 hover:bg-amber-500/15 text-amber-200 transition-colors"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Next Event */}
          {stats.next_event && (
            <div className="tactical-panel rounded-lg p-3">
              <div className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold mb-1">
                Next Event
              </div>
              <div className="text-sm text-primary font-semibold">{stats.next_event}</div>
            </div>
          )}
        </div>
      </div>
    </div>
    </>
  );
}
