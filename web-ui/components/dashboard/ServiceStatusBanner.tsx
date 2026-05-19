"use client";

import { useEffect, useRef, useState } from "react";

import { getGatewayStatus, type GatewayStatus } from "@/lib/api";

/**
 * Sticky top-of-dashboard banner that surfaces gateway availability.
 *
 * Three states:
 *
 *   1. Healthy & stable: nothing rendered (no UI noise).
 *
 *   2. Recently restarted (`process_started_at` within 60s): yellow
 *      "service updating" banner. Backgrounds the deploy warm-up window so
 *      the operator understands why the dashboard panels are blank instead
 *      of staring at frozen "Refreshing..." spinners.
 *
 *   3. Unreachable (timeout or HTTP error on `/api/v1/version`): red
 *      "gateway unreachable" banner with a manual retry button.
 *
 * Poll cadence: 30s when healthy, 5s while in a degraded/updating state. The
 * faster cadence lets the banner clear quickly once the gateway recovers.
 */

const HEALTHY_POLL_MS = 30_000;
const DEGRADED_POLL_MS = 5_000;

export function ServiceStatusBanner() {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    let cancelled = false;

    const scheduleNext = (s: GatewayStatus | null) => {
      const inDegraded = Boolean(s && (!s.ok || s.recentlyRestarted));
      const delay = inDegraded ? DEGRADED_POLL_MS : HEALTHY_POLL_MS;
      timerRef.current = setTimeout(poll, delay);
    };

    const poll = async () => {
      const next = await getGatewayStatus();
      if (cancelled) return;
      setStatus(next);
      scheduleNext(next);
    };

    poll();

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleRetry = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const next = await getGatewayStatus();
      setStatus(next);
    } finally {
      setRefreshing(false);
    }
  };

  if (!status) return null;

  if (status.recentlyRestarted) {
    const ageSec =
      status.processAgeMs !== undefined ? Math.max(0, Math.round(status.processAgeMs / 1000)) : "?";
    return (
      <div className="flex shrink-0 items-center justify-center gap-2 bg-amber-400 px-4 py-1.5 text-xs font-semibold text-amber-950">
        <span>🟡</span>
        <span>
          Gateway just updated ({String(ageSec)}s ago
          {status.shortSha ? ` · ${status.shortSha}` : ""}) — dashboard refreshing
        </span>
      </div>
    );
  }

  if (!status.ok) {
    return (
      <div className="flex shrink-0 items-center justify-center gap-3 bg-red-500 px-4 py-1.5 text-xs font-semibold text-red-50">
        <span>🔴</span>
        <span>
          Gateway unreachable ({status.error ?? "unknown error"}) — some panels will be empty
        </span>
        <button
          type="button"
          className="rounded border border-red-50/40 px-2 py-0.5 text-[11px] font-medium hover:bg-red-50/10 disabled:opacity-60"
          onClick={handleRetry}
          disabled={refreshing}
        >
          {refreshing ? "retrying…" : "retry"}
        </button>
      </div>
    );
  }

  return null;
}
