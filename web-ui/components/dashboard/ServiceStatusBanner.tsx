"use client";

import { useEffect, useRef, useState } from "react";

import { getActiveMutationCount, getGatewayStatus, type GatewayStatus } from "@/lib/api";

/**
 * Sticky top-of-dashboard banner that surfaces gateway availability.
 *
 * Four states:
 *
 *   1. Healthy & stable: nothing rendered (no UI noise).
 *
 *   2. Recently restarted (`process_started_at` within 60s): yellow
 *      "service updating" banner. Backgrounds the deploy warm-up window so
 *      the operator understands why the dashboard panels are blank instead
 *      of staring at frozen "Refreshing..." spinners.
 *
 *   3. First failed probe (debounce window): yellow "reconnecting" banner.
 *      The gateway hard-restarts on EVERY merge-to-main deploy (~19/day), a
 *      ~15s dead window each time. A single failed probe used to flip the
 *      banner straight to red ~19 times/day for what are planned, ~15s
 *      restarts — not outages. We now require FAILURE_THRESHOLD consecutive
 *      failed probes before declaring the gateway unreachable; the first
 *      failure shows this softer "reconnecting" state instead, and the faster
 *      degraded cadence usually catches the recovered gateway (which then
 *      surfaces as the state-2 "just updated" banner) before red ever shows.
 *
 *   4. Unreachable (>= FAILURE_THRESHOLD consecutive timeouts/HTTP errors on
 *      `/api/v1/version`): red "gateway unreachable" banner with a manual
 *      retry button. A sustained failure streak — a real outage, not a deploy.
 *
 * Poll cadence: 30s when healthy, 5s while in a degraded/updating state. The
 * faster cadence lets the banner clear quickly once the gateway recovers.
 */

const HEALTHY_POLL_MS = 30_000;
const DEGRADED_POLL_MS = 5_000;
/** While a mutation is in flight we defer the poll instead of skipping it
 *  outright — short enough to resume promptly after the burst clears. */
const MUTATION_DEFER_MS = 1_000;
/** Consecutive failed probes required before the red "unreachable" banner
 *  shows. A single failure is treated as a transient (likely a deploy
 *  restart) and rendered as the softer yellow "reconnecting" state. */
const FAILURE_THRESHOLD = 2;

export function ServiceStatusBanner() {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [failureStreak, setFailureStreak] = useState(0);
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
      // Skip the version probe while mutations are in flight. A burst of
      // parallel deletes saturates the connection pool and starves the probe
      // past its 4s timeout, surfacing a false-positive "unreachable" banner.
      if (getActiveMutationCount() > 0) {
        if (cancelled) return;
        timerRef.current = setTimeout(poll, MUTATION_DEFER_MS);
        return;
      }
      const next = await getGatewayStatus();
      if (cancelled) return;
      // Belt-and-suspenders: if a mutation began while our request was in
      // flight and the response came back as an abort/timeout, treat it as a
      // deferred poll rather than a real outage.
      if (!next.reachable && getActiveMutationCount() > 0) {
        timerRef.current = setTimeout(poll, MUTATION_DEFER_MS);
        return;
      }
      // Track the consecutive-failure streak so a lone failed probe (almost
      // always a ~15s deploy restart) doesn't flip the banner straight to red.
      setFailureStreak((prev) => (next.ok ? 0 : prev + 1));
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
      setFailureStreak((prev) => (next.ok ? 0 : prev + 1));
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
    // Debounce: a single failed probe is treated as a transient (deploy
    // restart) and shown as a softer "reconnecting" state. Only a sustained
    // streak escalates to the red "unreachable" banner.
    if (failureStreak < FAILURE_THRESHOLD) {
      return (
        <div className="flex shrink-0 items-center justify-center gap-2 bg-amber-400 px-4 py-1.5 text-xs font-semibold text-amber-950">
          <span>🟡</span>
          <span>Reconnecting to gateway… — dashboard refreshing</span>
        </div>
      );
    }
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
