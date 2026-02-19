"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAgentStore } from "@/lib/store";
import { StorageOverview, StorageSessionItem, StorageSyncState } from "@/types/agent";
import { StorageSyncBadge } from "@/components/storage/StorageSyncBadge";

function getNetworkRouteLabel(): "Tailscale" | "Public" {
  if (typeof window === "undefined") return "Public";
  const host = (window.location.hostname || "").toLowerCase();
  if (host.endsWith(".ts.net") || host.startsWith("100.")) {
    return "Tailscale";
  }
  return "Public";
}

function sourceLabel(source: string): string {
  if (source === "web") return "Web/API";
  if (source === "hook") return "Hook";
  if (source === "telegram") return "Telegram";
  return source;
}

function formatEpoch(value?: number | null): string {
  if (!value || !Number.isFinite(value)) return "-";
  return new Date(value * 1000).toLocaleString();
}

export function StorageQuickPanel() {
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const setStorageSyncState = useAgentStore((s) => s.setStorageSyncState);

  const [overview, setOverview] = useState<StorageOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [optimisticState, setOptimisticState] = useState<StorageSyncState | null>(null);

  const networkRoute = useMemo(() => getNetworkRouteLabel(), []);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/vps/storage/overview", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      const parsed = data as StorageOverview;
      setOverview(parsed);
      setError("");
      setStorageSyncState(parsed.sync_state, parsed.pending_ready_count, Date.now());
    } catch (err: any) {
      const message = err?.message || "Failed to load storage overview";
      setError(message);
      setStorageSyncState("unknown", 0, Date.now());
    } finally {
      setLoading(false);
      setOptimisticState(null);
    }
  }, [setStorageSyncState]);

  useEffect(() => {
    let intervalId: number | undefined;

    const schedule = () => {
      if (intervalId) window.clearInterval(intervalId);
      const period = document.visibilityState === "visible" ? 15000 : 60000;
      intervalId = window.setInterval(() => {
        void refresh();
      }, period);
    };

    const onVisibility = () => {
      schedule();
      void refresh();
    };

    void refresh();
    schedule();
    window.addEventListener("focus", onVisibility);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      if (intervalId) window.clearInterval(intervalId);
      window.removeEventListener("focus", onVisibility);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  const handleSyncNow = async () => {
    if (syncing) return;
    setSyncing(true);
    setOptimisticState("syncing");
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
    } catch (err: any) {
      setError(err?.message || "Sync failed");
      setOptimisticState("error");
    } finally {
      setSyncing(false);
      void refresh();
    }
  };

  const syncState = optimisticState || overview?.sync_state || "unknown";

  const openRunLog = (item: StorageSessionItem | null) => {
    if (!item?.run_log_path) return;
    setViewingFile({
      name: "run.log",
      path: item.run_log_path,
      type: "vps_workspace",
    });
  };

  const openArtifactManifest = () => {
    if (!overview?.latest_artifact?.manifest_path) return;
    setViewingFile({
      name: "manifest.json",
      path: overview.latest_artifact.manifest_path,
      type: "vps_artifact",
    });
  };

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-xs">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Storage Quick Access</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300">
            {networkRoute}
          </span>
          <StorageSyncBadge
            state={syncState}
            pendingReadyCount={overview?.pending_ready_count || 0}
          />
          <button
            type="button"
            onClick={() => void handleSyncNow()}
            disabled={syncing}
            className="rounded border border-cyan-700 bg-cyan-600/15 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25 disabled:opacity-50"
          >
            {syncing ? "Syncing" : "Sync Now"}
          </button>
        </div>
      </div>

      {error && <div className="mb-2 rounded border border-red-700/50 bg-red-600/10 p-2 text-red-200">{error}</div>}

      {loading && !overview ? (
        <div className="text-slate-400">Loading storage overview...</div>
      ) : (
        <div className="space-y-2">
          {(["web", "hook", "telegram"] as const).map((source) => {
            const item = overview?.latest_sessions?.[source] ?? null;
            return (
              <div key={source} className="rounded border border-slate-800 bg-slate-950/50 p-2">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-semibold text-slate-200">Latest {sourceLabel(source)} Session</span>
                  <Link href={`/storage?tab=sessions&source=${source}`} className="text-cyan-300 underline">
                    Open
                  </Link>
                </div>
                {item ? (
                  <div className="space-y-1">
                    <div className="font-mono text-[11px] text-slate-300">{item.session_id}</div>
                    <div className="text-[11px] text-slate-400">{item.status} • {formatEpoch(item.completed_at_epoch || item.updated_at_epoch || item.modified_epoch)}</div>
                    <button
                      type="button"
                      onClick={() => openRunLog(item)}
                      disabled={!item.run_log_path}
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                    >
                      Open run.log
                    </button>
                  </div>
                ) : (
                  <div className="text-slate-500">No mirrored session yet.</div>
                )}
              </div>
            );
          })}

          <div className="rounded border border-slate-800 bg-slate-950/50 p-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="font-semibold text-slate-200">Latest Artifact Run</span>
              <Link href="/storage?tab=artifacts" className="text-cyan-300 underline">
                Open
              </Link>
            </div>
            {overview?.latest_artifact ? (
              <div className="space-y-1">
                <div className="text-slate-200">{overview.latest_artifact.title}</div>
                <div className="font-mono text-[11px] text-slate-400">{overview.latest_artifact.path}</div>
                <button
                  type="button"
                  onClick={openArtifactManifest}
                  disabled={!overview.latest_artifact.manifest_path}
                  className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                >
                  Open manifest
                </button>
              </div>
            ) : (
              <div className="text-slate-500">No mirrored artifact yet.</div>
            )}
          </div>

          <Link
            href="/storage"
            className="inline-flex rounded border border-emerald-700/60 bg-emerald-600/15 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-100 hover:bg-emerald-600/25"
          >
            Open Storage Workspace
          </Link>
        </div>
      )}
    </div>
  );
}
