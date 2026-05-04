"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";
const PRESET_CACHE_KEY = "ua.dashboard.events.presets.v1";
const FILTER_PREFS_KEY = "ua.dashboard.events.filterPrefs.v1";

const DEFAULT_CHECKED_SOURCES = ["csi", "tutorial", "continuity", "simone"];

type ActivityAction = {
  id?: string;
  label?: string;
  type?: string;
  href?: string;
};

type ActivityEvent = {
  id: string;
  event_class: string;
  source_domain: string;
  kind: string;
  title: string;
  summary: string;
  full_message: string;
  severity: string;
  status: string;
  requires_action: boolean;
  created_at_utc: string;
  updated_at_utc?: string;
  session_id?: string | null;
  entity_ref?: Record<string, unknown>;
  actions?: ActivityAction[];
  metadata?: Record<string, unknown>;
  // Phase 7: server-annotated smart title + visibility hint. Frontend
  // prefers smart_title over title when present; uses hide_by_default
  // to drive the default operator filter.
  smart_title?: string;
  hide_by_default?: boolean;
  title_template_source?: string;  // "code" | "fallback" | model name
};

type ActivityAuditRow = {
  id: number;
  event_id: string;
  action: string;
  actor: string;
  outcome: string;
  note?: string | null;
  created_at_utc: string;
  metadata?: Record<string, unknown>;
};

type DeliveryHealthRemediationStep = {
  code?: string;
  source?: string;
  title?: string;
  severity?: string;
  action?: string;
  runbook_command?: string;
  detail?: string;
};

type DeliveryHealthPanelState = {
  status: string;
  failingSources: string[];
  degradedSources: string[];
  steps: DeliveryHealthRemediationStep[];
  primaryRunbookCommand: string;
};

type EventPreset = {
  id: string;
  owner_id: string;
  name: string;
  filters: Record<string, unknown>;
  is_default: boolean;
  created_at_utc: string;
  updated_at_utc: string;
  last_used_at_utc?: string | null;
};

type SourceCounter = {
  unread: number;
  actionable: number;
  total: number;
};

type EventCounters = {
  totals: SourceCounter;
  by_source: Record<string, SourceCounter>;
};

type ActivityOpsMetrics = {
  uptime_seconds?: number;
  counters?: {
    events_sse_connects?: number;
    events_sse_disconnects?: number;
    events_sse_payloads?: number;
    events_sse_heartbeats?: number;
    events_sse_errors?: number;
    digest_compacted_total?: number;
    digest_immediate_bypass_total?: number;
    digest_buckets_open?: number;
  };
  retention_days?: {
    activity_events?: number;
    activity_stream?: number;
  };
  feature_flags?: {
    dashboard_events_sse_enabled?: boolean;
    activity_digest_enabled?: boolean;
  };
};

const SOURCE_STYLES: Record<string, string> = {
  csi: "bg-primary/10 text-primary border-primary/30",
  tutorial: "bg-secondary/10 text-secondary border-secondary/20",
  cron: "bg-accent/10 text-accent border-accent/30",
  continuity: "bg-accent/10 text-accent border-accent/30",
  heartbeat: "bg-primary/10 text-primary border-primary/20",
  simone: "bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/30",
  system: "bg-muted-foreground/10 text-foreground/80 border-muted-foreground/30",
};

const SEVERITY_STYLES: Record<string, string> = {
  success: "text-primary",
  error: "text-secondary",
  warning: "text-accent",
  info: "text-sky-300",
};

const SOURCE_ORDER = ["csi", "tutorial", "cron", "continuity", "heartbeat", "simone", "system"];

function emptyCounters(): EventCounters {
  const bySource: Record<string, SourceCounter> = {};
  for (const source of SOURCE_ORDER) {
    bySource[source] = { unread: 0, actionable: 0, total: 0 };
  }
  return {
    totals: { unread: 0, actionable: 0, total: 0 },
    by_source: bySource,
  };
}

function timeAgo(value: string): string {
  const ts = toEpochMs(value);
  if (ts === null) return "--";
  const deltaSeconds = Math.max(0, (Date.now() - ts) / 1000);
  if (deltaSeconds < 60) return "just now";
  if (deltaSeconds < 3600) return `${Math.floor(deltaSeconds / 60)}m ago`;
  if (deltaSeconds < 86400) return `${Math.floor(deltaSeconds / 3600)}h ago`;
  return `${Math.floor(deltaSeconds / 86400)}d ago`;
}

function sortEventsDesc(rows: ActivityEvent[]): ActivityEvent[] {
  return [...rows].sort((a, b) => {
    const at = toEpochMs(a.created_at_utc) ?? 0;
    const bt = toEpochMs(b.created_at_utc) ?? 0;
    if (bt !== at) return bt - at;
    return String(b.id).localeCompare(String(a.id));
  });
}

function loadPresetCache(): EventPreset[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PRESET_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as EventPreset[]) : [];
  } catch {
    return [];
  }
}

function savePresetCache(presets: EventPreset[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PRESET_CACHE_KEY, JSON.stringify(presets));
  } catch {
    // ignore local cache failures
  }
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item || "").trim())
    .filter(Boolean);
}

function parseDeliveryHealthPanelState(item: ActivityEvent | null): DeliveryHealthPanelState | null {
  if (!item) return null;
  const kind = String(item.kind || "").trim().toLowerCase();
  if (kind !== "csi_delivery_health_regression" && kind !== "csi_delivery_health_recovered") {
    return null;
  }
  const metadata = item.metadata && typeof item.metadata === "object" ? item.metadata : {};
  const status = String((metadata as Record<string, unknown>).delivery_health_status || "").trim().toLowerCase();
  const failingSources = asStringArray((metadata as Record<string, unknown>).failing_sources);
  const degradedSources = asStringArray((metadata as Record<string, unknown>).degraded_sources);
  const rawSteps = (metadata as Record<string, unknown>).remediation_steps;
  const steps = Array.isArray(rawSteps)
    ? rawSteps.filter((row): row is DeliveryHealthRemediationStep => typeof row === "object" && row !== null)
    : [];
  const primaryRunbookCommand = String((metadata as Record<string, unknown>).primary_runbook_command || "").trim();
  return {
    status: status || (kind.endsWith("_recovered") ? "ok" : "degraded"),
    failingSources,
    degradedSources,
    steps,
    primaryRunbookCommand,
  };
}

function canaryStatusClasses(status: string): string {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "failing") return "border-red-400/30 bg-red-400/10 text-red-400/80";
  if (normalized === "degraded") return "border-amber-700/60 bg-amber-950/30 text-amber-200";
  return "border-primary/30/60 bg-primary/10 text-primary/80";
}

function heartbeatMediationBadges(item: ActivityEvent | null): Array<{ label: string; classes: string }> {
  if (!item || item.source_domain !== "heartbeat" || !item.metadata || typeof item.metadata !== "object") return [];
  const metadata = item.metadata as Record<string, unknown>;
  const status = String(metadata.heartbeat_mediation_status || "").trim().toLowerCase();
  const badges: Array<{ label: string; classes: string }> = [];
  if (status === "dispatched") {
    badges.push({
      label: "Auto-triage dispatched",
      classes: "border-primary/30 bg-primary/10 text-primary/80",
    });
  } else if (status === "investigation_completed") {
    badges.push({
      label: "Investigation completed",
      classes: "border-primary/30/60 bg-primary/10 text-primary/80",
    });
  } else if (status === "dispatch_failed") {
    badges.push({
      label: "Auto-triage failed",
      classes: "border-red-400/30 bg-red-400/10 text-red-400/80",
    });
  } else if (status === "cooldown_active") {
    badges.push({
      label: "Dispatch cooling down",
      classes: "border-amber-700/60 bg-amber-950/30 text-amber-200",
    });
  }
  if (Boolean(metadata.heartbeat_operator_review_required)) {
    badges.push({
      label: "Operator review required",
      classes: "border-fuchsia-700/60 bg-fuchsia-950/30 text-fuchsia-200",
    });
  }
  return badges;
}

