"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { fetchSessionDirectory, deleteSessionDirectoryEntry, SessionDirectoryItem } from "@/lib/sessionDirectory";
import { LinkifiedText } from "@/components/LinkifiedText";
import { formatDateTimeTz } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";

type SummaryResponse = {
  sessions: { active: number; total: number };
  approvals: { pending: number; total: number };
  cron: { total: number; enabled: number };
  notifications: { unread: number; total: number };
  deployment_profile?: { profile: string };
};

type ApprovalHighlightResponse = {
  status?: string;
  pending_count?: number;
  banner?: {
    show?: boolean;
    text?: string;
    focus_href?: string;
  };
};

type DashboardNotification = {
  id: string;
  title: string;
  kind: string;
  message: string;
  severity: string;
  requires_action?: boolean;
  status: string;
  created_at: string;
  session_id?: string | null;
  metadata?: Record<string, unknown>;
};

type TutorialProgressEntry = {
  notificationId: string;
  kind: string;
  title: string;
  createdAt: string;
  sessionId: string;
};

type VpSessionSnapshot = {
  vp_id: string;
  status?: string;
  effective_status?: string;
  stale?: boolean;
  stale_reason?: string | null;
  session_id?: string;
  worker_id?: string;
  lease_expires_at?: string;
  last_heartbeat_at?: string;
  updated_at?: string;
  last_error?: string | null;
};

type VpMissionSnapshot = {
  mission_id: string;
  vp_id: string;
  mission_type?: string;
  objective?: string;
  status?: string;
  priority?: number;
  cancel_requested?: number;
  result_ref?: string | null;
  claim_expires_at?: string | null;
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number | null;
  payload?: Record<string, unknown> | null;
};

type VpEventSnapshot = {
  event_id?: string;
  mission_id?: string;
  vp_id?: string;
  event_type?: string;
  payload?: Record<string, unknown> | null;
  created_at?: string;
};

type VpMetricsSnapshot = {
  generated_at?: string;
  vp_id?: string;
  session?: VpSessionSnapshot | null;
  mission_counts?: Record<string, number>;
  latency_seconds?: {
    count?: number;
    avg_seconds?: number | null;
    p95_seconds?: number | null;
    max_seconds?: number | null;
  };
  recent_events?: VpEventSnapshot[];
};

const EMPTY_SUMMARY: SummaryResponse = {
  sessions: { active: 0, total: 0 },
  approvals: { pending: 0, total: 0 },
  cron: { total: 0, enabled: 0 },
  notifications: { unread: 0, total: 0 },
  deployment_profile: { profile: "local_workstation" },
};

const VP_IDS = ["vp.coder.primary", "vp.general.primary"] as const;
const VP_STALE_WINDOW_MS = 15 * 60 * 1000;
const MISSION_MAX_AGE_MS = 36 * 60 * 60 * 1000; // 36 hours — auto-hide old missions
const CLEARED_MISSIONS_LS_KEY = "ua.cleared_missions_before";
const VP_STATUS_COLORS: Record<string, { dot: string; text: string; bg: string }> = {
  idle: { dot: "bg-primary", text: "text-primary", bg: "border-primary/25" },
  active: { dot: "bg-sky-400 animate-pulse", text: "text-sky-300", bg: "border-sky-900/40" },
  running: { dot: "bg-sky-400 animate-pulse", text: "text-sky-300", bg: "border-sky-900/40" },
  degraded: { dot: "bg-red-400", text: "text-secondary", bg: "border-red-400/20" },
  stale: { dot: "bg-amber-400", text: "text-accent", bg: "border-amber-900/40" },
  unknown: { dot: "bg-muted-foreground", text: "text-muted-foreground", bg: "border-border" },
};

function formatElapsed(ms: number): string {
  if (ms < 0) return "--";
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return `${min}m ${sec}s`;
  const hr = Math.floor(min / 60);
  const rmMin = min % 60;
  return `${hr}h ${rmMin}m`;
}

// ── Notification filter categories ──────────────────────────────────────────
const NOTIFICATION_CATEGORIES = {
  important: {
    label: "Important",
    icon: "🔔",
    match: (n: DashboardNotification) =>
      n.severity === "error" || n.severity === "critical" ||
      Boolean(n.requires_action) ||
      n.kind === "continuity_alert" || n.kind === "system_error" ||
      n.kind.startsWith("simone_") || n.kind.startsWith("agentmail_"),
  },
  heartbeat: {
    label: "Heartbeat",
    icon: "♥",
    match: (n: DashboardNotification) =>
      n.kind.startsWith("heartbeat_") || n.kind.startsWith("autonomous_heartbeat_") ||
      n.kind === "agentmail_heartbeat_wake_queued",
  },
  csi: {
    label: "CSI",
    icon: "📊",
    match: (n: DashboardNotification) => n.kind.startsWith("csi_"),
  },
  tutorials: {
    label: "Tutorials",
    icon: "🎬",
    match: (n: DashboardNotification) =>
      n.kind.startsWith("tutorial_") || n.kind.startsWith("youtube_"),
  },
  system: {
    label: "System",
    icon: "⚙",
    match: (n: DashboardNotification) =>
      n.kind === "continuity_recovered" || n.kind === "system_command_routed" ||
      n.kind === "cancelled" || n.kind === "calendar_missed" ||
      n.kind === "hook_event",
  },
  simone: {
    label: "Simone",
    icon: "🤖",
    match: (n: DashboardNotification) =>
      n.kind.startsWith("simone_") || n.kind.startsWith("agentmail_"),
  },
} as const;
type NotificationCategoryKey = keyof typeof NOTIFICATION_CATEGORIES | "all";
const NOTIF_CATEGORY_KEYS: NotificationCategoryKey[] = [
  "important", "heartbeat", "csi", "tutorials", "system", "simone", "all",
];
const SEVERITY_OPTIONS = ["all", "info", "warning", "error", "critical"] as const;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function workspacePathFromResultRef(resultRef?: string | null): string {
  const ref = asText(resultRef);
  if (!ref.startsWith("workspace://")) return "";
  return ref.replace("workspace://", "").trim();
}

function missionArtifactPath(resultRef?: string | null, artifactRelpath?: string | null): string {
  const root = workspacePathFromResultRef(resultRef);
  const rel = asText(artifactRelpath);
  if (!root || !rel) return "";
  return `${root.replace(/\/+$/, "")}/${rel.replace(/^\/+/, "")}`;
}

function workspaceExplorerHref(path?: string | null): string {
  const normalized = asText(path).replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!normalized) return "";
  const marker = "AGENT_RUN_WORKSPACES/";
  const markerIdx = normalized.indexOf(marker);
  const relativePath = markerIdx >= 0
    ? normalized.slice(markerIdx + marker.length)
    : (normalized.startsWith("vp_") || normalized.startsWith("session_") || normalized.startsWith("tg_") || normalized.startsWith("api_"))
      ? normalized
      : "";
  if (!relativePath) return "";
  if (!relativePath.includes("/")) return "";
  const params = new URLSearchParams({
    scope: "workspaces",
    path: relativePath,
  });
  return `/storage?${params.toString()}`;
}

function artifactExplorerHref(path?: string | null): string {
  const normalized = asText(path).replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!normalized) return "";
  const params = new URLSearchParams({
    scope: "artifacts",
    path: normalized,
  });
  return `/storage?${params.toString()}`;
}

function chatSessionHref(sessionId?: string | null): string {
  const sid = asText(sessionId);
  if (!sid) return "";
  const params = new URLSearchParams({
    session_id: sid,
    attach: "tail",
    role: "viewer",
  });
  return `/?${params.toString()}`;
}

function parseVideoIdFromHookSession(sessionId: string): string {
  const normalized = asText(sessionId);
  if (!normalized.startsWith("session_hook_yt_")) return "";
  const body = normalized.slice("session_hook_yt_".length);
  const parts = body.split("_");
  if (parts.length < 2) return "";
  return parts[parts.length - 1] || "";
}

