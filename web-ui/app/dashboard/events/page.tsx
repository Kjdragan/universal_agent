"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";
const PRESET_CACHE_KEY = "ua.dashboard.events.presets.v1";

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
  csi: "bg-cyan-500/10 text-cyan-300 border-cyan-500/30",
  tutorial: "bg-violet-500/10 text-violet-300 border-violet-500/30",
  cron: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  continuity: "bg-orange-500/10 text-orange-300 border-orange-500/30",
  heartbeat: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  system: "bg-slate-500/10 text-slate-300 border-slate-500/30",
};

const SEVERITY_STYLES: Record<string, string> = {
  success: "text-emerald-300",
  error: "text-rose-300",
  warning: "text-amber-300",
  info: "text-sky-300",
};

const SOURCE_ORDER = ["csi", "tutorial", "cron", "continuity", "heartbeat", "system"];

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
  if (normalized === "failing") return "border-rose-700/60 bg-rose-950/30 text-rose-200";
  if (normalized === "degraded") return "border-amber-700/60 bg-amber-950/30 text-amber-200";
  return "border-emerald-700/60 bg-emerald-950/30 text-emerald-200";
}

export default function DashboardEventsPage() {
  const [items, setItems] = useState<ActivityEvent[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [timeWindow, setTimeWindow] = useState("7d");
  const [actionableOnly, setActionableOnly] = useState(false);
  const [pinnedOnly, setPinnedOnly] = useState(false);
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

  const sseSeqRef = useRef(0);
  const sseFailuresRef = useRef(0);
  const sseReconnectTimerRef = useRef<number | null>(null);
  const countersRefreshTimerRef = useRef<number | null>(null);
  const pollingTicksRef = useRef(0);
  const activityMetricsTimerRef = useRef<number | null>(null);

  const buildTimeBounds = useCallback((): { since?: string } => {
    if (timeWindow === "all") return {};
    const nowMs = Date.now();
    const windowMs =
      timeWindow === "24h"
        ? 24 * 60 * 60 * 1000
        : timeWindow === "30d"
          ? 30 * 24 * 60 * 60 * 1000
          : timeWindow === "90d"
            ? 90 * 24 * 60 * 60 * 1000
            : 7 * 24 * 60 * 60 * 1000;
    return { since: new Date(nowMs - windowMs).toISOString() };
  }, [timeWindow]);

  const buildFilterParams = useCallback(
    (includeActionable = true): URLSearchParams => {
      const params = new URLSearchParams();
      if (sourceFilter) params.set("source_domain", sourceFilter);
      if (severityFilter) params.set("severity", severityFilter);
      if (statusFilter) params.set("status", statusFilter);
      if (kindFilter) params.set("kind", kindFilter);
      if (includeActionable && actionableOnly) params.set("requires_action", "true");
      if (pinnedOnly) params.set("pinned", "true");
      const bounds = buildTimeBounds();
      if (bounds.since) params.set("since", bounds.since);
      return params;
    },
    [actionableOnly, buildTimeBounds, kindFilter, pinnedOnly, severityFilter, sourceFilter, statusFilter],
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

  useEffect(() => {
    void loadEvents({ append: false });
    void loadCounters();
    void loadActivityMetrics();
  }, [loadActivityMetrics, loadEvents, loadCounters]);

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

  const sourceOptions = useMemo(() => {
    const values = new Set<string>();
    for (const row of items) values.add(String(row.source_domain || "system"));
    return [...values].sort();
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
    source_domain: sourceFilter || "",
    severity: severityFilter || "",
    status: statusFilter || "",
    kind: kindFilter || "",
    time_window: timeWindow,
    actionable_only: actionableOnly,
    pinned_only: pinnedOnly,
  }), [actionableOnly, kindFilter, pinnedOnly, severityFilter, sourceFilter, statusFilter, timeWindow]);

  const applyPresetFilters = useCallback((filters: Record<string, unknown>) => {
    setSourceFilter(String(filters.source_domain || ""));
    setSeverityFilter(String(filters.severity || ""));
    setStatusFilter(String(filters.status || ""));
    setKindFilter(String(filters.kind || ""));
    const tw = String(filters.time_window || "7d");
    setTimeWindow(["24h", "7d", "30d", "90d", "all"].includes(tw) ? tw : "7d");
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

  const openAction = useCallback(async (action: ActivityAction) => {
    if (!action) return;
    const actionId = String(action.id || "");
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
  }, [loadAudit, loadCounters, loadEvents, selected]);

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
          <p className="text-sm text-slate-400">
            Unified feed across CSI, tutorials, cron, continuity, and system activity.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-[11px] text-slate-300">
            Stream: {sseStateText}
          </span>
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
            className="rounded border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800/70"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={selectedPresetId}
            onChange={(event) => void applyPresetSelection(event.target.value)}
            className="rounded border border-cyan-800/60 bg-cyan-950/20 px-2 py-1 text-[12px]"
          >
            <option value="">No preset</option>
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.is_default ? "★ " : ""}{preset.name}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => void savePreset()} className="rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-[11px] hover:bg-slate-800/70">Save preset</button>
          <button type="button" onClick={() => void updatePreset()} disabled={!selectedPresetId} className="rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-[11px] hover:bg-slate-800/70 disabled:opacity-50">Update preset</button>
          <button type="button" onClick={() => void setPresetDefault()} disabled={!selectedPresetId} className="rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-[11px] hover:bg-slate-800/70 disabled:opacity-50">Set default</button>
          <button type="button" onClick={() => void deletePreset()} disabled={!selectedPresetId} className="rounded border border-rose-800/60 bg-rose-950/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50">Delete preset</button>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          >
            <option value="">All Sources</option>
            {sourceOptions.map((source) => (
              <option key={source} value={source}>
                {source}
              </option>
            ))}
          </select>
          <select
            value={severityFilter}
            onChange={(event) => setSeverityFilter(event.target.value)}
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
            onChange={(event) => setStatusFilter(event.target.value)}
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
            onChange={(event) => setKindFilter(event.target.value)}
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
            onChange={(event) => setTimeWindow(event.target.value)}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          >
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
              onChange={(event) => setActionableOnly(event.target.checked)}
            />
            Actionable only
          </label>
          <label className="inline-flex items-center gap-1 rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]">
            <input
              type="checkbox"
              checked={pinnedOnly}
              onChange={(event) => setPinnedOnly(event.target.checked)}
            />
            Pinned only
          </label>
        </div>

        <div className="mt-2 flex flex-wrap gap-2">
          {SOURCE_ORDER.map((source) => {
            const bucket = counters.by_source[source] || { unread: 0, actionable: 0, total: 0 };
            return (
              <button
                key={`src-chip-${source}`}
                type="button"
                onClick={() => setSourceFilter((prev) => (prev === source ? "" : source))}
                className={`rounded border px-2 py-1 text-[11px] ${SOURCE_STYLES[source] || SOURCE_STYLES.system}`}
                title={`unread: ${bucket.unread} | actionable: ${bucket.actionable} | total: ${bucket.total}`}
              >
                {source} {bucket.unread}/{bucket.actionable}/{bucket.total}
              </button>
            );
          })}
          <span className="rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-[11px] text-slate-300">
            totals {counters.totals.unread}/{counters.totals.actionable}/{counters.totals.total}
          </span>
        </div>

        <div className="mt-2 rounded border border-slate-800 bg-slate-950/50 p-2">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">Activity Diagnostics</div>
          {activityMetricsError ? (
            <div className="mt-1 text-[11px] text-rose-300">{activityMetricsError}</div>
          ) : (
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-300">
              <span className="rounded border border-slate-700 px-2 py-0.5">sse_connects {Number(activityMetrics?.counters?.events_sse_connects || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">sse_disconnects {Number(activityMetrics?.counters?.events_sse_disconnects || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">sse_payloads {Number(activityMetrics?.counters?.events_sse_payloads || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">sse_heartbeats {Number(activityMetrics?.counters?.events_sse_heartbeats || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">sse_errors {Number(activityMetrics?.counters?.events_sse_errors || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">digest_compacted {Number(activityMetrics?.counters?.digest_compacted_total || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">digest_bypass {Number(activityMetrics?.counters?.digest_immediate_bypass_total || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">digest_buckets_open {Number(activityMetrics?.counters?.digest_buckets_open || 0)}</span>
              <span className="rounded border border-slate-700 px-2 py-0.5">uptime_s {Number(activityMetrics?.uptime_seconds || 0)}</span>
            </div>
          )}
        </div>

        {error && <div className="mt-2 text-sm text-rose-300">{error}</div>}
      </div>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-1 overflow-y-auto pr-1 scrollbar-thin">
          {items.length === 0 && (
            <div className="rounded border border-slate-800 bg-slate-950/40 px-3 py-4 text-sm text-slate-400">
              No notifications/events found.
            </div>
          )}
          {items.map((item) => {
            const sourceStyle = SOURCE_STYLES[item.source_domain] || SOURCE_STYLES.system;
            const severityStyle = SEVERITY_STYLES[item.severity] || SEVERITY_STYLES.info;
            const active = selectedId === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={[
                  "w-full rounded border px-3 py-2 text-left transition-colors",
                  active
                    ? "border-cyan-500/60 bg-cyan-500/10"
                    : "border-slate-800 bg-slate-950/40 hover:bg-slate-900/60",
                ].join(" ")}
              >
                <div className="mb-1 flex items-center gap-2">
                  <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${sourceStyle}`}>
                    {item.source_domain}
                  </span>
                  <span className={`text-[10px] uppercase ${severityStyle}`}>{item.severity}</span>
                  {(item.kind === "csi_delivery_health_regression" || item.kind === "csi_delivery_health_recovered") && (
                    <span className="text-[10px] uppercase text-amber-300">canary</span>
                  )}
                  {Boolean(item.metadata?.pinned) && (
                    <span className="text-[10px] uppercase text-amber-300">pinned</span>
                  )}
                  <span className="ml-auto text-[10px] text-slate-500" title={formatDateTimeTz(item.created_at_utc, { timeZone: "UTC", placeholder: "--" })}>
                    {timeAgo(item.created_at_utc)}
                  </span>
                </div>
                <div className="text-sm font-medium text-slate-200">{item.title}</div>
                <div className="mt-1 text-xs text-slate-400 line-clamp-2">{item.summary || item.full_message}</div>
              </button>
            );
          })}
          {hasMore && (
            <button
              type="button"
              onClick={() => void loadOlder()}
              disabled={loadingMore}
              className="mt-2 w-full rounded border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800/80 disabled:opacity-60"
            >
              {loadingMore ? "Loading older..." : "Load older"}
            </button>
          )}
          {!hasMore && items.length > 0 && (
            <div className="mt-2 rounded border border-slate-800 bg-slate-950/40 px-3 py-2 text-center text-[11px] text-slate-500">
              End of retained history
            </div>
          )}
        </div>

        <div className="min-h-[240px] overflow-hidden rounded border border-slate-800 bg-slate-950/50">
          {!selected ? (
            <div className="p-4 text-sm text-slate-500">Select a notification/event to view full details.</div>
          ) : (
            <div className="flex h-full flex-col">
              <div className="border-b border-slate-800 bg-slate-900/70 px-4 py-3">
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
                  <span
                    className="text-[10px] text-slate-500"
                    title={`UTC: ${formatDateTimeTz(selected.created_at_utc, { timeZone: "UTC", placeholder: "--" })}`}
                  >
                    Local: {formatDateTimeTz(selected.created_at_utc, { placeholder: "--" })}
                  </span>
                </div>
                <div className="text-sm font-semibold text-slate-100">{selected.title}</div>
                <div className="mt-1 text-[10px] font-mono text-slate-500">
                  id: {selected.id} | kind: {selected.kind} | status: {selected.status}
                </div>
                {handoffResult && <div className="mt-2 text-xs text-cyan-300">{handoffResult}</div>}
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto p-4 scrollbar-thin">
                {selectedCanary && (
                  <div className="space-y-2 rounded border border-cyan-900/60 bg-cyan-950/20 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-[11px] uppercase tracking-wide text-cyan-200">Delivery Health Canary</div>
                      <span className={`rounded border px-2 py-0.5 text-[10px] uppercase ${canaryStatusClasses(selectedCanary.status)}`}>
                        {selectedCanary.status || "unknown"}
                      </span>
                    </div>

                    {(selectedCanary.failingSources.length > 0 || selectedCanary.degradedSources.length > 0) && (
                      <div className="flex flex-wrap gap-2 text-[11px]">
                        {selectedCanary.failingSources.map((source) => (
                          <span
                            key={`failing-${source}`}
                            className="rounded border border-rose-700/60 bg-rose-950/30 px-2 py-0.5 text-rose-200"
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
                          className="rounded border border-cyan-700/50 bg-cyan-500/10 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-500/20"
                        >
                          Copy Primary Runbook
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => { window.location.href = "/dashboard/csi"; }}
                        className="rounded border border-slate-700 bg-slate-900/70 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-800/80"
                      >
                        View in CSI
                      </button>
                    </div>

                    {selectedCanary.steps.length > 0 && (
                      <div className="space-y-1">
                        <div className="text-[11px] uppercase tracking-wide text-slate-300">Guided Remediation</div>
                        <div className="space-y-2">
                          {selectedCanary.steps.map((step, index) => {
                            const stepCommand = String(step.runbook_command || "").trim();
                            const stepSource = String(step.source || "").trim();
                            const stepSeverity = String(step.severity || "warning").trim().toLowerCase();
                            return (
                              <div key={`step-${index}`} className="rounded border border-slate-800 bg-slate-900/50 p-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-[11px] text-slate-100">{step.title || step.code || "Remediation Step"}</span>
                                  <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${canaryStatusClasses(stepSeverity)}`}>
                                    {stepSeverity}
                                  </span>
                                  {stepSource && <span className="text-[10px] text-slate-400">{stepSource}</span>}
                                  {stepCommand && (
                                    <button
                                      type="button"
                                      onClick={() => void copyCommand(stepCommand)}
                                      className="ml-auto rounded border border-cyan-700/50 bg-cyan-500/10 px-2 py-0.5 text-[10px] text-cyan-200 hover:bg-cyan-500/20"
                                    >
                                      Copy Command
                                    </button>
                                  )}
                                </div>
                                {step.action && <div className="mt-1 text-[11px] text-slate-300">{step.action}</div>}
                                {step.detail && <div className="mt-1 text-[11px] text-slate-400">{step.detail}</div>}
                                {stepCommand && (
                                  <pre className="mt-2 rounded border border-slate-800 bg-slate-950/50 p-2 text-[10px] text-slate-300 overflow-x-auto whitespace-pre-wrap">
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
                  <div className="text-[11px] uppercase tracking-wide text-slate-400">Message</div>
                  <div className="rounded border border-slate-800 bg-slate-900/50 p-3 text-xs whitespace-pre-wrap text-slate-300">
                    {selected.full_message || selected.summary}
                  </div>
                </div>

                {Array.isArray(selected.actions) && selected.actions.length > 0 && (
                  <div className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Actions</div>
                    <div className="flex flex-wrap gap-2">
                      {selected.actions.map((action, idx) => (
                        <button
                          key={`${selected.id}-action-${idx}`}
                          type="button"
                          onClick={() => openAction(action)}
                          className="rounded border border-cyan-700/50 bg-cyan-500/10 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-500/20"
                        >
                          {action.label || action.id || "Action"}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {selected.entity_ref && Object.keys(selected.entity_ref).length > 0 && (
                  <div className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Entity Ref</div>
                    <pre className="rounded border border-slate-800 bg-slate-900/50 p-3 text-[10px] text-slate-300 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(selected.entity_ref, null, 2)}
                    </pre>
                  </div>
                )}

                {selected.metadata && Object.keys(selected.metadata).length > 0 && (
                  <div className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-slate-400">Metadata</div>
                    <pre className="rounded border border-slate-800 bg-slate-900/50 p-3 text-[10px] text-slate-300 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(selected.metadata, null, 2)}
                    </pre>
                  </div>
                )}

                <div className="space-y-1">
                  <div className="text-[11px] uppercase tracking-wide text-slate-400">Activity Audit</div>
                  {auditLoading ? (
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-400">Loading audit...</div>
                  ) : auditError ? (
                    <div className="rounded border border-rose-800/60 bg-rose-950/20 p-3 text-xs text-rose-300">{auditError}</div>
                  ) : auditRows.length === 0 ? (
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-500">No actions recorded yet.</div>
                  ) : (
                    <div className="space-y-1">
                      {auditRows.map((row) => (
                        <div key={`${selected.id}-audit-${row.id}`} className="rounded border border-slate-800 bg-slate-900/40 p-2">
                          <div className="flex items-center gap-2 text-[10px] text-slate-400">
                            <span className="uppercase text-cyan-300">{row.action}</span>
                            <span className="uppercase">{row.outcome}</span>
                            <span>{row.actor}</span>
                            <span className="ml-auto">{formatDateTimeTz(row.created_at_utc, { placeholder: "--" })}</span>
                          </div>
                          {row.note && <div className="mt-1 text-[11px] text-slate-300 whitespace-pre-wrap">{row.note}</div>}
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4">
          <div className="w-full max-w-xl rounded-lg border border-slate-700 bg-slate-900 p-4">
            <h2 className="text-sm font-semibold text-slate-100">Send to Simone</h2>
            <p className="mt-1 text-xs text-slate-400">
              Add context for why this item should be investigated. This message is attached to the forwarded event.
            </p>
            <textarea
              value={handoffInstruction}
              onChange={(event) => setHandoffInstruction(event.target.value)}
              rows={7}
              className="mt-3 w-full rounded border border-slate-700 bg-slate-950/70 p-2 text-xs text-slate-100 outline-none focus:border-cyan-500"
              placeholder="Explain what Simone should do with this report/event."
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setHandoffOpen(false)}
                className="rounded border border-slate-700 bg-slate-800/70 px-3 py-1.5 text-xs text-slate-200"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submitHandoff()}
                disabled={handoffBusy}
                className="rounded border border-cyan-700/60 bg-cyan-600/20 px-3 py-1.5 text-xs text-cyan-100 disabled:opacity-60"
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
