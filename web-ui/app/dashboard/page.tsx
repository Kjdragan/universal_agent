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
    <p className="mt-1 flex flex-wrap items-start gap-2 text-[10px] text-slate-400">
      <span className="text-slate-500">{label}:</span>
      <span className="min-w-[180px] flex-1 break-all">
        <LinkifiedText text={text} />
      </span>
      {explorerHref && (
        <Link
          href={explorerHref}
          className="rounded border border-blue-500/20 bg-blue-500/5 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.12em] text-blue-300 hover:bg-blue-500/10"
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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, notificationsRes, approvalsHighlightRes, vpSessionsRes, vpMissionsRes, vpMetricResponses] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/summary`),
        fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=30`),
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
  const visibleNotifications = useMemo(
    () =>
      notificationFilter === "unread"
        ? notifications.filter((item) => item.status === "new")
        : notifications,
    [notificationFilter, notifications],
  );
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


  const filteredVpMissions = useMemo(
    () =>
      vpMissions.filter((mission) => {
        if (selectedVpId !== "all" && mission.vp_id !== selectedVpId) return false;
        // Auto-clear missions older than 36 hours
        const ts = mission.updated_at || mission.created_at;
        if (ts) {
          const age = Date.now() - new Date(ts).getTime();
          if (Number.isFinite(age) && age > MISSION_MAX_AGE_MS) return false;
        }
        return true;
      }),
    [selectedVpId, vpMissions],
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
          <p className="text-sm text-slate-400">
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
            className="rounded-lg border border-emerald-700/60 bg-emerald-600/15 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-600/25"
          >
            New Session
          </button>
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
          >
            Refresh
          </button>
        </div>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <article
            key={card.label}
            className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 cursor-pointer transition hover:border-blue-500/30 hover:bg-white/[0.04]"
            onClick={() => handleCardClick(card.label)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleCardClick(card.label); }}
          >
            <p className="text-xs uppercase tracking-[0.16em] text-slate-400">{card.label}</p>
            <p className="mt-2 text-3xl font-semibold text-slate-100">{card.value}</p>
          </article>
        ))}
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">
              External Primary Agent Operations
            </h2>
            <p className="text-[11px] text-slate-500">
              Simone dispatches missions. External workers execute autonomously and report mission events.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedVpId}
              onChange={(event) => setSelectedVpId(event.target.value)}
              className="rounded border border-slate-700 bg-slate-900/70 px-2 py-1 text-xs text-slate-200"
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
              className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60"
            >
              Refresh VP
            </button>
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Active Workers</p>
            <p className="mt-1 text-xl font-semibold text-slate-100">{activeWorkerCount}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Queued</p>
            <p className="mt-1 text-xl font-semibold text-slate-100">{missionCountByStatus.queued}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Running</p>
            <p className="mt-1 text-xl font-semibold text-emerald-200">{missionCountByStatus.running}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Completed</p>
            <p className="mt-1 text-xl font-semibold text-slate-100">{missionCountByStatus.completed}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Failed</p>
            <p className="mt-1 text-xl font-semibold text-rose-200">{missionCountByStatus.failed}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Stalled</p>
            <p className="mt-1 text-xl font-semibold text-amber-200">{missionCountByStatus.stalled}</p>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
          <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Dispatch Mission</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              value={dispatchVpId}
              onChange={(event) => setDispatchVpId(event.target.value)}
              className="rounded border border-slate-700 bg-slate-900/70 px-2 py-1 text-xs text-slate-200"
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
              className="min-w-[240px] flex-1 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-blue-500/50"
            />
            <button
              type="button"
              onClick={dispatchMission}
              disabled={dispatchPending || !dispatchObjective.trim()}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-40"
            >
              {dispatchPending ? "Dispatching..." : "Dispatch"}
            </button>
          </div>
          {(dispatchStatus || vpError) && (
            <p className="mt-2 text-xs text-amber-300">{dispatchStatus || vpError}</p>
          )}
        </div>

        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {visibleVpIds.map((vpId) => {
            const metrics = vpMetrics[vpId];
            const vpSession = vpSessions.find((row) => row.vp_id === vpId);
            const p95Latency = metrics?.latency_seconds?.p95_seconds;
            const workerStatus = String(vpSession?.effective_status || vpSession?.status || metrics?.session?.status || "unknown");
            return (
              <div key={vpId} className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-xs text-slate-300">
                <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">{vpId}</p>
                <p className="mt-1">worker status: {workerStatus}</p>
                <p className="mt-1">session: {vpSession?.session_id || metrics?.session?.session_id || "--"}</p>
                <p className="mt-1">
                  queue/running: {metrics?.mission_counts?.queued ?? 0}/{metrics?.mission_counts?.running ?? 0}
                </p>
                <p className="mt-1">
                  p95 latency:{" "}
                  {typeof p95Latency === "number" ? `${p95Latency.toFixed(1)}s` : "--"}
                </p>
                <p className="mt-1 text-slate-500">
                  heartbeat: {formatLocalDateTime(vpSession?.last_heartbeat_at || vpSession?.updated_at)}
                </p>
              </div>
            );
          })}
        </div>

        <div className="mt-3 rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-xs">
          <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Recent Missions</p>
          <div className="mt-2 space-y-2">
            {filteredVpMissions.slice(0, 10).map((mission) => {
              const missionStatus = String(mission.status || "unknown").toLowerCase();
              const claimTs = mission.claim_expires_at ? new Date(mission.claim_expires_at).getTime() : Number.NaN;
              const updatedTs = mission.updated_at ? new Date(mission.updated_at).getTime() : Number.NaN;
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
              return (
                <div key={mission.mission_id} className="rounded border border-slate-800 bg-slate-900/40 px-2 py-1.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-mono text-[11px] text-slate-300">
                      {mission.mission_id} · {mission.vp_id} · {effectiveStatus}
                    </p>
                    {cancellable && (
                      <button
                        type="button"
                        onClick={() => cancelMission(mission.mission_id)}
                        className="rounded border border-rose-700 bg-rose-900/20 px-2 py-0.5 text-[10px] text-rose-200 hover:bg-rose-900/35"
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-slate-200">{mission.objective || "(no objective)"}</p>
                  <p className="mt-1 text-[10px] text-slate-500">updated: {formatLocalDateTime(mission.updated_at)}</p>
                  <RefLine label="result_ref" value={resultRef} />
                  <RefLine label="result_path" value={resultPath} storagePath={resultPath} />
                  <RefLine label="artifact_relpath" value={artifactRelpath} />
                  <RefLine label="artifact_path" value={artifactPath} storagePath={artifactPath} />
                </div>
              );
            })}
            {filteredVpMissions.length === 0 && (
              <p className="text-slate-500">No VP missions recorded for the current filter.</p>
            )}
          </div>
        </div>

        {recentVpEvents.length > 0 && (
          <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs">
            <div className="flex items-center justify-between">
              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">Recent VP Events</p>
              <button
                type="button"
                onClick={clearAllVpEvents}
                className="rounded border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-400 transition hover:bg-white/[0.06] hover:text-slate-200"
              >
                Clear All
              </button>
            </div>
            <div className="mt-2 space-y-2 text-slate-200">
              {recentVpEvents.map((event, idx) => {
                const eventPayload = asRecord(event.payload);
                const missionId = asText(event.mission_id) || asText(eventPayload.mission_id);
                const vpId = asText(event.vp_id) || asText(eventPayload.vp_id);
                const mission = missionId ? vpMissionById.get(missionId) : undefined;
                const missionStatus = asText(mission?.status);
                const resultRef = asText(mission?.result_ref);
                const resultPath = workspacePathFromResultRef(resultRef);
                const artifactRelpath = asText(eventPayload.artifact_relpath);
                const artifactPath = missionArtifactPath(resultRef, artifactRelpath);
                const workProductsPath = resultPath
                  ? `${resultPath.replace(/\/+$/, "")}/work_products`
                  : "";
                const runLogPath = resultPath
                  ? `${resultPath.replace(/\/+$/, "")}/run.log`
                  : "";
                const receiptRelpath = asText(eventPayload.mission_receipt_relpath);
                const receiptPath = missionArtifactPath(resultRef, receiptRelpath);
                const syncReadyRelpath = asText(eventPayload.sync_ready_marker_relpath);
                const syncReadyPath = missionArtifactPath(resultRef, syncReadyRelpath);
                return (
                  <div
                    key={`${event.event_id || event.created_at || "event"}-${idx}`}
                    className="relative rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-1.5"
                  >
                    {event.event_id && (
                      <button
                        type="button"
                        onClick={() => dismissVpEvent(event.event_id!)}
                        className="absolute top-1 right-1.5 rounded px-1 py-0.5 text-[10px] text-slate-600 transition hover:bg-white/[0.06] hover:text-slate-300"
                        title="Dismiss"
                      >
                        ✕
                      </button>
                    )}
                    <p>
                      {formatLocalDateTime(event.created_at)} · {event.event_type || "event"}
                    </p>
                    <p className="mt-1 text-[10px] text-slate-400">
                      {missionId || "--"} · {vpId || "--"} · {missionStatus || "--"}
                    </p>
                    <RefLine label="result_ref" value={resultRef} />
                    <RefLine label="result_path" value={resultPath} storagePath={resultPath} />
                    <RefLine label="work_products_path" value={workProductsPath} storagePath={workProductsPath} />
                    <RefLine label="run_log_path" value={runLogPath} storagePath={runLogPath} />
                    <RefLine label="artifact_relpath" value={artifactRelpath} />
                    <RefLine label="artifact_path" value={artifactPath} storagePath={artifactPath} />
                    <RefLine label="mission_receipt_relpath" value={receiptRelpath} />
                    <RefLine label="mission_receipt_path" value={receiptPath} storagePath={receiptPath} />
                    <RefLine label="sync_ready_marker_relpath" value={syncReadyRelpath} />
                    <RefLine label="sync_ready_marker_path" value={syncReadyPath} storagePath={syncReadyPath} />
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </section>

      <section ref={sessionSectionRef} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 scroll-mt-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Session Directory</h2>
            <div className="flex rounded-full border border-slate-700 overflow-hidden">
              <button
                type="button"
                onClick={() => setSessionFilter("active")}
                className={[
                  "px-2.5 py-0.5 text-[10px] font-medium transition",
                  sessionFilter === "active"
                    ? "bg-emerald-600/20 text-emerald-200 border-r border-slate-700"
                    : "bg-slate-800/40 text-slate-400 hover:text-slate-200 border-r border-slate-700",
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
                    ? "bg-blue-600/20 text-blue-200"
                    : "bg-slate-800/40 text-slate-400 hover:text-slate-200",
                ].join(" ")}
              >
                All
              </button>
            </div>
          </div>
          <span className="text-xs text-slate-500">
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
                  ? "border-blue-500/30 bg-blue-500/10 text-blue-200"
                  : "border-slate-700 bg-slate-800/40 text-slate-400 hover:text-slate-200",
              ].join(" ")}
            >
              {src}
            </button>
          ))}
        </div>

        {/* Bulk action bar */}
        {selectedSessions.size > 0 && (
          <div className="mb-3 flex items-center gap-3 rounded-lg border border-rose-800/50 bg-rose-950/20 px-3 py-2">
            <span className="text-xs text-rose-200">{selectedSessions.size} selected</span>
            <button
              type="button"
              onClick={bulkDeleteSelected}
              disabled={deletingIds.size > 0}
              className="rounded border border-rose-700 bg-rose-900/25 px-3 py-1 text-[11px] text-rose-200 hover:bg-rose-900/40 disabled:opacity-50 transition"
            >
              Delete Selected
            </button>
            <button
              type="button"
              onClick={() => setSelectedSessions(new Set())}
              className="text-[11px] text-slate-400 hover:text-slate-200"
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
                className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-900 accent-blue-500"
              />
              <span className="text-[11px] text-slate-400">Select all visible</span>
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
            <article key={session.session_id} className={`rounded-lg border p-3 transition ${selectedSessions.has(session.session_id) ? "border-blue-500/30 bg-blue-500/5" : "border-slate-800/80 bg-slate-950/50"} ${deletingIds.has(session.session_id) ? "opacity-40" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <input
                    type="checkbox"
                    checked={selectedSessions.has(session.session_id)}
                    onChange={() => toggleSession(session.session_id)}
                    className="h-3.5 w-3.5 shrink-0 rounded border-slate-600 bg-slate-900 accent-blue-500"
                  />
                  <p className="truncate font-mono text-xs text-slate-200">{session.session_id}</p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-[11px] text-slate-500">{session.status}</span>
                  <button
                    type="button"
                    onClick={() => deleteSession(session.session_id)}
                    disabled={deletingIds.has(session.session_id)}
                    title="Delete session"
                    className="rounded p-0.5 text-rose-400/60 hover:text-rose-300 hover:bg-rose-900/25 transition disabled:opacity-30"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-3.5 w-3.5">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                    </svg>
                  </button>
                </div>
              </div>
              <p className="mt-1 text-[11px] text-slate-400">
                {inferSourceCategory(session)} · {session.owner}
                {session.last_run_source === "heartbeat" && (
                  <span className="ml-1.5 inline-flex items-center gap-0.5 rounded-full border border-pink-800/50 bg-pink-900/20 px-1.5 py-0 text-[9px] text-pink-300">♥ heartbeat</span>
                )}
              </p>
              {session.description ? (
                <p
                  className="mt-1 text-[11px] text-slate-300/90 truncate"
                  title={session.description}
                >
                  {session.description}
                </p>
              ) : (
                <p className="mt-1 text-[11px] text-slate-600 italic truncate">no description yet</p>
              )}
              <p className="mt-1 text-[11px] text-slate-500">
                memory: {session.memory_mode}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                last activity: {formatLocalDateTime(session.last_activity)}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {!session.session_id.startsWith("vp_") && (
                  <button
                    type="button"
                    className="rounded-lg border border-white/[0.06] bg-white/[0.03] px-2 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06]"
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
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-sm text-slate-400">
              {sessionDirectory.length === 0 ? "No sessions discovered yet." : "No sessions match the current filter."}
            </div>
          )}
        </div>
      </section >

      <section ref={notificationSectionRef} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 scroll-mt-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Notification Center</h2>
            {notificationFilter === "unread" && (
              <button
                type="button"
                onClick={() => setNotificationFilter("all")}
                className="flex items-center gap-1 rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-300 hover:bg-white/[0.06] transition"
              >
                Unread only
                <span className="ml-0.5">×</span>
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            {visibleNotifications.length > 0 && (
              <button
                type="button"
                onClick={deleteAllVisibleNotifications}
                disabled={bulkUpdating}
                className="rounded border border-rose-800/70 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
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
                  className="rounded border border-emerald-800/70 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
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
                  className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
                >
                  Dismiss All
                </button>
              </>
            )}
            {loading && <span className="text-xs text-slate-500">Refreshing…</span>}
          </div>
        </div>
        <div className="space-y-2">
          {notifications.length === 0 && (
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-sm text-slate-400">
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
              <div key={item.id} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold">{item.title}</p>
                  <span className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{item.status}</span>
                </div>
                <p className="mt-1 text-sm text-slate-300">
                  <LinkifiedText text={item.message} />
                </p>
                <p className="mt-2 text-[11px] text-slate-500">
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
                        className="rounded border border-blue-800/70 bg-blue-900/20 px-2 py-1 text-[11px] text-blue-200 hover:bg-blue-900/35"
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
                        className="rounded-lg border border-white/[0.06] bg-white/[0.03] px-2 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06]"
                      >
                        View Tutorial Files
                      </Link>
                    )}
                    {reviewHref && (
                      <Link
                        href={reviewHref}
                        className="rounded border border-violet-800/70 bg-violet-900/20 px-2 py-1 text-[11px] text-violet-200 hover:bg-violet-900/35"
                      >
                        View Simone Review
                      </Link>
                    )}
                    {chatHref && (
                      <a
                        href={chatHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded border border-blue-800/70 bg-blue-900/20 px-2 py-1 text-[11px] text-blue-200 hover:bg-blue-900/35"
                      >
                        Open Session
                      </a>
                    )}
                    {!chatHref && relatedChatHref && (
                      <a
                        href={relatedChatHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded border border-blue-800/70 bg-blue-900/20 px-2 py-1 text-[11px] text-blue-200 hover:bg-blue-900/35"
                      >
                        Open Related Session
                      </a>
                    )}
                    {sessionRunLogHref && (
                      <Link
                        href={sessionRunLogHref}
                        className="rounded border border-cyan-800/70 bg-cyan-900/20 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-900/35"
                      >
                        View Run Log
                      </Link>
                    )}
                    {canDispatchTutorial && (
                      <button
                        type="button"
                        className="rounded border border-emerald-800/70 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
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
                      className="rounded border border-emerald-800/70 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
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
                      className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
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
                      className="rounded border border-rose-800/70 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
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
