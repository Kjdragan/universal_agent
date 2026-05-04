"use client";

/**
 * useMissionControlRefresh — async-job lifecycle for the
 * "Refresh Mission Control" button.
 *
 * Phase 1 of this refactor moved the LLM-backed refresh off the request
 * path: the backend now returns 202 + job_id immediately and the
 * dashboard polls until terminal. This hook owns that POST→poll
 * lifecycle and exposes a small surface to the panel (phase, label,
 * error, start/reset). On terminal "completed" it invokes `onComplete`
 * so the page shell can bump its `RefreshContext` key, which causes
 * every panel (tiles, cards, tasks, readout) to re-fetch.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = "/api/dashboard/gateway";
const DEFAULT_POLL_INTERVAL_MS = 2000;

export type RefreshPhase =
  | "idle"
  | "queued"
  | "cards_running"
  | "readout_running"
  | "completed"
  | "failed";

export type RefreshError = {
  phase: string | null;
  message: string;
};

export type UseMissionControlRefreshResult = {
  phase: RefreshPhase;
  progressLabel: string;
  isActive: boolean;
  error: RefreshError | null;
  jobId: string | null;
  start: () => Promise<void>;
  reset: () => void;
};

export type UseMissionControlRefreshOptions = {
  /** Bumped exactly once per successful job so panels reload from the
   *  new persisted readout/cards. */
  onComplete?: () => void;
  /** Override the polling cadence. Production uses 2000ms; tests pass
   *  a tiny value so a multi-step job script completes within the
   *  default vitest test timeout. */
  pollIntervalMs?: number;
};

export function progressLabelForPhase(phase: RefreshPhase): string {
  switch (phase) {
    case "queued":
      return "Queued…";
    case "cards_running":
      return "Refreshing cards…";
    case "readout_running":
      return "Synthesizing brief…";
    case "completed":
      return "Refresh Mission Control";
    case "failed":
      return "Refresh failed — Try again";
    case "idle":
    default:
      return "Refresh Mission Control";
  }
}

function isTerminalPhase(phase: RefreshPhase): boolean {
  return phase === "completed" || phase === "failed";
}

function isActivePhase(phase: RefreshPhase): boolean {
  return phase === "queued" || phase === "cards_running" || phase === "readout_running";
}

export function useMissionControlRefresh(
  options: UseMissionControlRefreshOptions = {},
): UseMissionControlRefreshResult {
  const [phase, setPhase] = useState<RefreshPhase>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<RefreshError | null>(null);

  // Refs so the polling loop can read latest values without
  // re-creating timers on every render.
  const onCompleteRef = useRef(options.onComplete);
  onCompleteRef.current = options.onComplete;
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelledRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    stopPolling();
    setPhase("idle");
    setJobId(null);
    setError(null);
  }, [stopPolling]);

  const pollOnce = useCallback(async (currentJobId: string) => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/mission-control/refresh/${encodeURIComponent(currentJobId)}`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        // 404 means the job was evicted (very long idle window) — treat
        // as a terminal failure rather than retrying forever.
        if (res.status === 404) {
          stopPolling();
          setPhase("failed");
          setError({ phase: null, message: "Refresh job expired before it could be polled." });
        }
        return;
      }
      const body = (await res.json()) as {
        status: RefreshPhase;
        failed_phase?: string | null;
        error?: string | null;
      };
      if (cancelledRef.current) return;
      const nextPhase = body.status;
      setPhase(nextPhase);
      if (nextPhase === "failed") {
        stopPolling();
        setError({
          phase: body.failed_phase ?? null,
          message: body.error || "Refresh failed.",
        });
        return;
      }
      if (nextPhase === "completed") {
        stopPolling();
        setError(null);
        try {
          onCompleteRef.current?.();
        } catch {
          // Callback errors must not break the panel.
        }
        return;
      }
    } catch (err) {
      // Transient network errors during polling shouldn't tear down the
      // job; the next interval tick will retry. We only surface them as
      // a final state if we hit a terminal failure response.
      // eslint-disable-next-line no-console
      console.warn("mission-control refresh poll failed", err);
    }
  }, [stopPolling]);

  const start = useCallback(async () => {
    // Guard re-entry while a job is already in flight.
    if (isActivePhase(phase)) return;
    setError(null);
    setPhase("queued");
    cancelledRef.current = false;

    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/mission-control/refresh`,
        { method: "POST", cache: "no-store" },
      );
      if (!res.ok) {
        // 409 means the backend has another in-flight job; surface the
        // same retry banner so the operator knows what's happening.
        const detail = await res.text().catch(() => "");
        throw new Error(`POST refresh failed: ${res.status} ${detail.slice(0, 120)}`);
      }
      const body = (await res.json()) as { job_id: string; status: RefreshPhase };
      if (cancelledRef.current) return;
      setJobId(body.job_id);
      setPhase(body.status || "queued");

      stopPolling();
      const interval = Math.max(10, options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
      intervalRef.current = setInterval(() => {
        void pollOnce(body.job_id);
      }, interval);
      // Fire one immediate poll so the pill flips to cards_running fast
      // (most refreshes start tier-1 within milliseconds of the POST).
      void pollOnce(body.job_id);
    } catch (err) {
      setPhase("failed");
      setError({
        phase: null,
        message: err instanceof Error ? err.message : "Refresh failed to start.",
      });
    }
  }, [phase, pollOnce, stopPolling]);

  // Stop polling on unmount.
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      stopPolling();
    };
  }, [stopPolling]);

  return {
    phase,
    progressLabel: progressLabelForPhase(phase),
    isActive: isActivePhase(phase),
    error,
    jobId,
    start,
    reset,
  };
}
