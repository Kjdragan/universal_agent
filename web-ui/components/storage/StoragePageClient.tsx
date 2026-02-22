"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArtifactsTable } from "@/components/storage/ArtifactsTable";
import { ExplorerPanel } from "@/components/storage/ExplorerPanel";
import { SessionsTable } from "@/components/storage/SessionsTable";
import { StorageSyncBadge } from "@/components/storage/StorageSyncBadge";
import { useAgentStore } from "@/lib/store";
import {
  StorageArtifactItem,
  StorageOverview,
  StorageRootSource,
  StorageSessionItem,
  StorageSyncState,
} from "@/types/agent";

type StorageTab = "sessions" | "artifacts" | "explorer";
type SessionSourceFilter = "all" | "web" | "hook" | "telegram" | "vp";
type ExplorerScope = "workspaces" | "artifacts";
type RootSourceFilter = StorageRootSource;

function getNetworkRouteLabel(): "Tailscale" | "Public" {
  if (typeof window === "undefined") return "Public";
  const host = (window.location.hostname || "").toLowerCase();
  if (host.endsWith(".ts.net") || host.startsWith("100.")) {
    return "Tailscale";
  }
  return "Public";
}

function normalizeTab(value: string | null): StorageTab {
  if (value === "artifacts" || value === "explorer") return value;
  return "sessions";
}

function normalizeSource(value: string | null): SessionSourceFilter {
  if (value === "web" || value === "hook" || value === "telegram" || value === "vp") return value;
  return "all";
}

function normalizeScope(value: string | null): ExplorerScope {
  if (value === "artifacts") return "artifacts";
  return "workspaces";
}

function defaultRootSource(): RootSourceFilter {
  if (typeof window === "undefined") return "local";
  const host = (window.location.hostname || "").toLowerCase();
  if (host === "localhost" || host === "127.0.0.1" || host === "::1") {
    return "local";
  }
  return "mirror";
}

function normalizeRootSource(value: string | null, fallback: RootSourceFilter): RootSourceFilter {
  if (value === "mirror" || value === "local") return value;
  return fallback;
}

function parentDirectory(path: string): string {
  const normalized = String(path || "").trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!normalized) return "";
  const parts = normalized.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}