export default function DashboardEventsPage() {
  const [items, setItems] = useState<ActivityEvent[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [checkedSources, setCheckedSources] = useState<Set<string>>(() => {
    // Instant restore from localStorage while we wait for backend prefs
    if (typeof window !== "undefined") {
      try {
        const cached = window.localStorage.getItem(FILTER_PREFS_KEY);
        if (cached) {
          const parsed = JSON.parse(cached);
          if (Array.isArray(parsed.events_checked_sources) && parsed.events_checked_sources.length > 0) {
            return new Set(parsed.events_checked_sources as string[]);
          }
        }
      } catch { /* ignore */ }
    }
    return new Set(DEFAULT_CHECKED_SOURCES);
  });
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const [severityFilter, setSeverityFilter] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); return String(c.events_severity_filter || ""); } catch { /* */ }
    }
    return "";
  });
  const [statusFilter, setStatusFilter] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); return String(c.events_status_filter || ""); } catch { /* */ }
    }
    return "";
  });
  const [kindFilter, setKindFilter] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); return String(c.events_kind_filter || ""); } catch { /* */ }
    }
    return "";
  });
  const [timeWindow, setTimeWindow] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); const tw = String(c.events_time_window || ""); if (tw) return tw; } catch { /* */ }
    }
    return "7d";
  });
  const [actionableOnly, setActionableOnly] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); return Boolean(c.events_actionable_only); } catch { /* */ }
    }
    return false;
  });
  const [pinnedOnly, setPinnedOnly] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); return Boolean(c.events_pinned_only); } catch { /* */ }
    }
    return false;
  });
  const [hideTransient, setHideTransient] = useState(() => {
    if (typeof window !== "undefined") {
      try { const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}"); return Boolean(c.events_hide_transient); } catch { /* */ }
    }
    return false;
  });
  // Phase 7: "Show All Activity" toggle. Default OFF (hide routine
  // green/info noise). Sticky-per-user via localStorage with a 7-day
  // soft expiry — past 7 days the value resets to false so debugging
  // sessions don't permanently bloat the operator's default view.
  const [showAllActivity, setShowAllActivity] = useState(() => {
    if (typeof window !== "undefined") {
      try {
        const c = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}");
        const flag = Boolean(c.events_show_all_activity);
        const stamp = String(c.events_show_all_activity_set_at || "");
        if (flag && stamp) {
          const setAt = Date.parse(stamp);
          if (!isNaN(setAt) && (Date.now() - setAt) < 7 * 24 * 60 * 60 * 1000) {
            return true;
          }
        }
      } catch { /* */ }
    }
    return false;
  });
  // Persist Show All toggle when it changes
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const cur = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}");
      cur.events_show_all_activity = showAllActivity;
      cur.events_show_all_activity_set_at = showAllActivity ? new Date().toISOString() : "";
      window.localStorage.setItem(FILTER_PREFS_KEY, JSON.stringify(cur));
    } catch { /* */ }
  }, [showAllActivity]);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [handoffInstruction, setHandoffInstruction] = useState("");
  const [handoffBusy, setHandoffBusy] = useState(false);
  const [handoffResult, setHandoffResult] = useState<string | null>(null);
  const [auditRows, setAuditRows] = useState<ActivityAuditRow[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState("");
  const [sseMode, setSseMode] = useState<"live" | "polling">("live");
  const [sseStateText, setSseStateText] = useState("live");
  const [presets, setPresets] = useState<EventPreset[]>([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [counters, setCounters] = useState<EventCounters>(emptyCounters());
  const [activityMetrics, setActivityMetrics] = useState<ActivityOpsMetrics | null>(null);
  const [activityMetricsError, setActivityMetricsError] = useState("");
  const [bulkDeleteBusy, setBulkDeleteBusy] = useState(false);

  const sseSeqRef = useRef(0);
  const sseFailuresRef = useRef(0);
  const sseReconnectTimerRef = useRef<number | null>(null);
  const countersRefreshTimerRef = useRef<number | null>(null);
  const pollingTicksRef = useRef(0);
  const activityMetricsTimerRef = useRef<number | null>(null);

  const buildTimeBounds = useCallback((): { since?: string } => {
    if (timeWindow === "all") return {};
    const nowMs = Date.now();
    const windowMap: Record<string, number> = {
      "30m": 30 * 60 * 1000,
      "1h": 60 * 60 * 1000,
      "2h": 2 * 60 * 60 * 1000,
      "4h": 4 * 60 * 60 * 1000,
      "24h": 24 * 60 * 60 * 1000,
      "7d": 7 * 24 * 60 * 60 * 1000,
      "30d": 30 * 24 * 60 * 60 * 1000,
      "90d": 90 * 24 * 60 * 60 * 1000,
    };
    const windowMs = windowMap[timeWindow] ?? 7 * 24 * 60 * 60 * 1000;
    return { since: new Date(nowMs - windowMs).toISOString() };
  }, [timeWindow]);

  const buildFilterParams = useCallback(
    (includeActionable = true): URLSearchParams => {
      const params = new URLSearchParams();
      // No source_domain param — we fetch all sources and filter client-side via checkedSources
      if (severityFilter) params.set("severity", severityFilter);
      if (statusFilter) params.set("status", statusFilter);
      if (kindFilter) params.set("kind", kindFilter);
      if (includeActionable && actionableOnly) params.set("requires_action", "true");
      if (pinnedOnly) params.set("pinned", "true");
      params.set("all_noise", "true");
      const bounds = buildTimeBounds();
      if (bounds.since) params.set("since", bounds.since);
      return params;
    },
    [actionableOnly, buildTimeBounds, kindFilter, pinnedOnly, severityFilter, statusFilter],
  );

  const loadAudit = useCallback(async (eventId: string) => {
    const id = String(eventId || "").trim();
    if (!id) {
      setAuditRows([]);
      setAuditError("");
      return;
    }
    setAuditLoading(true);
    setAuditError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(id)}/audit?limit=25`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`Audit request failed (${res.status})`);
      const payload = await res.json();
      const rows = Array.isArray(payload.audit) ? (payload.audit as ActivityAuditRow[]) : [];
      setAuditRows(rows);
    } catch (err: any) {
      setAuditError(err?.message || "Failed to load activity audit.");
      setAuditRows([]);
    } finally {
      setAuditLoading(false);
    }
  }, []);

  const loadCounters = useCallback(async () => {
    try {
      const params = buildFilterParams(false);
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events/counters?${params.toString()}`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Counters request failed (${res.status})`);
      const payload = await res.json();
      const next: EventCounters = {
        totals: {
          unread: Number(payload?.totals?.unread || 0),
          actionable: Number(payload?.totals?.actionable || 0),
          total: Number(payload?.totals?.total || 0),
        },
        by_source: {
          ...emptyCounters().by_source,
          ...(payload?.by_source || {}),
        },
      };
      setCounters(next);
    } catch {
      // keep stale counters on transient failures
    }
  }, [buildFilterParams]);

  const loadActivityMetrics = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/metrics/activity-events`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Metrics request failed (${res.status})`);
      const payload = await res.json();
      setActivityMetrics((payload?.metrics || null) as ActivityOpsMetrics | null);
      setActivityMetricsError("");
    } catch (err: any) {
      setActivityMetricsError(err?.message || "Diagnostics unavailable");
    }
  }, []);

  const scheduleCountersRefresh = useCallback(() => {
    if (countersRefreshTimerRef.current !== null) {
      window.clearTimeout(countersRefreshTimerRef.current);
    }
    countersRefreshTimerRef.current = window.setTimeout(() => {
      countersRefreshTimerRef.current = null;
      void loadCounters();
    }, 350);
  }, [loadCounters]);

  const _TRANSIENT_PATTERNS = useMemo(() => [
    /connection\s*refused/i,
    /connecterror/i,
    /\b50[234]\b/,
    /dns\s*resolution\s*failed/i,
    /maintenance\s*mode/i,
    /timeout/i,
    /transient/i,
  ], []);

  const filteredItems = useMemo(() => {
    let result = items;
    // Additive pill filter: only show events whose source_domain is checked
    if (checkedSources.size > 0) {
      result = result.filter((item) => checkedSources.has(item.source_domain));
    } else {
      // Nothing checked = nothing shown (build-up model)
      result = [];
    }
    if (hideTransient) {
      result = result.filter((item) => {
        const fc = String(item.metadata?.failure_class || "");
        if (fc.startsWith("transient_") || fc === "maintenance_mode") return false;
        const msg = (item.full_message || item.summary || "").toLowerCase();
        if (_TRANSIENT_PATTERNS.some((re) => re.test(msg))) return false;
        return true;
      });
    }
    // Phase 7: smart-default filter — hide routine green/info noise
    // unless operator explicitly toggles "Show All Activity". The
    // backend annotates each event with hide_by_default; the frontend
    // honors it here.
    if (!showAllActivity) {
      result = result.filter((item) => !(item.hide_by_default ?? false));
    }
    return result;
  }, [items, hideTransient, checkedSources, _TRANSIENT_PATTERNS, showAllActivity]);

  const [copyBusy, setCopyBusy] = useState(false);

  const copyEventsToClipboard = useCallback(async () => {
    if (filteredItems.length === 0) return;
    setCopyBusy(true);
    try {
      const lines: string[] = [
        `# UA Events Digest (${filteredItems.length} events, filter: ${timeWindow} sources=[${Array.from(checkedSources).join(",")}]${severityFilter ? ` severity=${severityFilter}` : ""})`,
        `# Exported: ${new Date().toISOString()}`,
        "",
      ];
      for (const item of filteredItems) {
        lines.push(`---`);
        lines.push(`[${item.severity.toUpperCase()}] ${item.source_domain} | ${item.title}`);
        lines.push(`  kind: ${item.kind} | status: ${item.status} | created: ${item.created_at_utc}`);
        if (item.requires_action) lines.push(`  ** REQUIRES ACTION **`);
        const msg = (item.full_message || item.summary || "").trim();
        if (msg) {
          lines.push(`  message: ${msg.length > 500 ? msg.slice(0, 500) + "..." : msg}`);
        }
        if (item.entity_ref && Object.keys(item.entity_ref).length > 0) {
          lines.push(`  entity_ref: ${JSON.stringify(item.entity_ref)}`);
        }
        if (item.metadata && Object.keys(item.metadata).length > 0) {
          lines.push(`  metadata: ${JSON.stringify(item.metadata)}`);
        }
        lines.push("");
      }
      lines.push("---");
      lines.push("# End of events digest. Investigate any errors or warnings above.");
      await navigator.clipboard.writeText(lines.join("\n"));
      setHandoffResult(`Copied ${filteredItems.length} event(s) to clipboard.`);
    } catch {
      setHandoffResult("Unable to copy events to clipboard.");
    } finally {
      setCopyBusy(false);
    }
  }, [filteredItems, timeWindow, checkedSources, severityFilter]);

  const deleteAllNotifications = useCallback(async () => {
    const targetCount = items.length;
    if (targetCount === 0) return;
    if (!window.confirm("Delete all notifications/events from the dashboard feed?")) return;

    setBulkDeleteBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/purge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clear_all: true }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(String(payload.detail || payload.reason || `HTTP ${res.status}`));
      }

      const deletedCount = Number(payload.deleted || 0);
      setItems([]);
      setSelectedId("");

      await loadCounters();

      setHandoffResult(`Deleted ${deletedCount} notification/event item(s).`);
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to delete notifications/events.");
    } finally {
      setBulkDeleteBusy(false);
    }
  }, [items.length, loadCounters]);

  const loadEvents = useCallback(async ({ append = false, cursorToken = "" }: { append?: boolean; cursorToken?: string } = {}) => {
    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
    }
    setError("");
    const params = buildFilterParams(true);
    params.set("limit", "120");
    if (cursorToken) params.set("cursor", cursorToken);

    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events?${params.toString()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Events request failed (${res.status})`);
      const payload = await res.json();
      const rows = Array.isArray(payload.events) ? (payload.events as ActivityEvent[]) : [];
      if (append) {
        setItems((prev) => {
          const seen = new Set(prev.map((item) => item.id));
          const merged = [...prev];
          for (const row of rows) {
            if (seen.has(row.id)) continue;
            seen.add(row.id);
            merged.push(row);
          }
          return sortEventsDesc(merged);
        });
      } else {
        const sorted = sortEventsDesc(rows);
        setItems(sorted);
        setSelectedId((prev) => (sorted.some((row) => row.id === prev) ? prev : (sorted[0]?.id || "")));
      }
      setNextCursor(typeof payload.next_cursor === "string" && payload.next_cursor ? payload.next_cursor : null);
      setHasMore(Boolean(payload.has_more));
    } catch (err: any) {
      setError(err?.message || "Failed to load notifications and events.");
      if (!append) {
        setItems([]);
        setSelectedId("");
      }
    } finally {
      if (append) {
        setLoadingMore(false);
      } else {
        setLoading(false);
      }
    }
  }, [buildFilterParams]);

  const loadPresets = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events/presets`, { cache: "no-store" });
      if (!res.ok) throw new Error("preset load failed");
      const payload = await res.json();
      const rows = Array.isArray(payload.presets) ? (payload.presets as EventPreset[]) : [];
      setPresets(rows);
      savePresetCache(rows);
      const defaultPreset = rows.find((item) => item.is_default);
      if (defaultPreset) setSelectedPresetId(defaultPreset.id);
    } catch {
      const cached = loadPresetCache();
      setPresets(cached);
      const defaultPreset = cached.find((item) => item.is_default);
      if (defaultPreset) setSelectedPresetId(defaultPreset.id);
    }
  }, []);

  // Save the full filter state to both localStorage (instant) and backend (durable)
  const saveAllFilterPreferences = useCallback((overrides?: {
    sources?: string[];
    severity?: string;
    status?: string;
    kind?: string;
    timeWindow?: string;
    actionableOnly?: boolean;
    pinnedOnly?: boolean;
    hideTransient?: boolean;
  }) => {
    const prefs: Record<string, unknown> = {
      events_checked_sources: overrides?.sources ?? Array.from(checkedSources),
      events_severity_filter: overrides?.severity ?? severityFilter,
      events_status_filter: overrides?.status ?? statusFilter,
      events_kind_filter: overrides?.kind ?? kindFilter,
      events_time_window: overrides?.timeWindow ?? timeWindow,
      events_actionable_only: overrides?.actionableOnly ?? actionableOnly,
      events_pinned_only: overrides?.pinnedOnly ?? pinnedOnly,
      events_hide_transient: overrides?.hideTransient ?? hideTransient,
    };
    // Instant localStorage save for fast page restore
    try {
      window.localStorage.setItem(FILTER_PREFS_KEY, JSON.stringify(prefs));
    } catch { /* ignore */ }
    // Durable backend save (fire-and-forget)
    fetch(`${API_BASE}/api/v1/ops/preferences`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences: prefs }),
    }).catch((e) => console.error("Failed to save event preferences", e));
  }, [checkedSources, severityFilter, statusFilter, kindFilter, timeWindow, actionableOnly, pinnedOnly, hideTransient]);

  // Fetch event source preferences on mount (before loading events)
  const fetchEventPreferences = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/ops/preferences`, { cache: "no-store" });
      if (r.ok) {
        const data = await r.json();
        const p = data.preferences || {};
        // Restore sources
        const sources = p.events_checked_sources;
        if (Array.isArray(sources) && sources.length > 0) {
          setCheckedSources(new Set(sources as string[]));
        } else if (!Array.isArray(sources)) {
          // No backend prefs yet — keep the default set
        }
        // Restore all other filter settings
        if (typeof p.events_severity_filter === "string") setSeverityFilter(p.events_severity_filter);
        if (typeof p.events_status_filter === "string") setStatusFilter(p.events_status_filter);
        if (typeof p.events_kind_filter === "string") setKindFilter(p.events_kind_filter);
        if (typeof p.events_time_window === "string" && p.events_time_window) setTimeWindow(p.events_time_window);
        if (typeof p.events_actionable_only === "boolean") setActionableOnly(p.events_actionable_only);
        if (typeof p.events_pinned_only === "boolean") setPinnedOnly(p.events_pinned_only);
        if (typeof p.events_hide_transient === "boolean") setHideTransient(p.events_hide_transient);
        // Sync to localStorage for fast restore next time
        try {
          window.localStorage.setItem(FILTER_PREFS_KEY, JSON.stringify(p));
        } catch { /* ignore */ }
      }
    } catch (e) {
      console.error("Failed to load event preferences", e);
    } finally {
      setPreferencesLoaded(true);
    }
  }, []);

  const toggleSource = useCallback((source: string) => {
    setCheckedSources((prev) => {
      const next = new Set(prev);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      saveAllFilterPreferences({ sources: Array.from(next) });
      return next;
    });
  }, [saveAllFilterPreferences]);

  const selectAllSources = useCallback(() => {
    const allSet = new Set(SOURCE_ORDER);
    setCheckedSources(allSet);
    saveAllFilterPreferences({ sources: [...SOURCE_ORDER] });
  }, [saveAllFilterPreferences]);

  const deselectAllSources = useCallback(() => {
    setCheckedSources(new Set());
    saveAllFilterPreferences({ sources: [] });
  }, [saveAllFilterPreferences]);

  useEffect(() => {
    void fetchEventPreferences();
  }, [fetchEventPreferences]);

  // Wait for preferences before loading events
  useEffect(() => {
    if (!preferencesLoaded) return;
    void loadEvents({ append: false });
    void loadCounters();
    void loadActivityMetrics();
  }, [loadActivityMetrics, loadEvents, loadCounters, preferencesLoaded]);

  useEffect(() => {
    void loadPresets();
  }, [loadPresets]);

  useEffect(() => {
    if (!selectedId) {
      setAuditRows([]);
      setAuditError("");
      return;
    }
    void loadAudit(selectedId);
  }, [loadAudit, selectedId]);

  useEffect(() => {
    if (countersRefreshTimerRef.current !== null) {
      window.clearTimeout(countersRefreshTimerRef.current);
      countersRefreshTimerRef.current = null;
    }
    return () => {
      if (countersRefreshTimerRef.current !== null) {
        window.clearTimeout(countersRefreshTimerRef.current);
        countersRefreshTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (activityMetricsTimerRef.current !== null) {
      window.clearInterval(activityMetricsTimerRef.current);
      activityMetricsTimerRef.current = null;
    }
    activityMetricsTimerRef.current = window.setInterval(() => {
      void loadActivityMetrics();
    }, 20000);
    return () => {
      if (activityMetricsTimerRef.current !== null) {
        window.clearInterval(activityMetricsTimerRef.current);
        activityMetricsTimerRef.current = null;
      }
    };
  }, [loadActivityMetrics]);

  useEffect(() => {
    if (sseMode !== "polling") return;
    const timer = window.setInterval(() => {
      void loadEvents({ append: false });
      void loadCounters();
      void loadActivityMetrics();
      pollingTicksRef.current += 1;
      if (pollingTicksRef.current % 5 === 0) {
        sseFailuresRef.current = 0;
        setSseMode("live");
        setSseStateText("restarting stream");
      }
    }, 12000);
    return () => window.clearInterval(timer);
  }, [loadActivityMetrics, loadCounters, loadEvents, sseMode]);

  useEffect(() => {
    if (sseMode !== "live") return;

    let stopped = false;
    let eventSource: EventSource | null = null;

    sseSeqRef.current = 0;
    sseFailuresRef.current = 0;
    setSseStateText("connecting");

    const cleanupStream = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    };

    const reconnectWithBackoff = () => {
      if (stopped) return;
      const failures = sseFailuresRef.current;
      if (failures >= 3) {
        setSseMode("polling");
        setSseStateText("polling fallback");
        return;
      }
      const backoffMs = Math.min(8000, 800 * failures);
      if (sseReconnectTimerRef.current !== null) {
        window.clearTimeout(sseReconnectTimerRef.current);
      }
      sseReconnectTimerRef.current = window.setTimeout(() => {
        sseReconnectTimerRef.current = null;
        openStream();
      }, backoffMs);
    };

    const openStream = () => {
      if (stopped) return;
      cleanupStream();

      const params = buildFilterParams(true);
      params.set("limit", "120");
      params.set("heartbeat_seconds", "20");
      params.set("since_seq", String(Math.max(0, sseSeqRef.current)));
      const url = `${API_BASE}/api/v1/dashboard/events/stream?${params.toString()}`;
      eventSource = new EventSource(url);

      eventSource.onopen = () => {
        if (stopped) return;
        sseFailuresRef.current = 0;
        setSseStateText("live");
      };

      eventSource.onmessage = (message) => {
        if (stopped) return;
        let payload: any = null;
        try {
          payload = JSON.parse(message.data || "{}");
        } catch {
          return;
        }
        const seq = Number(payload?.seq || 0);
        if (Number.isFinite(seq) && seq > sseSeqRef.current) {
          sseSeqRef.current = seq;
        }
        const kind = String(payload?.kind || "");
        if (kind === "snapshot") {
          const rows = Array.isArray(payload.events) ? (payload.events as ActivityEvent[]) : [];
          const sorted = sortEventsDesc(rows);
          setItems(sorted);
          setSelectedId((prev) => (sorted.some((row) => row.id === prev) ? prev : (sorted[0]?.id || "")));
          scheduleCountersRefresh();
          return;
        }
        if (kind === "event") {
          const op = String(payload?.op || "upsert").toLowerCase();
          const event = payload?.event as ActivityEvent | undefined;
          if (op === "delete") {
            const removedId = String((event as any)?.id || "").trim();
            if (removedId) {
              setItems((prev) => prev.filter((row) => row.id !== removedId));
              setSelectedId((prev) => (prev === removedId ? "" : prev));
              scheduleCountersRefresh();
            }
            return;
          }
          if (event && typeof event.id === "string") {
            setItems((prev) => {
              const next = prev.filter((row) => row.id !== event.id);
              next.unshift(event);
              return sortEventsDesc(next);
            });
            setSelectedId((prev) => prev || event.id);
            scheduleCountersRefresh();
          }
        }
      };

      eventSource.onerror = () => {
        cleanupStream();
        if (stopped) return;
        sseFailuresRef.current += 1;
        setSseStateText("reconnecting");
        reconnectWithBackoff();
      };
    };

    openStream();

    return () => {
      stopped = true;
      cleanupStream();
      if (sseReconnectTimerRef.current !== null) {
        window.clearTimeout(sseReconnectTimerRef.current);
        sseReconnectTimerRef.current = null;
      }
    };
  }, [buildFilterParams, scheduleCountersRefresh, sseMode]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );
  const selectedCanary = useMemo(() => parseDeliveryHealthPanelState(selected), [selected]);
  const selectedHeartbeatBadges = useMemo(() => heartbeatMediationBadges(selected), [selected]);

  // Compute per-source counts for the pill badges
  const sourceCountsFromItems = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const source of SOURCE_ORDER) counts[source] = 0;
    for (const row of items) {
      const sd = String(row.source_domain || "system");
      counts[sd] = (counts[sd] || 0) + 1;
    }
    return counts;
  }, [items]);

  const kindOptions = useMemo(() => {
    const values = new Set<string>();
    for (const row of items) values.add(String(row.kind || ""));
    return [...values].filter(Boolean).sort();
  }, [items]);

  const loadOlder = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    await loadEvents({ append: true, cursorToken: nextCursor });
  }, [loadEvents, loadingMore, nextCursor]);

  const currentPresetFilters = useCallback((): Record<string, unknown> => ({
    source_domain: "",
    severity: severityFilter || "",
    status: statusFilter || "",
    kind: kindFilter || "",
    time_window: timeWindow,
    actionable_only: actionableOnly,
    pinned_only: pinnedOnly,
  }), [actionableOnly, kindFilter, pinnedOnly, severityFilter, statusFilter, timeWindow]);

  const applyPresetFilters = useCallback((filters: Record<string, unknown>) => {
    // Source filtering is now handled by pills (checkedSources), not presets
    setSeverityFilter(String(filters.severity || ""));
    setStatusFilter(String(filters.status || ""));
    setKindFilter(String(filters.kind || ""));
    const tw = String(filters.time_window || "7d");
    setTimeWindow(["30m", "1h", "2h", "4h", "24h", "7d", "30d", "90d", "all"].includes(tw) ? tw : "7d");
    setActionableOnly(Boolean(filters.actionable_only));
    setPinnedOnly(Boolean(filters.pinned_only));
  }, []);

  const savePreset = useCallback(async () => {
    const name = window.prompt("Preset name", "My filter preset");
    const cleanName = String(name || "").trim();
    if (!cleanName) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events/presets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: cleanName, filters: currentPresetFilters() }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(payload.detail || `HTTP ${res.status}`));
      const preset = payload.preset as EventPreset;
      const next = [preset, ...presets.filter((row) => row.id !== preset.id)];
      setPresets(next);
      savePresetCache(next);
      setSelectedPresetId(preset.id);
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to save preset.");
    }
  }, [currentPresetFilters, presets]);

  const updatePreset = useCallback(async () => {
    if (!selectedPresetId) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events/presets/${encodeURIComponent(selectedPresetId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filters: currentPresetFilters() }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(payload.detail || `HTTP ${res.status}`));
      const preset = payload.preset as EventPreset;
      const next = presets.map((row) => (row.id === preset.id ? preset : row));
      setPresets(next);
      savePresetCache(next);
      setHandoffResult(`Preset updated: ${preset.name}`);
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to update preset.");
    }
  }, [currentPresetFilters, presets, selectedPresetId]);

  const deletePreset = useCallback(async () => {
    if (!selectedPresetId) return;
    const target = presets.find((row) => row.id === selectedPresetId);
    if (!target) return;
    if (!window.confirm(`Delete preset '${target.name}'?`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events/presets/${encodeURIComponent(selectedPresetId)}`, {
        method: "DELETE",
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(payload.detail || `HTTP ${res.status}`));
      const next = presets.filter((row) => row.id !== selectedPresetId);
      setPresets(next);
      savePresetCache(next);
      setSelectedPresetId("");
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to delete preset.");
    }
  }, [presets, selectedPresetId]);

  const setPresetDefault = useCallback(async () => {
    if (!selectedPresetId) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events/presets/${encodeURIComponent(selectedPresetId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_default: true }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(payload.detail || `HTTP ${res.status}`));
      await loadPresets();
      setHandoffResult("Preset marked as default.");
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to set default preset.");
    }
  }, [loadPresets, selectedPresetId]);

  const applyPresetSelection = useCallback(async (presetId: string) => {
    setSelectedPresetId(presetId);
    if (!presetId) return;
    const preset = presets.find((row) => row.id === presetId);
    if (!preset) return;
    applyPresetFilters(preset.filters || {});
    try {
      await fetch(`${API_BASE}/api/v1/dashboard/events/presets/${encodeURIComponent(presetId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mark_used: true }),
      });
    } catch {
      // best effort
    }
  }, [applyPresetFilters, presets]);

  const copyCommand = useCallback(async (command: string) => {
    const text = String(command || "").trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setHandoffResult("Copied remediation command.");
    } catch {
      setHandoffResult("Unable to copy command.");
    }
  }, []);

  const openAction = useCallback(async (action: ActivityAction) => {
    if (!action) return;
    const actionId = String(action.id || "");
    if (actionId === "copy_runbook_command") {
      const command = String(selected?.metadata?.primary_runbook_command || "").trim();
      if (command) {
        await copyCommand(command);
      }
      return;
    }
    if (actionId === "send_to_simone") {
      setHandoffOpen(true);
      setHandoffInstruction("");
      setHandoffResult(null);
      return;
    }
    if (selected && ["mark_read", "snooze", "unsnooze", "pin", "unpin"].includes(actionId)) {
      try {
        const body: Record<string, unknown> = { action: actionId };
        if (actionId === "snooze") {
          const raw = window.prompt("Snooze for how many minutes?", "60");
          const parsed = Number(raw || "60");
          if (Number.isFinite(parsed) && parsed > 0) {
            body.snooze_minutes = Math.floor(parsed);
          }
        }
        const res = await fetch(
          `${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(selected.id)}/action`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
        );
        const payload = await res.json().catch(() => ({}));
        if (!res.ok || payload.ok === false) {
          throw new Error(String(payload.detail || payload.reason || `HTTP ${res.status}`));
        }
        setHandoffResult(`Action applied: ${actionId}`);
        await loadEvents();
        await loadCounters();
        await loadAudit(selected.id);
      } catch (err: any) {
        setHandoffResult(err?.message || `Action failed: ${actionId}`);
      }
      return;
    }
    const href = String(action.href || "").trim();
    if (!href) return;
    window.location.href = href;
  }, [copyCommand, loadAudit, loadCounters, loadEvents, selected]);

  // Phase 7B: in-UI button to upgrade an event kind's title from the
  // code-fallback template to an LLM-generated one. One glm-4.7 call
  // per (kind, metadata_shape) pair; the cached template applies
  // automatically to every future event of the same shape.
  const [improvingKinds, setImprovingKinds] = useState<Set<string>>(new Set());
  const [improveResult, setImproveResult] = useState<{ kind: string; ok: boolean; message: string } | null>(null);

  const improveEventTitle = useCallback(async (item: ActivityEvent) => {
    const kind = String(item.kind || "").trim();
    if (!kind) return;
    if (improvingKinds.has(kind)) return;
    setImprovingKinds((prev) => {
      const next = new Set(prev);
      next.add(kind);
      return next;
    });
    setImproveResult(null);
    try {
      const res = await fetch(
        `/api/dashboard/gateway/api/v1/dashboard/events/templates/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sample_event: item }),
        },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 120)}`);
      }
      const payload = await res.json();
      const template = payload?.template?.title_template ?? "(no template returned)";
      setImproveResult({ kind, ok: true, message: `Template for "${kind}": ${template}` });
      // Refresh the events list so the new template applies immediately
      await loadEvents({ append: false });
    } catch (err: any) {
      setImproveResult({ kind, ok: false, message: err?.message || "Failed to improve title" });
    } finally {
      setImprovingKinds((prev) => {
        const next = new Set(prev);
        next.delete(kind);
        return next;
      });
    }
  }, [improvingKinds, loadEvents]);

  const deleteNotification = useCallback(async (id: string, opts?: { skipConfirm?: boolean }) => {
    const eventId = String(id || "").trim();
    if (!eventId) return;
    if (!opts?.skipConfirm && !window.confirm("Delete this notification?")) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(eventId)}`,
        { method: "DELETE" },
      );
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(payload.detail || `HTTP ${res.status}`));
      setItems((prev) => prev.filter((row) => row.id !== eventId));
      setSelectedId((prev) => (prev === eventId ? "" : prev));
      await loadCounters();
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to delete notification.");
    }
  }, [loadCounters]);

  async function submitHandoff() {
    if (!selected) return;
    const instruction = handoffInstruction.trim();
    if (!instruction) {
      setHandoffResult("Instruction is required.");
      return;
    }
    setHandoffBusy(true);
    setHandoffResult(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(selected.id)}/send-to-simone`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction }),
        },
      );
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload.ok === false) {
        throw new Error(String(payload.detail || payload.reason || `HTTP ${res.status}`));
      }
      setHandoffResult("Handoff dispatched to Simone.");
      setHandoffOpen(false);
      setHandoffInstruction("");
      await loadEvents();
      await loadCounters();
      await loadAudit(selected.id);
    } catch (err: any) {
      setHandoffResult(err?.message || "Failed to dispatch handoff.");
    } finally {
      setHandoffBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Notifications & Events</h1>
          <p className="text-sm text-muted-foreground">
            Unified feed across CSI, tutorials, cron, continuity, and system activity.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded border border-border bg-background/60 px-2 py-1 text-[11px] text-foreground/80">
            Stream: {sseStateText}
          </span>
          <button
            type="button"
            onClick={() => void copyEventsToClipboard()}
            disabled={copyBusy || filteredItems.length === 0}
            className="rounded border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-primary/80 hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
            title="Copy all visible events as structured text for IDE AI coder"
          >
            {copyBusy ? "Copying..." : `Copy Events (${filteredItems.length})`}
          </button>
          <button
            type="button"
            onClick={() => {
              void deleteAllNotifications();
            }}
            disabled={bulkDeleteBusy || filteredItems.length === 0}
            className="rounded border border-red-400/30 bg-red-400/10 px-3 py-1.5 text-xs text-red-400/80 hover:bg-red-400/20 disabled:cursor-not-allowed disabled:opacity-50"
            title="Delete all currently visible notifications/events"
          >
            {bulkDeleteBusy ? "Deleting..." : `Delete All (${filteredItems.length})`}
          </button>
          <button
            type="button"
            onClick={() => {
              void loadEvents({ append: false });
              void loadCounters();
              void loadActivityMetrics();
              if (sseMode === "polling") {
                sseFailuresRef.current = 0;
                setSseMode("live");
                setSseStateText("connecting");
              }
            }}
            className="rounded border border-border bg-background/60 px-3 py-1.5 text-xs text-foreground hover:bg-card/70"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-background/70 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={selectedPresetId}
            onChange={(event) => void applyPresetSelection(event.target.value)}
            className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[12px]"
          >
            <option value="">No preset</option>
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.is_default ? "★ " : ""}{preset.name}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => void savePreset()} className="rounded border border-border bg-background/60 px-2 py-1 text-[11px] hover:bg-card/70">Save preset</button>
          <button type="button" onClick={() => void updatePreset()} disabled={!selectedPresetId} className="rounded border border-border bg-background/60 px-2 py-1 text-[11px] hover:bg-card/70 disabled:opacity-50">Update preset</button>
          <button type="button" onClick={() => void setPresetDefault()} disabled={!selectedPresetId} className="rounded border border-border bg-background/60 px-2 py-1 text-[11px] hover:bg-card/70 disabled:opacity-50">Set default</button>
          <button type="button" onClick={() => void deletePreset()} disabled={!selectedPresetId} className="rounded border border-red-400/30 bg-red-400/10 px-2 py-1 text-[11px] text-red-400/80 hover:bg-red-400/20 disabled:opacity-50">Delete preset</button>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            value={severityFilter}
            onChange={(event) => { setSeverityFilter(event.target.value); saveAllFilterPreferences({ severity: event.target.value }); }}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          >
            <option value="">All Severity</option>
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="error">error</option>
            <option value="success">success</option>
          </select>
          <select
            value={statusFilter}
            onChange={(event) => { setStatusFilter(event.target.value); saveAllFilterPreferences({ status: event.target.value }); }}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          >
            <option value="">All Status</option>
            <option value="new">new</option>
            <option value="read">read</option>
            <option value="acknowledged">acknowledged</option>
            <option value="snoozed">snoozed</option>
            <option value="dismissed">dismissed</option>
          </select>
          <select
            value={kindFilter}
            onChange={(event) => { setKindFilter(event.target.value); saveAllFilterPreferences({ kind: event.target.value }); }}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          >
            <option value="">All Kinds</option>
            {kindOptions.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
          <select
            value={timeWindow}
            onChange={(event) => { setTimeWindow(event.target.value); saveAllFilterPreferences({ timeWindow: event.target.value }); }}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          >
            <option value="30m">Last 30m</option>
            <option value="1h">Last 1h</option>
            <option value="2h">Last 2h</option>
            <option value="4h">Last 4h</option>
            <option value="24h">Last 24h</option>
            <option value="7d">Last 7d</option>
            <option value="30d">Last 30d</option>
            <option value="90d">Last 90d</option>
            <option value="all">All (retained)</option>
          </select>
          <label className="inline-flex items-center gap-1 rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]">
            <input
              type="checkbox"
              checked={actionableOnly}
              onChange={(event) => { setActionableOnly(event.target.checked); saveAllFilterPreferences({ actionableOnly: event.target.checked }); }}
            />
            Actionable only
          </label>
          <label className="inline-flex items-center gap-1 rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]">
            <input
              type="checkbox"
              checked={pinnedOnly}
              onChange={(event) => { setPinnedOnly(event.target.checked); saveAllFilterPreferences({ pinnedOnly: event.target.checked }); }}
            />
            Pinned only
          </label>
          <label className="inline-flex items-center gap-1 rounded border border-amber-700/40 bg-amber-950/20 px-2 py-1 text-[12px] text-amber-200">
            <input
              type="checkbox"
              checked={hideTransient}
              onChange={(event) => { setHideTransient(event.target.checked); saveAllFilterPreferences({ hideTransient: event.target.checked }); }}
            />
            Hide transient
          </label>
          {/* Phase 7: smart-default filter toggle. OFF = operator view */}
          {/* (hide routine green/info noise); ON = full firehose for debug. */}
          {/* Sticky 7 days; auto-resets so debug sessions don't bloat default. */}
          <label
            className="inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[12px] text-primary/85"
            title="When OFF (default), routine heartbeat ticks and unchanged cron syncs are hidden. Toggle ON to see the full firehose; reverts after 7 days."
          >
            <input
              type="checkbox"
              checked={showAllActivity}
              onChange={(event) => setShowAllActivity(event.target.checked)}
            />
            Show All Activity
          </label>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold mr-1">Sources</span>
          {SOURCE_ORDER.map((source) => {
            const isChecked = checkedSources.has(source);
            const count = sourceCountsFromItems[source] || 0;
            return (
              <button
                key={`src-chip-${source}`}
                type="button"
                onClick={() => toggleSource(source)}
                className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-all ${isChecked
                  ? `${SOURCE_STYLES[source] || SOURCE_STYLES.system} ring-1 ring-primary/40 shadow-sm`
                  : "border-border/40 bg-background/30 text-muted-foreground/60 hover:bg-background/50 hover:text-foreground/80"
                }`}
                title={`${source}: ${count} event(s) in current window`}
                aria-pressed={isChecked}
              >
                {source}{count > 0 ? ` (${count})` : ""}
              </button>
            );
          })}
          <span className="mx-1 h-4 w-px bg-border/40" />
          <button
            type="button"
            onClick={selectAllSources}
            className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-all ${
              checkedSources.size === SOURCE_ORDER.length
                ? "border-primary/40 bg-primary/15 text-primary ring-1 ring-primary/40"
                : "border-border/40 bg-background/30 text-muted-foreground/60 hover:bg-primary/10 hover:text-primary/80"
            }`}
            title="Select all source categories"
          >
            All
          </button>
          <button
            type="button"
            onClick={deselectAllSources}
            className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-all ${
              checkedSources.size === 0
                ? "border-muted-foreground/40 bg-muted-foreground/10 text-muted-foreground ring-1 ring-muted-foreground/30"
                : "border-border/40 bg-background/30 text-muted-foreground/60 hover:bg-muted-foreground/10 hover:text-foreground/80"
            }`}
            title="Clear all source categories"
          >
            None
          </button>
        </div>

        <div className="mt-2 rounded border border-border bg-background/50 p-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Activity Diagnostics</div>
          {activityMetricsError ? (
            <div className="mt-1 text-[11px] text-secondary">{activityMetricsError}</div>
          ) : (
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-foreground/80">
              <span className="rounded border border-border px-2 py-0.5">sse_connects {Number(activityMetrics?.counters?.events_sse_connects || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">sse_disconnects {Number(activityMetrics?.counters?.events_sse_disconnects || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">sse_payloads {Number(activityMetrics?.counters?.events_sse_payloads || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">sse_heartbeats {Number(activityMetrics?.counters?.events_sse_heartbeats || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">sse_errors {Number(activityMetrics?.counters?.events_sse_errors || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">digest_compacted {Number(activityMetrics?.counters?.digest_compacted_total || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">digest_bypass {Number(activityMetrics?.counters?.digest_immediate_bypass_total || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">digest_buckets_open {Number(activityMetrics?.counters?.digest_buckets_open || 0)}</span>
              <span className="rounded border border-border px-2 py-0.5">uptime_s {Number(activityMetrics?.uptime_seconds || 0)}</span>
            </div>
          )}
        </div>

        {error && <div className="mt-2 text-sm text-secondary">{error}</div>}
      </div>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-1 overflow-y-auto pr-1 scrollbar-thin">
          {/* Phase 7B: result banner from the most recent "Improve title" action */}
          {improveResult && (
            <div
              className={`mb-2 flex items-start justify-between gap-2 rounded border px-3 py-2 text-xs ${
                improveResult.ok
                  ? "border-primary/40 bg-primary/10 text-primary/90"
                  : "border-red-500/30 bg-red-500/10 text-red-300"
              }`}
            >
              <div className="min-w-0 flex-1 break-words">
                <span className="font-medium">{improveResult.ok ? "✓ Title improved" : "✗ Failed"}</span>
                <span className="ml-2">{improveResult.message}</span>
              </div>
              <button
                onClick={() => setImproveResult(null)}
                className="shrink-0 rounded px-1 text-muted-foreground hover:text-foreground"
                title="Dismiss"
              >
                ×
              </button>
            </div>
          )}
          {filteredItems.length === 0 && (
            <div className="rounded border border-border bg-background/40 px-3 py-4 text-sm text-muted-foreground">
              {checkedSources.size === 0
                ? "Select source categories above to view events."
                : hideTransient && items.length > 0
                  ? `${items.length - filteredItems.length} event(s) hidden by active filters.`
                  : "No notifications/events found for selected sources."}
            </div>
          )}
          {filteredItems.map((item) => {
            const sourceStyle = SOURCE_STYLES[item.source_domain] || SOURCE_STYLES.system;
            const severityStyle = SEVERITY_STYLES[item.severity] || SEVERITY_STYLES.info;
            const active = selectedId === item.id;
            const mediationBadges = heartbeatMediationBadges(item);
            return (
              <div key={item.id} className="relative group">
                <button
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                  className={[
                    "w-full rounded border px-3 py-2 text-left transition-colors",
                    active
                      ? "border-primary/40 bg-primary/10"
                      : "border-border bg-background/40 hover:bg-background/60",
                  ].join(" ")}
                >
                  <div className="mb-1 flex items-center gap-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${sourceStyle}`}>
                      {item.source_domain}
                    </span>
                    <span className={`text-[10px] uppercase ${severityStyle}`}>{item.severity}</span>
                    {(item.kind === "csi_delivery_health_regression" || item.kind === "csi_delivery_health_recovered") && (
                      <span className="text-[10px] uppercase text-accent">canary</span>
                    )}
                    {mediationBadges.map((badge) => (
                      <span key={`${item.id}-${badge.label}`} className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${badge.classes}`}>
                        {badge.label}
                      </span>
                    ))}
                    {Boolean(item.metadata?.pinned) && (
                      <span className="text-[10px] uppercase text-accent">pinned</span>
                    )}
                    <span className="ml-auto pr-6 text-[10px] text-muted-foreground" title={formatDateTimeTz(item.created_at_utc, { timeZone: "UTC", placeholder: "--" })}>
                      {timeAgo(item.created_at_utc)}
                    </span>
                  </div>
                  <div className="text-sm font-medium text-foreground">{item.smart_title || item.title}</div>
                  <div className="mt-1 text-xs text-muted-foreground line-clamp-2">{item.summary || item.full_message}</div>
                  {/* Phase 7B: show "Improve title" affordance on rows whose smart_title came from the code fallback. */}
                  {item.title_template_source === "fallback" && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); void improveEventTitle(item); }}
                      disabled={improvingKinds.has(item.kind)}
                      className="mt-2 inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] text-primary/85 hover:bg-primary/20 disabled:opacity-50"
                      title="Generate an LLM title template for this event kind. One glm-4.7 call; future events of the same shape pick it up automatically."
                    >
                      {improvingKinds.has(item.kind) ? "Improving…" : "✨ Improve title"}
                    </button>
                  )}
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); void deleteNotification(item.id); }}
                  className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity rounded p-1 text-muted-foreground hover:text-secondary hover:bg-red-400/15"
                  title="Delete notification"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6"/>
                    <path d="M19 6l-1 14H6L5 6"/>
                    <path d="M10 11v6M14 11v6"/>
                    <path d="M9 6V4h6v2"/>
                  </svg>
                </button>
              </div>
            );
          })}
          {hasMore && (
            <button
              type="button"
              onClick={() => void loadOlder()}
              disabled={loadingMore}
              className="mt-2 w-full rounded border border-border bg-background/70 px-3 py-2 text-xs text-foreground hover:bg-card/80 disabled:opacity-60"
            >
              {loadingMore ? "Loading older..." : "Load older"}
            </button>
          )}
          {!hasMore && filteredItems.length > 0 && (
            <div className="mt-2 rounded border border-border bg-background/40 px-3 py-2 text-center text-[11px] text-muted-foreground">
              End of retained history
            </div>
          )}
        </div>

        <div className="min-h-[240px] overflow-hidden rounded border border-border bg-background/50">
          {!selected ? (
            <div className="p-4 text-sm text-muted-foreground">Select a notification/event to view full details.</div>
          ) : (
            <div className="flex h-full flex-col">
              <div className="border-b border-border bg-background/70 px-4 py-3">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                      SOURCE_STYLES[selected.source_domain] || SOURCE_STYLES.system
                    }`}
                  >
                    {selected.source_domain}
                  </span>
                  <span className={`text-[10px] uppercase ${SEVERITY_STYLES[selected.severity] || SEVERITY_STYLES.info}`}>
                    {selected.severity}
                  </span>
                  {selectedHeartbeatBadges.map((badge) => (
                    <span key={`${selected.id}-${badge.label}`} className={`rounded border px-2 py-0.5 text-[10px] uppercase ${badge.classes}`}>
                      {badge.label}
                    </span>
                  ))}
                  <span
                    className="text-[10px] text-muted-foreground"
                    title={`UTC: ${formatDateTimeTz(selected.created_at_utc, { timeZone: "UTC", placeholder: "--" })}`}
                  >
                    Local: {formatDateTimeTz(selected.created_at_utc, { placeholder: "--" })}
                  </span>
                  <button
                    type="button"
                    onClick={() => { setHandoffOpen(true); setHandoffInstruction(""); setHandoffResult(null); }}
                    className="ml-auto flex items-center gap-1 rounded border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20 transition-colors"
                    title="Reply to Simone with this event as context"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
                    </svg>
                    Reply
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteNotification(selected.id)}
                    className="flex items-center gap-1 rounded border border-red-400/30 bg-red-400/10 px-2 py-1 text-[11px] text-secondary hover:bg-red-400/15 hover:text-red-400/80 transition-colors"
                    title="Delete this notification"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="3 6 5 6 21 6"/>
                      <path d="M19 6l-1 14H6L5 6"/>
                      <path d="M10 11v6M14 11v6"/>
                      <path d="M9 6V4h6v2"/>
                    </svg>
                    Delete
                  </button>
                </div>
                <div className="text-sm font-semibold text-foreground">{selected.title}</div>
                <div className="mt-1 text-[10px] font-mono text-muted-foreground">
                  id: {selected.id} | kind: {selected.kind} | status: {selected.status}
                </div>
                {selected.source_domain === "heartbeat" && (
                  <div className="mt-2 text-[11px] text-foreground/80">
                    Non-OK heartbeat findings are auto-routed to Simone for investigation. Manual handoff remains available.
                  </div>
                )}
                {handoffResult && <div className="mt-2 text-xs text-primary">{handoffResult}</div>}
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto p-4 scrollbar-thin">
                {selectedCanary && (
                  <div className="space-y-2 rounded border border-primary/30 bg-primary/10 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-[11px] uppercase tracking-wide text-primary/80">Delivery Health Canary</div>
                      <span className={`rounded border px-2 py-0.5 text-[10px] uppercase ${canaryStatusClasses(selectedCanary.status)}`}>
                        {selectedCanary.status || "unknown"}
                      </span>
                    </div>

                    {(selectedCanary.failingSources.length > 0 || selectedCanary.degradedSources.length > 0) && (
                      <div className="flex flex-wrap gap-2 text-[11px]">
                        {selectedCanary.failingSources.map((source) => (
                          <span
                            key={`failing-${source}`}
                            className="rounded border border-red-400/30 bg-red-400/10 px-2 py-0.5 text-red-400/80"
                          >
                            failing: {source}
                          </span>
                        ))}
                        {selectedCanary.degradedSources.map((source) => (
                          <span
                            key={`degraded-${source}`}
                            className="rounded border border-amber-700/60 bg-amber-950/30 px-2 py-0.5 text-amber-200"
                          >
                            degraded: {source}
                          </span>
                        ))}
                      </div>
                    )}

                    <div className="flex flex-wrap gap-2">
                      {selectedCanary.primaryRunbookCommand && (
                        <button
                          type="button"
                          onClick={() => void copyCommand(selectedCanary.primaryRunbookCommand)}
                          className="rounded border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                        >
                          Copy Primary Runbook
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => { window.location.href = "/dashboard/csi"; }}
                        className="rounded border border-border bg-background/70 px-2 py-1 text-[11px] text-foreground hover:bg-card/80"
                      >
                        View in CSI
                      </button>
                    </div>

                    {selectedCanary.steps.length > 0 && (
                      <div className="space-y-1">
                        <div className="text-[11px] uppercase tracking-wide text-foreground/80">Guided Remediation</div>
                        <div className="space-y-2">
                          {selectedCanary.steps.map((step, index) => {
                            const stepCommand = String(step.runbook_command || "").trim();
                            const stepSource = String(step.source || "").trim();
                            const stepSeverity = String(step.severity || "warning").trim().toLowerCase();
                            return (
                              <div key={`step-${index}`} className="rounded border border-border bg-background/50 p-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-[11px] text-foreground">{step.title || step.code || "Remediation Step"}</span>
                                  <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${canaryStatusClasses(stepSeverity)}`}>
                                    {stepSeverity}
                                  </span>
                                  {stepSource && <span className="text-[10px] text-muted-foreground">{stepSource}</span>}
                                  {stepCommand && (
                                    <button
                                      type="button"
                                      onClick={() => void copyCommand(stepCommand)}
                                      className="ml-auto rounded border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] text-primary/80 hover:bg-primary/20"
                                    >
                                      Copy Command
                                    </button>
                                  )}
                                </div>
                                {step.action && <div className="mt-1 text-[11px] text-foreground/80">{step.action}</div>}
                                {step.detail && <div className="mt-1 text-[11px] text-muted-foreground">{step.detail}</div>}
                                {stepCommand && (
                                  <pre className="mt-2 rounded border border-border bg-background/50 p-2 text-[10px] text-foreground/80 overflow-x-auto whitespace-pre-wrap">
                                    {stepCommand}
                                  </pre>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <div className="space-y-1">
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Message</div>
                  <div className="rounded border border-border bg-background/50 p-3 text-xs whitespace-pre-wrap text-foreground/80">
                    {selected.full_message || selected.summary}
                  </div>
                </div>

                {Array.isArray(selected.actions) && selected.actions.length > 0 && (
                  <div className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Actions</div>
                    <div className="flex flex-wrap gap-2">
                      {selected.actions.map((action, idx) => (
                        <button
                          key={`${selected.id}-action-${idx}`}
                          type="button"
                          onClick={() => openAction(action)}
                          className="rounded border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                        >
                          {action.label || action.id || "Action"}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {selected.entity_ref && Object.keys(selected.entity_ref).length > 0 && (() => {
                  const ref = selected.entity_ref as Record<string, unknown>;
                  const links: { label: string; href: string }[] = [];
                  if (typeof ref.route === "string" && ref.route) links.push({ label: `Go to ${String(ref.tab || "tab").charAt(0).toUpperCase() + String(ref.tab || "tab").slice(1)}`, href: ref.route });
                  if (typeof ref.session_href === "string" && ref.session_href) links.push({ label: "Open Session", href: ref.session_href });
                  if (typeof ref.report_href === "string" && ref.report_href) links.push({ label: "Open Report", href: ref.report_href });
                  if (typeof ref.artifact_href === "string" && ref.artifact_href) links.push({ label: "Open Artifact", href: ref.artifact_href });
                  const remaining = Object.fromEntries(Object.entries(ref).filter(([k]) => !["route", "tab", "session_href", "report_href", "artifact_href"].includes(k)));
                  return (
                    <div className="space-y-1">
                      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Entity Ref</div>
                      {links.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {links.map((link) => (
                            <a
                              key={link.href}
                              href={link.href}
                              className="rounded border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20 no-underline transition-colors"
                            >
                              {link.label}
                            </a>
                          ))}
                        </div>
                      )}
                      {Object.keys(remaining).length > 0 && (
                        <pre className="rounded border border-border bg-background/50 p-3 text-[10px] text-foreground/80 overflow-x-auto whitespace-pre-wrap">
                          {JSON.stringify(remaining, null, 2)}
                        </pre>
                      )}
                    </div>
                  );
                })()}

                {selected.metadata && Object.keys(selected.metadata).length > 0 && (
                  <div className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Metadata</div>
                    <pre className="rounded border border-border bg-background/50 p-3 text-[10px] text-foreground/80 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(selected.metadata, null, 2)}
                    </pre>
                  </div>
                )}

                <div className="space-y-1">
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Activity Audit</div>
                  {auditLoading ? (
                    <div className="rounded border border-border bg-background/40 p-3 text-xs text-muted-foreground">Loading audit...</div>
                  ) : auditError ? (
                    <div className="rounded border border-red-400/30 bg-red-400/10 p-3 text-xs text-secondary">{auditError}</div>
                  ) : auditRows.length === 0 ? (
                    <div className="rounded border border-border bg-background/40 p-3 text-xs text-muted-foreground">No actions recorded yet.</div>
                  ) : (
                    <div className="space-y-1">
                      {auditRows.map((row) => (
                        <div key={`${selected.id}-audit-${row.id}`} className="rounded border border-border bg-background/40 p-2">
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                            <span className="uppercase text-primary">{row.action}</span>
                            <span className="uppercase">{row.outcome}</span>
                            <span>{row.actor}</span>
                            <span className="ml-auto">{formatDateTimeTz(row.created_at_utc, { placeholder: "--" })}</span>
                          </div>
                          {row.note && <div className="mt-1 text-[11px] text-foreground/80 whitespace-pre-wrap">{row.note}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {handoffOpen && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4">
          <div className="w-full max-w-xl rounded-lg border border-border bg-background p-4">
            <h2 className="text-sm font-semibold text-foreground">Send to Simone</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Add context for why this item should be investigated. This message is attached to the forwarded event.
            </p>
            <textarea
              value={handoffInstruction}
              onChange={(event) => setHandoffInstruction(event.target.value)}
              rows={7}
              className="mt-3 w-full rounded border border-border bg-background/70 p-2 text-xs text-foreground outline-none focus:border-primary"
              placeholder="Explain what Simone should do with this report/event."
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setHandoffOpen(false)}
                className="rounded border border-border bg-card/70 px-3 py-1.5 text-xs text-foreground"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submitHandoff()}
                disabled={handoffBusy}
                className="rounded border border-primary/30 bg-primary/20 px-3 py-1.5 text-xs text-primary/90 disabled:opacity-60"
              >
                {handoffBusy ? "Sending..." : "Send to Simone"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