function tutorialVideoKey(item: DashboardNotification): string {
  const metadata = asRecord(item.metadata);
  const fromVideoId = asText(metadata.video_id);
  if (fromVideoId) return fromVideoId;
  const fromVideoKey = asText(metadata.video_key);
  if (fromVideoKey) return fromVideoKey;
  return parseVideoIdFromHookSession(asText(item.session_id));
}

function tutorialSessionId(item: DashboardNotification): string {
  const sessionId = asText(item.session_id);
  if (sessionId) return sessionId;
  const metadata = asRecord(item.metadata);
  const hookSessionKey = asText(metadata.hook_session_key);
  if (!hookSessionKey) return "";
  return `session_hook_${hookSessionKey}`;
}

function isTutorialKind(kind: string): boolean {
  return (
    kind.startsWith("youtube_")
    || kind.startsWith("tutorial_")
  );
}

function RefLine({
  label,
  value,
  storagePath,
}: {
  label: string;
  value?: string | null;
  storagePath?: string | null;
}) {
  const text = asText(value);
  if (!text) return null;
  const explorerHref = workspaceExplorerHref(storagePath || "");
  return (
    <p className="mt-1 flex flex-wrap items-start gap-2 text-[10px] text-muted-foreground">
      <span className="text-muted-foreground">{label}:</span>
      <span className="min-w-[180px] flex-1 break-all">
        <LinkifiedText text={text} />
      </span>
      {explorerHref && (
        <Link
          href={explorerHref}
          className="rounded border border-primary/20 bg-primary/5 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.12em] text-primary hover:bg-primary/10"
        >
          Open in Storage
        </Link>
      )}
    </p>
  );
}

function formatLocalDateTime(value?: string | number | null): string {
  return formatDateTimeTz(value, { placeholder: "--" });
}