export function StoragePageClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const setStorageSyncState = useAgentStore((s) => s.setStorageSyncState);

  const [overview, setOverview] = useState<StorageOverview | null>(null);
  const [sessions, setSessions] = useState<StorageSessionItem[]>([]);
  const [artifacts, setArtifacts] = useState<StorageArtifactItem[]>([]);

  const [syncing, setSyncing] = useState(false);
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingArtifacts, setLoadingArtifacts] = useState(true);
  const [error, setError] = useState("");

  const [optimisticSyncState, setOptimisticSyncState] = useState<StorageSyncState | null>(null);
  const defaultRoot = useMemo(() => defaultRootSource(), []);

  const activeTab = normalizeTab(searchParams.get("tab"));
  const sourceFilter = normalizeSource(searchParams.get("source"));
  const explorerScope = normalizeScope(searchParams.get("scope"));
  const explorerPath = searchParams.get("path") || "";
  const rootSource = normalizeRootSource(searchParams.get("root_source"), defaultRoot);
  const networkRoute = useMemo(() => getNetworkRouteLabel(), []);

  const syncState = optimisticSyncState || overview?.sync_state || "unknown";

  const updateQuery = (next: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(next).forEach(([key, value]) => {
      if (!value) params.delete(key);
      else params.set(key, value);
    });
    const query = params.toString();
    router.replace(query ? `/storage?${query}` : "/storage");
  };

  const refreshOverview = useCallback(async () => {
    try {
      const res = await fetch(`/api/vps/storage/overview?root_source=${rootSource}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      const parsed = data as StorageOverview;
      setOverview(parsed);
      setStorageSyncState(parsed.sync_state, parsed.pending_ready_count, Date.now());
      setError("");
    } catch (err: any) {
      setError(err?.message || "Failed to load storage overview");
      setStorageSyncState("unknown", 0, Date.now());
    } finally {
      setLoadingOverview(false);
      setOptimisticSyncState(null);
    }
  }, [rootSource, setStorageSyncState]);

  const refreshSessions = useCallback(async (source: SessionSourceFilter) => {
    setLoadingSessions(true);
    try {
      const res = await fetch(`/api/vps/storage/sessions?source=${source}&limit=200&root_source=${rootSource}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      setSessions(Array.isArray(data?.sessions) ? (data.sessions as StorageSessionItem[]) : []);
    } catch (err: any) {
      setSessions([]);
      setError(err?.message || "Failed to load sessions");
    } finally {
      setLoadingSessions(false);
    }
  }, [rootSource]);

  const refreshArtifacts = useCallback(async () => {
    setLoadingArtifacts(true);
    try {
      const res = await fetch(`/api/vps/storage/artifacts?limit=200&root_source=${rootSource}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      setArtifacts(Array.isArray(data?.artifacts) ? (data.artifacts as StorageArtifactItem[]) : []);
    } catch (err: any) {
      setArtifacts([]);
      setError(err?.message || "Failed to load artifacts");
    } finally {
      setLoadingArtifacts(false);
    }
  }, [rootSource]);

  useEffect(() => {
    void refreshOverview();
    void refreshArtifacts();
  }, [refreshArtifacts, refreshOverview]);

  useEffect(() => {
    void refreshSessions(sourceFilter);
  }, [sourceFilter, refreshSessions]);

  useEffect(() => {
    let intervalId: number | undefined;

    const schedule = () => {
      if (intervalId) window.clearInterval(intervalId);
      const period = document.visibilityState === "visible" ? 15000 : 60000;
      intervalId = window.setInterval(() => {
        void refreshOverview();
      }, period);
    };

    const onVisibility = () => {
      schedule();
      void refreshOverview();
    };

    schedule();
    window.addEventListener("focus", onVisibility);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      if (intervalId) window.clearInterval(intervalId);
      window.removeEventListener("focus", onVisibility);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refreshOverview]);

  const runSyncNow = async () => {
    if (syncing) return;
    setSyncing(true);
    setOptimisticSyncState("syncing");
    setError("");
    try {
      const res = await fetch("/api/vps/sync/now", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || `Sync failed (${res.status})`);
      }
      await Promise.all([refreshOverview(), refreshSessions(sourceFilter), refreshArtifacts()]);
    } catch (err: any) {
      setError(err?.message || "Sync failed");
      setOptimisticSyncState("error");
    } finally {
      setSyncing(false);
    }
  };

  const openExplorer = (scope: ExplorerScope, path: string) => {
    updateQuery({ tab: "explorer", scope, path: path || null, root_source: rootSource, preview: null });
  };

  const openVpsFile = async (scope: ExplorerScope, path: string) => {
    const normalizedPath = String(path || "").trim().replace(/\\/g, "/");
    if (!normalizedPath) return;

    // VP lane roots often have an empty lane-level run.log while child
    // vp-mission-*/run.log contains the actual transcript.
    if (scope === "workspaces" && /\/run\.log$/i.test(normalizedPath) && /^vp_/i.test(normalizedPath)) {
      try {
        const laneRunResp = await fetch(
          `/api/vps/file?scope=workspaces&root_source=${rootSource}&path=${encodeURIComponent(normalizedPath)}`,
          { cache: "no-store" },
        );
        const laneRunText = laneRunResp.ok ? await laneRunResp.text() : "";
        if (!laneRunText.trim()) {
          const lanePath = normalizedPath.replace(/\/run\.log$/i, "");
          const laneEntriesResp = await fetch(
            `/api/vps/files?scope=workspaces&root_source=${rootSource}&path=${encodeURIComponent(lanePath)}`,
            { cache: "no-store" },
          );
          const laneEntriesData = laneEntriesResp.ok ? await laneEntriesResp.json() : {};
          const laneEntries = Array.isArray(laneEntriesData?.files) ? laneEntriesData.files : [];
          const missions = laneEntries
            .filter((entry: any) => Boolean(entry?.is_dir) && /^vp-mission-/i.test(String(entry?.name || "")))
            .sort((a: any, b: any) => Number(b?.modified || 0) - Number(a?.modified || 0));
          if (missions.length > 0) {
            const candidate = `${lanePath}/${String(missions[0].name)}/run.log`;
            updateQuery({
              tab: "explorer",
              scope: "workspaces",
              path: candidate.replace(/\/run\.log$/i, ""),
              root_source: rootSource,
              preview: candidate,
            });
            return;
          }
        }
      } catch {
        // Fall through to direct explorer preview on any lookup issue.
      }
    }

    updateQuery({
      tab: "explorer",
      scope,
      path: parentDirectory(normalizedPath) || null,
      root_source: rootSource,
      preview: normalizedPath,
    });
  };

  return (
    <main className="h-screen overflow-hidden bg-slate-950 text-slate-100 p-4 md:p-6">
      <div className="mx-auto flex h-full w-full max-w-7xl flex-col gap-4">
        <section className="shrink-0 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-lg font-semibold tracking-tight">Storage</h1>
            <StorageSyncBadge state={syncState} pendingReadyCount={overview?.pending_ready_count || 0} />
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300">
              {networkRoute}
            </span>
            <button
              type="button"
              onClick={() => void runSyncNow()}
              disabled={syncing}
              className="rounded border border-cyan-700 bg-cyan-600/20 px-3 py-2 text-xs uppercase tracking-widest text-cyan-100 disabled:opacity-50"
            >
              {syncing ? "Syncing..." : "Sync now"}
            </button>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => updateQuery({ tab: "sessions" })}
                className={`rounded border px-3 py-2 text-xs uppercase tracking-widest ${activeTab === "sessions" ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-950 text-slate-300"}`}
              >
                Sessions
              </button>
              <button
                type="button"
                onClick={() => updateQuery({ tab: "artifacts" })}
                className={`rounded border px-3 py-2 text-xs uppercase tracking-widest ${activeTab === "artifacts" ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-950 text-slate-300"}`}
              >
                Artifacts
              </button>
              <button
                type="button"
                onClick={() => updateQuery({ tab: "explorer" })}
                className={`rounded border px-3 py-2 text-xs uppercase tracking-widest ${activeTab === "explorer" ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-950 text-slate-300"}`}
              >
                Explorer
              </button>
            </div>
            <span className="text-xs text-slate-400">
              {loadingOverview
                ? "Loading sync overview..."
                : `Pending ready runs: ${overview?.pending_ready_count || 0}${typeof overview?.lag_seconds === "number" ? ` • Lag: ${Math.round(overview.lag_seconds)}s` : ""}${overview?.probe_error ? ` • Probe: ${overview.probe_error}` : ""}`}
            </span>
            <span className="text-xs text-slate-400">
              Root: {rootSource} {overview?.workspace_root ? `• ${overview.workspace_root}` : ""}
            </span>
            <div className="flex items-center gap-2">
              {(["local", "mirror"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => updateQuery({ root_source: option === defaultRoot ? null : option })}
                  className={`rounded border px-2 py-1 text-[10px] uppercase tracking-wider ${rootSource === option ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-950 text-slate-300"}`}
                >
                  {option}
                </button>
              ))}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Link
                href="/"
                className="rounded border border-slate-700 bg-slate-950 px-3 py-2 text-xs uppercase tracking-widest text-slate-200"
              >
                Back to App
              </Link>
            </div>
          </div>
          {error && <div className="mt-3 rounded border border-red-700/60 bg-red-600/10 p-2 text-sm text-red-200">{error}</div>}
        </section>

        {activeTab === "sessions" && (
          <section className="min-h-0 flex-1 space-y-3 overflow-auto">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs uppercase tracking-wider text-slate-400">Source filter</span>
              {(["all", "web", "hook", "telegram", "vp"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => updateQuery({ source: option === "all" ? null : option })}
                  className={`rounded border px-2 py-1 text-xs uppercase tracking-wider ${sourceFilter === option ? "border-emerald-700 bg-emerald-600/20 text-emerald-100" : "border-slate-700 bg-slate-900 text-slate-300"}`}
                >
                  {option}
                </button>
              ))}
            </div>
            <SessionsTable
              sessions={sessions}
              loading={loadingSessions}
              rootSource={rootSource}
              onOpenRoot={(path) => openExplorer("workspaces", path)}
              onOpenRunLog={(path) => { void openVpsFile("workspaces", path); }}
            />
          </section>
        )}

        {activeTab === "artifacts" && (
          <section className="min-h-0 flex-1 overflow-auto">
            <ArtifactsTable
              artifacts={artifacts}
              loading={loadingArtifacts}
              onOpenPath={(path) => openExplorer("artifacts", path)}
              onOpenFile={(path) => { void openVpsFile("artifacts", path); }}
            />
          </section>
        )}

        {activeTab === "explorer" && (
          <section className="min-h-0 flex-1">
            <ExplorerPanel
              initialScope={explorerScope}
              initialPath={explorerPath}
              initialRootSource={rootSource}
              initialPreviewPath={searchParams.get("preview") || ""}
            />
          </section>
        )}
      </div>
    </main>
  );
}