export default function DashboardPage() {
  const router = useRouter();
  const sessionSectionRef = useRef<HTMLElement>(null);
  const notificationSectionRef = useRef<HTMLElement>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [notifications, setNotifications] = useState<DashboardNotification[]>([]);
  const [sessionDirectory, setSessionDirectory] = useState<SessionDirectoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [bulkUpdating, setBulkUpdating] = useState(false);
  const [sessionFilter, setSessionFilter] = useState<"all" | "active">("active");
  const [notificationFilter, setNotificationFilter] = useState<"all" | "unread">("all");
  const [notifCategoryFilter, setNotifCategoryFilter] = useState<NotificationCategoryKey>(
    () => {
      if (typeof window === "undefined") return "important";
      return (localStorage.getItem("ua.notif_category_filter.v1") as NotificationCategoryKey) || "important";
    },
  );
  const [notifSeverityFilter, setNotifSeverityFilter] = useState<string>(
    () => {
      if (typeof window === "undefined") return "all";
      return localStorage.getItem("ua.notif_severity_filter.v1") || "all";
    },
  );
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set());
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());
  const [vpSessions, setVpSessions] = useState<VpSessionSnapshot[]>([]);
  const [vpMissions, setVpMissions] = useState<VpMissionSnapshot[]>([]);
  const [vpMetrics, setVpMetrics] = useState<Record<string, VpMetricsSnapshot>>({});
  const [vpError, setVpError] = useState<string>("");
  const [selectedVpId, setSelectedVpId] = useState<string>("all");
  const [dispatchVpId, setDispatchVpId] = useState<string>("vp.general.primary");
  const [dispatchObjective, setDispatchObjective] = useState("");
  const [dispatchPending, setDispatchPending] = useState(false);
  const [dispatchStatus, setDispatchStatus] = useState<string>("");
  const [approvalHighlight, setApprovalHighlight] = useState<ApprovalHighlightResponse | null>(null);
  const [tutorialDispatchingId, setTutorialDispatchingId] = useState<string>("");
  const [dismissedVpEventIds, setDismissedVpEventIds] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const stored = localStorage.getItem("ua.dismissed_vp_events.v1");
      return stored ? new Set(JSON.parse(stored)) : new Set();
    } catch { return new Set(); }
  });
  const [expandedMissionEvents, setExpandedMissionEvents] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, notificationsRes, approvalsHighlightRes, vpSessionsRes, vpMissionsRes, vpMetricResponses] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/summary`),
        fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=100`),
        fetch(`${API_BASE}/api/v1/dashboard/approvals/highlight`),
        fetch(`${API_BASE}/api/v1/ops/vp/sessions?status=all&limit=50`),
        fetch(`${API_BASE}/api/v1/ops/vp/missions?status=all&limit=100`),
        Promise.all(
          VP_IDS.map(async (vpId) => {
            const res = await fetch(
              `${API_BASE}/api/v1/ops/metrics/vp?vp_id=${encodeURIComponent(vpId)}&mission_limit=50&event_limit=100`,
            );
            if (!res.ok) return [vpId, null] as const;
            const data = (await res.json()) as VpMetricsSnapshot;
            return [vpId, data] as const;
          }),
        ),
      ]);
      const summaryData = summaryRes.ok
        ? await summaryRes.json()
        : EMPTY_SUMMARY;
      const notificationsData = notificationsRes.ok
        ? await notificationsRes.json()
        : { notifications: [] };
      const approvalsHighlightData = approvalsHighlightRes.ok
        ? await approvalsHighlightRes.json()
        : { pending_count: 0, banner: { show: false, text: "", focus_href: "/dashboard/todolist?mode=personal&focus=approvals" } };
      const vpSessionsData = vpSessionsRes.ok ? await vpSessionsRes.json() : { sessions: [] };
      const vpMissionsData = vpMissionsRes.ok ? await vpMissionsRes.json() : { missions: [] };
      const sessions = await fetchSessionDirectory(120);
      setSummary({
        ...EMPTY_SUMMARY,
        ...(summaryData || {}),
        sessions: {
          ...EMPTY_SUMMARY.sessions,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).sessions) || {}),
        },
        approvals: {
          ...EMPTY_SUMMARY.approvals,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).approvals) || {}),
        },
        cron: {
          ...EMPTY_SUMMARY.cron,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).cron) || {}),
        },
        notifications: {
          ...EMPTY_SUMMARY.notifications,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).notifications) || {}),
        },
      });
      setNotifications(
        Array.isArray(notificationsData.notifications)
          ? notificationsData.notifications.filter(
            (item: DashboardNotification) => item.status !== "dismissed",
          )
          : [],
      );
      setVpSessions(Array.isArray(vpSessionsData.sessions) ? vpSessionsData.sessions : []);
      setVpMissions(Array.isArray(vpMissionsData.missions) ? vpMissionsData.missions : []);
      const nextVpMetrics: Record<string, VpMetricsSnapshot> = {};
      for (const [vpId, snapshot] of vpMetricResponses) {
        if (snapshot) {
          nextVpMetrics[vpId] = snapshot;
        }
      }
      setVpMetrics(nextVpMetrics);
      if (!vpSessionsRes.ok || !vpMissionsRes.ok) {
        setVpError("VP mission status is currently unavailable.");
      } else {
        setVpError("");
      }
      setSessionDirectory(sessions);
      setApprovalHighlight(approvalsHighlightData as ApprovalHighlightResponse);
    } finally {
      setLoading(false);
    }
  }, []);

  const updateNotificationStatus = useCallback(
    async (
      id: string,
      status: "acknowledged" | "snoozed" | "dismissed" | "read",
      note?: string,
      snoozeMinutes?: number,
    ) => {
      setUpdatingId(id);
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, note, snooze_minutes: snoozeMinutes }),
        });
        if (!res.ok) return;
        const data = await res.json();
        const updated = data.notification as DashboardNotification;
        setNotifications((prev) => {
          if (updated.status === "dismissed") {
            return prev.filter((item) => item.id !== id);
          }
          return prev.map((item) => (item.id === id ? updated : item));
        });
      } finally {
        setUpdatingId((prev) => (prev === id ? null : prev));
      }
    },
    [],
  );

  const bulkUpdateContinuityAlerts = useCallback(
    async (
      status: "acknowledged" | "snoozed" | "dismissed",
      note: string,
      snoozeMinutes?: number,
    ) => {
      setBulkUpdating(true);
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/bulk`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status,
            note,
            kind: "continuity_alert",
            current_status: "new",
            snooze_minutes: snoozeMinutes,
            limit: 500,
          }),
        });
        if (!res.ok) return;
        await load();
      } finally {
        setBulkUpdating(false);
      }
    },
    [load],
  );

  const dispatchTutorialToSimone = useCallback(
    async (notificationId: string, runPath: string) => {
      const normalizedRunPath = asText(runPath);
      if (!normalizedRunPath) return;
      setTutorialDispatchingId(notificationId);
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/tutorials/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_path: normalizedRunPath }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          const detail = asText((payload as Record<string, unknown>).detail) || `Dispatch failed (${res.status})`;
          throw new Error(detail);
        }
        await updateNotificationStatus(
          notificationId,
          "acknowledged",
          "tutorial sent to Simone from notification center",
        );
        await load();
      } catch (error) {
        console.error("Failed to dispatch tutorial review", error);
      } finally {
        setTutorialDispatchingId("");
      }
    },
    [load, updateNotificationStatus],
  );

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  // Persist notification filter preferences
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("ua.notif_category_filter.v1", notifCategoryFilter);
    }
  }, [notifCategoryFilter]);
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("ua.notif_severity_filter.v1", notifSeverityFilter);
    }
  }, [notifSeverityFilter]);

  const handleCardClick = useCallback(
    (label: string) => {
      switch (label) {
        case "Active Sessions":
          setSessionFilter("active");
          sessionSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        case "Pending Approvals":
          router.push("/dashboard/approvals");
          break;
        case "Unread Alerts":
          setNotificationFilter("unread");
          notificationSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        case "Enabled Cron Jobs":
          router.push("/dashboard/cron-jobs");
          break;
      }
    },
    [router],
  );

  const cards = useMemo(
    () => [
      { label: "Active Sessions", value: summary?.sessions?.active ?? 0 },
      { label: "Pending Approvals", value: summary?.approvals?.pending ?? 0 },
      { label: "Unread Alerts", value: summary?.notifications?.unread ?? 0 },
      { label: "Enabled Cron Jobs", value: summary?.cron?.enabled ?? 0 },
    ],
    [summary],
  );
  const openContinuityAlerts = useMemo(
    () =>
      notifications.filter(
        (item) => item.kind === "continuity_alert" && item.status === "new",
      ),
    [notifications],
  );
  const visibleNotifications = useMemo(() => {
    let filtered = notifications;
    // Status filter (unread only)
    if (notificationFilter === "unread") {
      filtered = filtered.filter((item) => item.status === "new");
    }
    // Category filter
    if (notifCategoryFilter !== "all") {
      const cat = NOTIFICATION_CATEGORIES[notifCategoryFilter];
      if (cat) filtered = filtered.filter(cat.match);
    }
    // Severity filter
    if (notifSeverityFilter !== "all") {
      filtered = filtered.filter((item) => item.severity === notifSeverityFilter);
    }
    return filtered;
  }, [notificationFilter, notifCategoryFilter, notifSeverityFilter, notifications]);

  const categoryBadgeCounts = useMemo(() => {
    const base = notificationFilter === "unread"
      ? notifications.filter((item) => item.status === "new")
      : notifications;
    const counts: Record<string, number> = { all: base.length };
    for (const [key, cat] of Object.entries(NOTIFICATION_CATEGORIES)) {
      counts[key] = base.filter(cat.match).length;
    }
    return counts;
  }, [notificationFilter, notifications]);
  const tutorialProgressByVideo = useMemo(() => {
    const index = new Map<string, TutorialProgressEntry>();
    for (const item of notifications) {
      if (!isTutorialKind(item.kind)) continue;
      const videoKey = tutorialVideoKey(item);
      if (!videoKey) continue;
      const existing = index.get(videoKey);
      if (!existing) {
        index.set(videoKey, {
          notificationId: item.id,
          kind: item.kind,
          title: item.title,
          createdAt: item.created_at,
          sessionId: tutorialSessionId(item),
        });
        continue;
      }
      const nextTs = new Date(item.created_at).getTime();
      const prevTs = new Date(existing.createdAt).getTime();
      if (Number.isFinite(nextTs) && Number.isFinite(prevTs) && nextTs <= prevTs) continue;
      index.set(videoKey, {
        notificationId: item.id,
        kind: item.kind,
        title: item.title,
        createdAt: item.created_at,
        sessionId: tutorialSessionId(item),
      });
    }
    return index;
  }, [notifications]);

  const deleteAllVisibleNotifications = useCallback(async () => {
    const targetCount = visibleNotifications.length;
    if (targetCount === 0) return;
    if (!window.confirm(`Delete ${targetCount} notification${targetCount > 1 ? "s" : ""}?`)) return;
    setBulkUpdating(true);
    try {
      const body: Record<string, unknown> = {
        status: "dismissed",
        note: "deleted in dashboard bulk action",
        limit: 1000,
      };
      if (notificationFilter === "unread") {
        body.current_status = "new";
      }
      const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) return;
      await load();
    } finally {
      setBulkUpdating(false);
    }
  }, [load, notificationFilter, visibleNotifications.length]);
  const vpIds = useMemo(() => {
    const ids = new Set<string>(VP_IDS);
    for (const row of vpSessions) {
      if (row?.vp_id) ids.add(row.vp_id);
    }
    for (const row of vpMissions) {
      if (row?.vp_id) ids.add(row.vp_id);
    }
    return Array.from(ids);
  }, [vpMissions, vpSessions]);


  // "Clear All" support for Recent Missions panel
  const [clearedMissionsBefore, setClearedMissionsBefore] = useState<string | null>(null);
  useEffect(() => {
    try {
      const stored = localStorage.getItem(CLEARED_MISSIONS_LS_KEY);
      if (stored) setClearedMissionsBefore(stored);
    } catch { /* localStorage unavailable */ }
  }, []);
  const handleClearMissions = useCallback(() => {
    const now = new Date().toISOString();
    try { localStorage.setItem(CLEARED_MISSIONS_LS_KEY, now); } catch { /* ignore */ }
    setClearedMissionsBefore(now);
  }, []);

  const filteredVpMissions = useMemo(
    () => {
      const clearedTs = clearedMissionsBefore ? new Date(clearedMissionsBefore).getTime() : null;
      return vpMissions.filter((mission) => {
        if (selectedVpId !== "all" && mission.vp_id !== selectedVpId) return false;
        // Auto-clear missions older than 36 hours
        const ts = mission.updated_at || mission.created_at;
        if (ts) {
          const age = Date.now() - new Date(ts).getTime();
          if (Number.isFinite(age) && age > MISSION_MAX_AGE_MS) return false;
          // Hide missions cleared by user
          if (clearedTs && new Date(ts).getTime() <= clearedTs) return false;
        }
        return true;
      });
    },
    [selectedVpId, vpMissions, clearedMissionsBefore],
  );

  const filteredVpSessions = useMemo(
    () =>
      vpSessions.filter((session) =>
        selectedVpId === "all" ? true : session.vp_id === selectedVpId,
      ),
    [selectedVpId, vpSessions],
  );

  const vpMissionById = useMemo(() => {
    const index = new Map<string, VpMissionSnapshot>();
    for (const mission of vpMissions) {
      if (mission.mission_id) {
        index.set(mission.mission_id, mission);
      }
    }
    return index;
  }, [vpMissions]);

  const missionCountByStatus = useMemo(() => {
    const counts = {
      queued: 0,
      running: 0,
      stalled: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    };
    for (const mission of filteredVpMissions) {
      const status = String(mission.status || "unknown").toLowerCase();
      if (status === "running") {
        const claimTs = mission.claim_expires_at ? new Date(mission.claim_expires_at).getTime() : Number.NaN;
        const updatedTs = mission.updated_at ? new Date(mission.updated_at).getTime() : Number.NaN;
        const staleByClaim = Number.isFinite(claimTs) && claimTs < Date.now();
        const staleByNoClaim = !Number.isFinite(claimTs) && Number.isFinite(updatedTs) && Date.now() - updatedTs > VP_STALE_WINDOW_MS;
        if (staleByClaim || staleByNoClaim) {
          counts.stalled += 1;
          continue;
        }
      }
      if (status in counts) {
        counts[status as keyof typeof counts] += 1;
      }
    }
    return counts;
  }, [filteredVpMissions]);

  const visibleVpIds = useMemo(
    () => (selectedVpId === "all" ? vpIds : vpIds.filter((vpId) => vpId === selectedVpId)),
    [selectedVpId, vpIds],
  );

  const activeWorkerCount = useMemo(
    () =>
      filteredVpSessions.filter((session) => {
        if (session.stale === true) return false;
        const effectiveStatus = String(session.effective_status || "").toLowerCase();
        if (effectiveStatus) {
          return ["active", "running", "healthy"].includes(effectiveStatus);
        }
        if (!["active", "running", "healthy"].includes(String(session.status || "").toLowerCase())) {
          return false;
        }
        const heartbeatTs = session.last_heartbeat_at ? new Date(session.last_heartbeat_at).getTime() : Number.NaN;
        const updatedTs = session.updated_at ? new Date(session.updated_at).getTime() : Number.NaN;
        if (Number.isFinite(heartbeatTs) && Date.now() - heartbeatTs <= VP_STALE_WINDOW_MS) return true;
        if (Number.isFinite(updatedTs) && Date.now() - updatedTs <= VP_STALE_WINDOW_MS) return true;
        return false;
      }).length,
    [filteredVpSessions],
  );

  const recentVpEvents = useMemo(() => {
    const events: VpEventSnapshot[] = [];
    for (const vpId of vpIds) {
      if (selectedVpId !== "all" && selectedVpId !== vpId) continue;
      const metrics = vpMetrics[vpId];
      if (metrics?.recent_events?.length) {
        events.push(...metrics.recent_events);
      }
    }
    return events
      .slice()
      .filter((e) => !dismissedVpEventIds.has(e.event_id || ""))
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
      .slice(0, 6);
  }, [selectedVpId, vpIds, vpMetrics, dismissedVpEventIds]);

  const dismissVpEvent = useCallback((eventId: string) => {
    setDismissedVpEventIds((prev) => {
      const next = new Set(prev);
      next.add(eventId);
      try { localStorage.setItem("ua.dismissed_vp_events.v1", JSON.stringify([...next])); } catch {}
      return next;
    });
  }, []);

  const clearAllVpEvents = useCallback(() => {
    const allIds = new Set(dismissedVpEventIds);
    for (const vpId of vpIds) {
      const metrics = vpMetrics[vpId];
      if (metrics?.recent_events?.length) {
        for (const e of metrics.recent_events) {
          if (e.event_id) allIds.add(e.event_id);
        }
      }
    }
    setDismissedVpEventIds(allIds);
    try { localStorage.setItem("ua.dismissed_vp_events.v1", JSON.stringify([...allIds])); } catch {}
  }, [dismissedVpEventIds, vpIds, vpMetrics]);

  const dispatchMission = useCallback(async () => {
    const objective = dispatchObjective.trim();
    if (!objective) return;
    setDispatchPending(true);
    setDispatchStatus("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/vp/missions/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vp_id: dispatchVpId,
          mission_type: "task",
          objective,
          source_session_id: "ops.dashboard",
          reply_mode: "async",
        }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setDispatchStatus(String(payload?.detail || `Dispatch failed (${res.status})`));
        return;
      }
      const data = await res.json();
      setDispatchStatus(`Queued mission ${data?.mission?.mission_id || ""}`.trim());
      setDispatchObjective("");
      await load();
    } finally {
      setDispatchPending(false);
    }
  }, [dispatchObjective, dispatchVpId, load]);

  const cancelMission = useCallback(
    async (missionId: string) => {
      if (!missionId) return;
      const confirmed = window.confirm(`Cancel mission ${missionId}?`);
      if (!confirmed) return;
      const res = await fetch(`${API_BASE}/api/v1/ops/vp/missions/${encodeURIComponent(missionId)}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "cancelled from dashboard" }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setDispatchStatus(String(payload?.detail || `Cancel failed (${res.status})`));
        return;
      }
      setDispatchStatus(`Cancel requested for ${missionId}`);
      await load();
    },
    [load],
  );

  const SOURCE_FILTERS = ["all", "chat", "cron", "telegram", "hook", "local", "api", "heartbeat"] as const;

  const inferSourceCategory = useCallback((session: SessionDirectoryItem) => {
    // Heartbeat runs on existing sessions; detect via last_run_source metadata
    if (session.last_run_source === "heartbeat") return "heartbeat";
    const sid = session.session_id.toLowerCase();
    if (sid.startsWith("tg_")) return "telegram";
    if (sid.startsWith("session_hook_")) return "hook";
    if (sid.startsWith("session_")) return "chat";
    if (sid.startsWith("cron_")) return "cron";
    if (sid.startsWith("api_")) return "api";
    return "local";
  }, []);

  const filteredSessions = useMemo(() => {
    let list = sessionDirectory;
    if (sessionFilter === "active") {
      list = list.filter((s) => s.status === "active");
    }
    if (sourceFilter !== "all") {
      list = list.filter((s) => inferSourceCategory(s) === sourceFilter);
    }
    const sorted = [...list];
    sorted.sort((a, b) => {
      const aTs = Date.parse(a.last_activity || "") || 0;
      const bTs = Date.parse(b.last_activity || "") || 0;
      if (aTs !== bTs) return bTs - aTs;
      return b.session_id.localeCompare(a.session_id);
    });
    return sorted;
  }, [sessionDirectory, sessionFilter, sourceFilter, inferSourceCategory]);

  const toggleSession = useCallback((id: string) => {
    setSelectedSessions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAllVisible = useCallback(() => {
    const visibleIds = filteredSessions.map((s) => s.session_id);
    setSelectedSessions((prev) => {
      const allSelected = visibleIds.every((id) => prev.has(id));
      if (allSelected) return new Set();
      return new Set(visibleIds);
    });
  }, [filteredSessions]);

  const deleteSession = useCallback(async (id: string) => {
    if (!window.confirm(`Delete session ${id}?`)) return;
    setDeletingIds((prev) => new Set(prev).add(id));
    try {
      await deleteSessionDirectoryEntry(id);
      setSessionDirectory((prev) => prev.filter((s) => s.session_id !== id));
      setSelectedSessions((prev) => { const next = new Set(prev); next.delete(id); return next; });
    } catch (err) {
      console.error("Delete failed:", err);
    } finally {
      setDeletingIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  }, []);

  const bulkDeleteSelected = useCallback(async () => {
    const ids = Array.from(selectedSessions);
    if (ids.length === 0) return;
    if (!window.confirm(`Delete ${ids.length} selected session${ids.length > 1 ? "s" : ""}?`)) return;
    setDeletingIds(new Set(ids));
    const succeeded: string[] = [];
    for (const id of ids) {
      try {
        await deleteSessionDirectoryEntry(id);
        succeeded.push(id);
      } catch { /* continue */ }
    }
    setSessionDirectory((prev) => prev.filter((s) => !succeeded.includes(s.session_id)));
    setSelectedSessions(new Set());
    setDeletingIds(new Set());
  }, [selectedSessions]);

  return (
    <div className="space-y-6">
      {approvalHighlight?.banner?.show ? (
        <section className="sticky top-0 z-20 rounded-xl border border-amber-700/60 bg-amber-950/90 px-3 py-2 text-xs text-amber-100 backdrop-blur">
          <div className="flex items-center justify-between gap-2">
            <div>
              <span className="font-semibold uppercase tracking-wide">Approval Outstanding:</span>{" "}
              {approvalHighlight?.banner?.text || `${approvalHighlight?.pending_count || 0} approval(s) pending`}
            </div>
            <Link
              href={approvalHighlight?.banner?.focus_href || "/dashboard/todolist?mode=personal&focus=approvals"}
              className="rounded border border-amber-600/70 bg-amber-800/25 px-2 py-1 font-semibold uppercase tracking-wide text-amber-100 hover:bg-amber-800/35"
            >
              Review
            </Link>
          </div>
        </section>
      ) : null}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Profile: {summary?.deployment_profile?.profile ?? "local_workstation"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() =>
              openOrFocusChatWindow({
                role: "writer",
                newSession: true,
                focusInput: true,
              })
            }
            className="rounded-lg border border-primary/30/60 bg-primary/15 px-3 py-1.5 text-sm text-primary/80 hover:bg-primary/25"
          >
            New Session
          </button>
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card"
          >
            Refresh
          </button>
        </div>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <article
            key={card.label}
            className="rounded-xl border border-border/40 bg-card/10 p-4 cursor-pointer transition hover:border-primary/30 hover:bg-card/20"
            onClick={() => handleCardClick(card.label)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleCardClick(card.label); }}
          >
            <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{card.label}</p>
            <p className="mt-2 text-3xl font-semibold text-foreground">{card.value}</p>
          </article>
        ))}
      </section>

      <section className="rounded-xl border border-border bg-background/70 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-foreground/80">
              External Primary Agent Operations
            </h2>
            <p className="text-[11px] text-muted-foreground">
              Simone dispatches missions. External workers execute autonomously and report mission events.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedVpId}
              onChange={(event) => setSelectedVpId(event.target.value)}
              className="rounded border border-border bg-background/70 px-2 py-1 text-xs text-foreground"
            >
              <option value="all">all agents</option>
              {vpIds.map((vpId) => (
                <option key={vpId} value={vpId}>
                  {vpId}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={load}
              className="rounded border border-border bg-background/50 px-2 py-1 text-[11px] text-foreground/80 hover:bg-card/60"
            >
              Refresh VP
            </button>
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
          <div className="rounded-lg border border-border/80 bg-background/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Active Workers</p>
            <p className="mt-1 text-xl font-semibold text-foreground">{activeWorkerCount}</p>
          </div>
          <div className="rounded-lg border border-border/80 bg-background/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Queued</p>
            <p className="mt-1 text-xl font-semibold text-foreground">{missionCountByStatus.queued}</p>
          </div>
          <div className="rounded-lg border border-border/80 bg-background/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Running</p>
            <p className="mt-1 text-xl font-semibold text-primary/80">{missionCountByStatus.running}</p>
          </div>
          <div className="rounded-lg border border-border/80 bg-background/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Completed</p>
            <p className="mt-1 text-xl font-semibold text-foreground">{missionCountByStatus.completed}</p>
          </div>
          <div className="rounded-lg border border-border/80 bg-background/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Failed</p>
            <p className="mt-1 text-xl font-semibold text-red-400/80">{missionCountByStatus.failed}</p>
          </div>
          <div className="rounded-lg border border-border/80 bg-background/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Stalled</p>
            <p className="mt-1 text-xl font-semibold text-amber-200">{missionCountByStatus.stalled}</p>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-border/80 bg-background/50 p-3">
          <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Dispatch Mission</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              value={dispatchVpId}
              onChange={(event) => setDispatchVpId(event.target.value)}
              className="rounded border border-border bg-background/70 px-2 py-1 text-xs text-foreground"
            >
              {vpIds.map((vpId) => (
                <option key={vpId} value={vpId}>
                  {vpId}
                </option>
              ))}
            </select>
            <input
              value={dispatchObjective}
              onChange={(event) => setDispatchObjective(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  dispatchMission();
                }
              }}
              placeholder="Objective for external primary agent..."
              className="min-w-[240px] flex-1 rounded-lg border border-border/40 bg-card/15 px-3 py-1.5 text-sm text-foreground placeholder-muted-foreground outline-none focus:border-primary/50"
            />
            <button
              type="button"
              onClick={dispatchMission}
              disabled={dispatchPending || !dispatchObjective.trim()}
              className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary disabled:opacity-40"
            >
              {dispatchPending ? "Dispatching..." : "Dispatch"}
            </button>
          </div>
          {(dispatchStatus || vpError) && (
            <p className="mt-2 text-xs text-accent">{dispatchStatus || vpError}</p>
          )}
        </div>

        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {visibleVpIds.map((vpId) => {
            const metrics = vpMetrics[vpId];
            const vpSession = vpSessions.find((row) => row.vp_id === vpId);
            const p95Latency = metrics?.latency_seconds?.p95_seconds;
            const workerStatus = String(vpSession?.effective_status || vpSession?.status || metrics?.session?.status || "unknown");
            const statusColors = VP_STATUS_COLORS[workerStatus] || VP_STATUS_COLORS.unknown;
            const lastError = vpSession?.last_error || null;
            const leaseExpires = vpSession?.lease_expires_at ? new Date(vpSession.lease_expires_at).getTime() : NaN;
            const leaseSecondsLeft = Number.isFinite(leaseExpires) ? Math.max(0, Math.floor((leaseExpires - Date.now()) / 1000)) : NaN;
            return (
              <div key={vpId} className={`rounded-lg border bg-background/50 p-3 text-xs text-foreground/80 ${statusColors.bg}`}>
                <div className="flex items-center justify-between">
                  <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{vpId}</p>
                  <div className="flex items-center gap-1.5">
                    <span className={`inline-block h-2 w-2 rounded-full ${statusColors.dot}`} />
                    <span className={`text-[11px] font-semibold uppercase ${statusColors.text}`}>{workerStatus}</span>
                  </div>
                </div>
                {lastError && workerStatus === "degraded" && (
                  <div className="mt-2 rounded border border-red-400/25 bg-red-400/10 px-2 py-1.5">
                    <p className="text-[10px] font-medium uppercase tracking-wide text-secondary">Last Error</p>
                    <p className="mt-0.5 text-[11px] text-red-400/80 break-all">{lastError}</p>
                  </div>
                )}
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                  <p className="text-muted-foreground">session</p>
                  <p className="text-right font-mono text-[10px]">{vpSession?.session_id || metrics?.session?.session_id || "--"}</p>
                  <p className="text-muted-foreground">queue / running</p>
                  <p className="text-right">{metrics?.mission_counts?.queued ?? 0} / {metrics?.mission_counts?.running ?? 0}</p>
                  <p className="text-muted-foreground">p95 latency</p>
                  <p className="text-right">{typeof p95Latency === "number" ? `${p95Latency.toFixed(1)}s` : "--"}</p>
                  {Number.isFinite(leaseSecondsLeft) && (
                    <><p className="text-muted-foreground">lease TTL</p>
                    <p className={`text-right ${leaseSecondsLeft < 30 ? "text-secondary" : leaseSecondsLeft < 60 ? "text-accent" : "text-primary"}`}>{formatElapsed(leaseSecondsLeft * 1000)}</p></>
                  )}
                </div>
                <p className="mt-2 text-[10px] text-muted">
                  heartbeat: {formatLocalDateTime(vpSession?.last_heartbeat_at || vpSession?.updated_at)}
                </p>
              </div>
            );
          })}
        </div>

        <div className="mt-3 rounded-lg border border-border/80 bg-background/50 p-3 text-xs">
          <div className="flex items-center justify-between">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Recent Missions</p>
            {filteredVpMissions.length > 0 && (
              <button
                type="button"
                onClick={handleClearMissions}
                className="rounded border border-border bg-card px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-card/50 hover:text-foreground/80 transition"
              >
                Clear All
              </button>
            )}
          </div>
          <div className="mt-2 space-y-2">
            {filteredVpMissions.slice(0, 10).map((mission) => {
              const missionStatus = String(mission.status || "unknown").toLowerCase();
              const claimTs = mission.claim_expires_at ? new Date(mission.claim_expires_at).getTime() : Number.NaN;
              const updatedTs = mission.updated_at ? new Date(mission.updated_at).getTime() : Number.NaN;
              const startedTs = mission.started_at ? new Date(mission.started_at).getTime() : Number.NaN;
              const staleByClaim = missionStatus === "running" && Number.isFinite(claimTs) && claimTs < Date.now();
              const staleByNoClaim =
                missionStatus === "running" &&
                !Number.isFinite(claimTs) &&
                Number.isFinite(updatedTs) &&
                Date.now() - updatedTs > VP_STALE_WINDOW_MS;
              const effectiveStatus = staleByClaim || staleByNoClaim ? "stalled" : missionStatus;
              const cancellable = missionStatus === "queued" || (missionStatus === "running" && effectiveStatus !== "stalled");
              const missionPayload = asRecord(mission.payload);
              const artifactRelpath = asText(missionPayload.artifact_relpath);
              const resultRef = asText(mission.result_ref);
              const resultPath = workspacePathFromResultRef(resultRef);
              const artifactPath = missionArtifactPath(resultRef, artifactRelpath);
              // Restart detection: count started events for this mission
              const restartCount = recentVpEvents.filter(
                (e) => (e.mission_id === mission.mission_id || asText(asRecord(e.payload).mission_id) === mission.mission_id) && e.event_type === "vp.mission.started"
              ).length;
              // Elapsed time for running missions
              const elapsedMs = missionStatus === "running" && Number.isFinite(startedTs) ? Date.now() - startedTs : NaN;
              // Claim lease remaining
              const claimSecsLeft = Number.isFinite(claimTs) ? Math.max(0, Math.floor((claimTs - Date.now()) / 1000)) : NaN;
              // Status styling
              const statusStyle = effectiveStatus === "completed" ? "text-primary"
                : effectiveStatus === "running" ? "text-sky-300"
                : effectiveStatus === "failed" ? "text-secondary"
                : effectiveStatus === "stalled" ? "text-accent"
                : effectiveStatus === "cancelled" ? "text-muted-foreground"
                : "text-muted-foreground";
              return (
                <div key={mission.mission_id} className="rounded border border-border bg-background/40 px-2 py-1.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <p className="font-mono text-[11px] text-foreground/80">
                        {mission.mission_id} · {mission.vp_id}
                      </p>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${statusStyle} bg-white/5`}>{effectiveStatus}</span>
                      {missionStatus === "running" && Number.isFinite(elapsedMs) && (
                        <span className="rounded bg-sky-900/30 px-1.5 py-0.5 text-[10px] font-mono text-sky-200" title={`Started ${formatLocalDateTime(mission.started_at)}`}>⏱ {formatElapsed(elapsedMs)}</span>
                      )}
                      {restartCount > 1 && (
                        <span className="rounded bg-red-400/10 px-1.5 py-0.5 text-[10px] font-semibold text-red-400/80" title={`${restartCount} vp.mission.started events detected — indicates restart loop`}>⟳ {restartCount} restarts</span>
                      )}
                    </div>
                    {cancellable && (
                      <button
                        type="button"
                        onClick={() => cancelMission(mission.mission_id)}
                        className="rounded border border-red-400/30 bg-red-400/10 px-2 py-0.5 text-[10px] text-red-400/80 hover:bg-red-400/20"
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-foreground">{mission.objective || "(no objective)"}</p>
                  {missionStatus === "running" && Number.isFinite(claimSecsLeft) && (
                    <div className="mt-1.5">
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-muted-foreground">claim lease</span>
                        <span className={claimSecsLeft < 30 ? "text-secondary" : claimSecsLeft < 60 ? "text-accent" : "text-primary"}>{formatElapsed(claimSecsLeft * 1000)} remaining</span>
                      </div>
                      <div className="mt-0.5 h-1 w-full rounded-full bg-card">
                        <div
                          className={`h-1 rounded-full transition-all ${claimSecsLeft < 30 ? "bg-red-400" : claimSecsLeft < 60 ? "bg-amber-500" : "bg-primary"}`}
                          style={{ width: `${Math.min(100, (claimSecsLeft / 120) * 100)}%` }}
                        />
                      </div>
                    </div>
                  )}
                  <div className="mt-1 flex flex-wrap gap-3 text-[10px] text-muted-foreground">
                    {mission.started_at && <span>started: {formatLocalDateTime(mission.started_at)}</span>}
                    <span>updated: {formatLocalDateTime(mission.updated_at)}</span>
                    {mission.completed_at && <span>completed: {formatLocalDateTime(mission.completed_at)}</span>}
                    {typeof mission.duration_seconds === "number" && <span>duration: {formatElapsed(mission.duration_seconds * 1000)}</span>}
                  </div>
                  <RefLine label="result_ref" value={resultRef} />
                  <RefLine label="result_path" value={resultPath} storagePath={resultPath} />
                  <RefLine label="artifact_relpath" value={artifactRelpath} />
                  <RefLine label="artifact_path" value={artifactPath} storagePath={artifactPath} />
                  {/* Inline event timeline */}
                  {(() => {
                    const missionEvents = recentVpEvents.filter(
                      (e) => (e.mission_id === mission.mission_id || asText(asRecord(e.payload).mission_id) === mission.mission_id)
                    );
                    if (missionEvents.length === 0) return null;
                    const isExpanded = expandedMissionEvents.has(mission.mission_id);
                    return (
                      <div className="mt-1.5">
                        <button
                          type="button"
                          onClick={() => {
                            setExpandedMissionEvents((prev) => {
                              const next = new Set(prev);
                              if (next.has(mission.mission_id)) next.delete(mission.mission_id);
                              else next.add(mission.mission_id);
                              return next;
                            });
                          }}
                          className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground/80 transition"
                        >
                          <span className="select-none">{isExpanded ? "▾" : "▸"}</span>
                          <span>{missionEvents.length} event{missionEvents.length !== 1 ? "s" : ""}</span>
                        </button>
                        {isExpanded && (
                          <div className="mt-1 ml-2 space-y-1 border-l border-border pl-2">
                            {missionEvents.map((evt, ei) => {
                              const ep = asRecord(evt.payload);
                              const evtResultRef = asText(vpMissionById.get(mission.mission_id)?.result_ref);
                              const evtResultPath = workspacePathFromResultRef(evtResultRef);
                              const wpPath = evtResultPath ? `${evtResultPath.replace(/\/+$/, "")}/work_products` : "";
                              const rlPath = evtResultPath ? `${evtResultPath.replace(/\/+$/, "")}/run.log` : "";
                              const rcptRel = asText(ep.mission_receipt_relpath);
                              const rcptPath = missionArtifactPath(evtResultRef, rcptRel);
                              return (
                                <div key={`${evt.event_id || evt.created_at || "e"}-${ei}`} className="text-[10px] text-muted-foreground">
                                  <span className="text-muted-foreground">{formatLocalDateTime(evt.created_at)}</span>{" "}
                                  <span className="text-foreground/80">{evt.event_type || "event"}</span>
                                  <RefLine label="work_products" value={wpPath} storagePath={wpPath} />
                                  <RefLine label="run_log" value={rlPath} storagePath={rlPath} />
                                  <RefLine label="receipt" value={rcptPath} storagePath={rcptPath} />
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
              );
            })}
            {filteredVpMissions.length === 0 && (
              <p className="text-muted-foreground">No VP missions recorded for the current filter.</p>
            )}
          </div>
        </div>


      </section>

      <section ref={sessionSectionRef} className="rounded-xl border border-border bg-background/70 p-4 scroll-mt-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-foreground/80">Session Directory</h2>
            <div className="flex rounded-full border border-border overflow-hidden">
              <button
                type="button"
                onClick={() => setSessionFilter("active")}
                className={[
                  "px-2.5 py-0.5 text-[10px] font-medium transition",
                  sessionFilter === "active"
                    ? "bg-primary/15 text-primary/80 border-r border-border"
                    : "bg-card/40 text-muted-foreground hover:text-foreground border-r border-border",
                ].join(" ")}
              >
                Active
              </button>
              <button
                type="button"
                onClick={() => setSessionFilter("all")}
                className={[
                  "px-2.5 py-0.5 text-[10px] font-medium transition",
                  sessionFilter === "all"
                    ? "bg-primary/20 text-primary/80"
                    : "bg-card/40 text-muted-foreground hover:text-foreground",
                ].join(" ")}
              >
                All
              </button>
            </div>
          </div>
          <span className="text-xs text-muted-foreground">
            {filteredSessions.length} session{filteredSessions.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Source filter bar */}
        <div className="mb-3 flex flex-wrap gap-1.5">
          {SOURCE_FILTERS.map((src) => (
            <button
              key={src}
              type="button"
              onClick={() => { setSourceFilter(src); setSelectedSessions(new Set()); }}
              className={[
                "rounded-full px-2.5 py-1 text-[11px] capitalize transition border",
                sourceFilter === src
                  ? "border-primary/30 bg-primary/10 text-primary/80"
                  : "border-border bg-card/40 text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              {src}
            </button>
          ))}
        </div>

        {/* Bulk action bar */}
        {selectedSessions.size > 0 && (
          <div className="mb-3 flex items-center gap-3 rounded-lg border border-red-400/25 bg-red-400/10 px-3 py-2">
            <span className="text-xs text-red-400/80">{selectedSessions.size} selected</span>
            <button
              type="button"
              onClick={bulkDeleteSelected}
              disabled={deletingIds.size > 0}
              className="rounded border border-red-400/30 bg-red-400/10 px-3 py-1 text-[11px] text-red-400/80 hover:bg-red-400/15 disabled:opacity-50 transition"
            >
              Delete Selected
            </button>
            <button
              type="button"
              onClick={() => setSelectedSessions(new Set())}
              className="text-[11px] text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          </div>
        )}

        {/* Select all toggle */}
        {filteredSessions.length > 0 && (
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={filteredSessions.length > 0 && filteredSessions.every((s) => selectedSessions.has(s.session_id))}
                onChange={toggleAllVisible}
                className="h-3.5 w-3.5 rounded border-border bg-background accent-blue-500"
              />
              <span className="text-[11px] text-muted-foreground">Select all visible</span>
            </div>

            <button
              onClick={async () => {
                const count = filteredSessions.length;
                if (!window.confirm(`Delete ALL ${count} visible sessions? This cannot be undone.`)) return;
                const ids = filteredSessions.map(s => s.session_id);
                setDeletingIds(new Set(ids));
                for (const id of ids) {
                  try { await deleteSessionDirectoryEntry(id); } catch { /* ignore */ }
                }
                // We rely on state update from directory refresh or just optimistically clear
                setSessionDirectory(prev => prev.filter(s => !ids.includes(s.session_id)));
                setDeletingIds(new Set());
                setSelectedSessions(new Set());
              }}
              className="rounded border border-red-900/50 bg-red-900/10 px-2 py-0.5 text-xs text-red-400 hover:bg-red-900/30 transition"
            >
              Delete All Visible
            </button>
          </div>
        )}

        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {filteredSessions.map((session) => (
            <article key={session.session_id} className={`rounded-lg border p-3 transition ${selectedSessions.has(session.session_id) ? "border-primary/30 bg-primary/5" : "border-border/80 bg-background/50"} ${deletingIds.has(session.session_id) ? "opacity-40" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <input
                    type="checkbox"
                    checked={selectedSessions.has(session.session_id)}
                    onChange={() => toggleSession(session.session_id)}
                    className="h-3.5 w-3.5 shrink-0 rounded border-border bg-background accent-blue-500"
                  />
                  <p className="truncate font-mono text-xs text-foreground">{session.session_id}</p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-[11px] text-muted-foreground">{session.status}</span>
                  <button
                    type="button"
                    onClick={() => deleteSession(session.session_id)}
                    disabled={deletingIds.has(session.session_id)}
                    title="Delete session"
                    className="rounded p-0.5 text-secondary/60 hover:text-secondary hover:bg-red-400/10 transition disabled:opacity-30"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-3.5 w-3.5">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                    </svg>
                  </button>
                </div>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {inferSourceCategory(session)} · {session.owner}
                {session.last_run_source === "heartbeat" && (
                  <span className="ml-1.5 inline-flex items-center gap-0.5 rounded-full border border-secondary/25 bg-secondary/10 px-1.5 py-0 text-[9px] text-secondary">♥ heartbeat</span>
                )}
              </p>
              {session.description ? (
                <p
                  className="mt-1 text-[11px] text-foreground/80/90 truncate"
                  title={session.description}
                >
                  {session.description}
                </p>
              ) : (
                <p className="mt-1 text-[11px] text-muted italic truncate">no description yet</p>
              )}
              <p className="mt-1 text-[11px] text-muted-foreground">
                memory: {session.memory_mode}
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                last activity: {formatLocalDateTime(session.last_activity)}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {!session.session_id.startsWith("vp_") && (
                  <button
                    type="button"
                    className="rounded-lg border border-border/40 bg-card/15 px-2 py-1 text-[11px] text-foreground/80 hover:bg-card/30"
                    onClick={() =>
                      openOrFocusChatWindow({
                        sessionId: session.session_id,
                        attachMode: "tail",
                        role: "writer",
                      })
                    }
                  >
                    Open Writer
                  </button>
                )}
                <button
                  type="button"
                  className="rounded border border-amber-700 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-900/30"
                  onClick={() =>
                    openOrFocusChatWindow({
                      sessionId: session.session_id,
                      attachMode: "tail",
                      role: "viewer",
                    })
                  }
                >
                  Open Viewer
                </button>
              </div>
            </article>
          ))}
          {filteredSessions.length === 0 && (
            <div className="rounded-lg border border-border/80 bg-background/50 p-3 text-sm text-muted-foreground">
              {sessionDirectory.length === 0 ? "No sessions discovered yet." : "No sessions match the current filter."}
            </div>
          )}
        </div>
      </section >

      <section ref={notificationSectionRef} className="rounded-xl border border-border bg-background/70 p-4 scroll-mt-4">
        {/* ── Header row ── */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-foreground/80">Notification Center</h2>
            {/* Unread chip */}
            <button
              type="button"
              onClick={() => setNotificationFilter(notificationFilter === "unread" ? "all" : "unread")}
              className={[
                "rounded-full px-2 py-0.5 text-[10px] font-medium border transition",
                notificationFilter === "unread"
                  ? "border-amber-600/50 bg-amber-500/15 text-amber-200"
                  : "border-border bg-card/40 text-muted-foreground hover:text-foreground/80",
              ].join(" ")}
            >
              Unread only
            </button>
            {/* Severity dropdown */}
            <select
              value={notifSeverityFilter}
              onChange={(e) => setNotifSeverityFilter(e.target.value)}
              className="rounded border border-border bg-background/70 px-2 py-0.5 text-[11px] text-foreground/80"
            >
              {SEVERITY_OPTIONS.map((sev) => (
                <option key={sev} value={sev}>
                  {sev === "all" ? "All severities" : sev}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            {visibleNotifications.length > 0 && (
              <button
                type="button"
                onClick={deleteAllVisibleNotifications}
                disabled={bulkUpdating}
                className="rounded border border-red-400/30 bg-red-400/10 px-2 py-1 text-[11px] text-red-400/80 hover:bg-red-400/20 disabled:opacity-50"
              >
                Delete All ({visibleNotifications.length})
              </button>
            )}
            {openContinuityAlerts.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => bulkUpdateContinuityAlerts("acknowledged", "acknowledged in dashboard bulk action")}
                  disabled={bulkUpdating}
                  className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20 disabled:opacity-50"
                >
                  Ack All Continuity ({openContinuityAlerts.length})
                </button>
                <button
                  type="button"
                  onClick={() => bulkUpdateContinuityAlerts("snoozed", "snoozed in dashboard bulk action", 30)}
                  disabled={bulkUpdating}
                  className="rounded border border-amber-800/70 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                >
                  Snooze All 30m
                </button>
                <button
                  type="button"
                  onClick={() => bulkUpdateContinuityAlerts("dismissed", "dismissed in dashboard bulk action")}
                  disabled={bulkUpdating}
                  className="rounded border border-border bg-background/50 px-2 py-1 text-[11px] text-foreground/80 hover:bg-card/60 disabled:opacity-50"
                >
                  Dismiss All
                </button>
              </>
            )}
            {loading && <span className="text-xs text-muted-foreground">Refreshing…</span>}
          </div>
        </div>

        {/* ── Category filter pill bar ── */}
        <div className="mb-3 flex flex-wrap gap-1.5">
          {NOTIF_CATEGORY_KEYS.map((catKey) => {
            const isAll = catKey === "all";
            const cat = isAll ? null : NOTIFICATION_CATEGORIES[catKey];
            const label = isAll ? "All" : cat!.label;
            const icon = isAll ? "📋" : cat!.icon;
            const count = categoryBadgeCounts[catKey] ?? 0;
            const isActive = notifCategoryFilter === catKey;
            return (
              <button
                key={catKey}
                type="button"
                onClick={() => setNotifCategoryFilter(catKey)}
                className={[
                  "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium border transition",
                  isActive
                    ? catKey === "important"
                      ? "border-accent/40 bg-amber-500/15 text-amber-200"
                      : "border-primary/30 bg-primary/10 text-primary/80"
                    : "border-border bg-card/40 text-muted-foreground hover:text-foreground hover:border-border",
                ].join(" ")}
              >
                <span className="text-[10px]">{icon}</span>
                {label}
                <span className={[
                  "ml-0.5 rounded-full px-1.5 py-0 text-[9px] font-semibold tabular-nums",
                  isActive
                    ? catKey === "important"
                      ? "bg-amber-500/25 text-accent"
                      : "bg-primary/20 text-primary"
                    : "bg-card/50/60 text-muted-foreground",
                ].join(" ")}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>
        <div className="space-y-2">
          {notifications.length === 0 && (
            <div className="rounded-lg border border-border/80 bg-background/50 p-3 text-sm text-muted-foreground">
              No notifications yet.
            </div>
          )}
          {visibleNotifications.map((item) => {
            const metadata = asRecord(item.metadata);
            const tutorialRunPath = asText(metadata.tutorial_run_path);
            const reviewRunPath = asText(metadata.review_run_path);
            const sessionId = asText(item.session_id);
            const videoKey = tutorialVideoKey(item);
            const relatedProgress = videoKey ? tutorialProgressByVideo.get(videoKey) : undefined;
            const relatedSessionId = asText(relatedProgress?.sessionId);
            const effectiveSessionId = sessionId || relatedSessionId;
            const chatHref = chatSessionHref(item.session_id);
            const relatedChatHref = chatSessionHref(effectiveSessionId);
            const sessionRunLogHref = effectiveSessionId
              ? workspaceExplorerHref(`${effectiveSessionId}/run.log`)
              : "";
            const tutorialHref = artifactExplorerHref(tutorialRunPath);
            const reviewHref = artifactExplorerHref(reviewRunPath);
            const metadataRequiresAction =
              metadata.requires_action === true || metadata.requires_user_action === true;
            const requiresAction = Boolean(item.requires_action || metadataRequiresAction);
            const canDispatchTutorial = Boolean(
              tutorialRunPath &&
              requiresAction &&
              item.status === "new" &&
              item.kind !== "tutorial_review_ready" &&
              item.kind !== "tutorial_review_failed",
            );
            const hasSessionAction = Boolean(effectiveSessionId);
            const hasAnyAction = Boolean(
              tutorialHref
              || reviewHref
              || canDispatchTutorial
              || hasSessionAction
              || sessionRunLogHref
              || relatedChatHref,
            );
            return (
              <div key={item.id} className="rounded-lg border border-border/80 bg-background/60 p-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold">{item.title}</p>
                  <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{item.status}</span>
                </div>
                <p className="mt-1 text-sm text-foreground/80">
                  <LinkifiedText text={item.message} />
                </p>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  {item.kind} · {item.session_id || "global"} · {formatLocalDateTime(item.created_at)}
                </p>
                {relatedProgress && relatedProgress.notificationId !== item.id && (
                  <p className="mt-1 text-[11px] text-sky-300">
                    Latest tutorial status: {relatedProgress.title} · {formatLocalDateTime(relatedProgress.createdAt)}
                  </p>
                )}
                {hasAnyAction && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {hasSessionAction && (
                      <button
                        type="button"
                        className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                        onClick={() =>
                          openOrFocusChatWindow({
                            sessionId: effectiveSessionId,
                            attachMode: "tail",
                            role: "viewer",
                          })
                        }
                      >
                        Open Session Viewer
                      </button>
                    )}
                    {tutorialHref && (
                      <Link
                        href={tutorialHref}
                        className="rounded-lg border border-border/40 bg-card/15 px-2 py-1 text-[11px] text-foreground/80 hover:bg-card/30"
                      >
                        View Tutorial Files
                      </Link>
                    )}
                    {reviewHref && (
                      <Link
                        href={reviewHref}
                        className="rounded border border-secondary/30 bg-secondary/10 px-2 py-1 text-[11px] text-secondary/80 hover:bg-secondary/20"
                      >
                        View Simone Review
                      </Link>
                    )}
                    {chatHref && (
                      <a
                        href={chatHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                      >
                        Open Session
                      </a>
                    )}
                    {!chatHref && relatedChatHref && (
                      <a
                        href={relatedChatHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                      >
                        Open Related Session
                      </a>
                    )}
                    {sessionRunLogHref && (
                      <Link
                        href={sessionRunLogHref}
                        className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                      >
                        View Run Log
                      </Link>
                    )}
                    {canDispatchTutorial && (
                      <button
                        type="button"
                        className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20 disabled:opacity-50"
                        onClick={() => dispatchTutorialToSimone(item.id, tutorialRunPath)}
                        disabled={tutorialDispatchingId === item.id}
                      >
                        {tutorialDispatchingId === item.id ? "Queueing..." : "Send to Simone"}
                      </button>
                    )}
                  </div>
                )}
                {item.kind === "continuity_alert" && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20 disabled:opacity-50"
                      onClick={() => updateNotificationStatus(item.id, "acknowledged", "acknowledged in dashboard")}
                      disabled={updatingId === item.id}
                    >
                      Acknowledge
                    </button>
                    <button
                      type="button"
                      className="rounded border border-amber-800/70 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                      onClick={() => updateNotificationStatus(item.id, "snoozed", "snoozed in dashboard", 30)}
                      disabled={updatingId === item.id}
                    >
                      Snooze 30m
                    </button>
                    <button
                      type="button"
                      className="rounded border border-border bg-background/50 px-2 py-1 text-[11px] text-foreground/80 hover:bg-card/60 disabled:opacity-50"
                      onClick={() => updateNotificationStatus(item.id, "dismissed", "dismissed in dashboard")}
                      disabled={updatingId === item.id}
                    >
                      Dismiss
                    </button>
                  </div>
                )}
                {item.kind !== "continuity_alert" && item.status === "new" && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="rounded border border-red-400/30 bg-red-400/10 px-2 py-1 text-[11px] text-red-400/80 hover:bg-red-400/20 disabled:opacity-50"
                      onClick={() => updateNotificationStatus(item.id, "dismissed", "deleted in dashboard")}
                      disabled={updatingId === item.id}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div >
  );
}
