/**
 * Shared HTTP utilities for talking to the UA Gateway through the dashboard proxy.
 *
 * Two helpers are exported:
 *
 *   - `fetchWithTimeout(url, init, timeoutMs)` — drop-in `fetch` replacement that
 *     aborts after `timeoutMs` (default 5000). Existing components can adopt this
 *     incrementally; nothing in this file changes how `fetch` works for legacy code.
 *
 *   - `getGatewayStatus()` — polls `/api/v1/version` (via the dashboard gateway
 *     proxy) and returns a `GatewayStatus` shape that `ServiceStatusBanner` uses
 *     to decide whether to show the "updating" banner.
 *
 * Background: the Universal Agent gateway restarts on every merge-to-main
 * deploy. During the ~30–45s warm-up window, dashboard API calls that fired
 * during the restart sit with "Refreshing..." spinners indefinitely. This
 * utility caps that wait at 5s and surfaces the state to the banner so the
 * operator sees "service updating" instead of a frozen UI.
 */

export const DEFAULT_API_TIMEOUT_MS = 5000;
export const GATEWAY_VERSION_PATH = "/api/dashboard/gateway/api/v1/version";

/** Recent-restart window — banner stays visible while the gateway is this fresh. */
export const RECENT_RESTART_WINDOW_MS = 60_000;

/**
 * Tracks in-flight mutations (bulk deletes, dispatches, etc.) so the gateway
 * status poll can skip itself while the browser is busy. Without this, a burst
 * of parallel mutations saturates the HTTP/1.1 connection pool (6/origin) and
 * starves the version poll past its 4s AbortController deadline, producing a
 * "Gateway unreachable" banner even though the backend is healthy.
 */
let activeMutationCount = 0;

export function beginMutation(): () => void {
  activeMutationCount += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    activeMutationCount = Math.max(0, activeMutationCount - 1);
  };
}

export function getActiveMutationCount(): number {
  return activeMutationCount;
}

export class ApiTimeoutError extends Error {
  constructor(public url: string, public timeoutMs: number) {
    super(`Request to ${url} timed out after ${timeoutMs}ms`);
    this.name = "ApiTimeoutError";
  }
}

/**
 * `fetch` wrapper with a hard timeout via AbortController.
 *
 * The caller can still pass their own `signal` — both signals will be honored
 * (whichever fires first wins). Errors are unchanged from `fetch`'s native
 * behavior, with one exception: an abort caused by `timeoutMs` elapsing is
 * re-raised as `ApiTimeoutError` so callers can distinguish "user cancelled"
 * from "request took too long".
 */
export async function fetchWithTimeout(
  url: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  // If caller supplied a signal, abort our controller when theirs fires.
  const callerSignal = init.signal;
  const onCallerAbort = () => controller.abort();
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort();
    else callerSignal.addEventListener("abort", onCallerAbort, { once: true });
  }

  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (controller.signal.aborted && !(callerSignal?.aborted)) {
      // Our timer fired (not the caller's signal) — surface as a typed error.
      throw new ApiTimeoutError(url, timeoutMs);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
    if (callerSignal) callerSignal.removeEventListener("abort", onCallerAbort);
  }
}

export interface GatewayStatus {
  ok: boolean;
  reachable: boolean;
  commitSha?: string;
  shortSha?: string;
  branch?: string;
  processStartedAt?: string;
  processAgeMs?: number;
  recentlyRestarted: boolean;
  error?: string;
}

/**
 * Hit `/api/v1/version` through the dashboard gateway proxy.
 *
 * Returns a `GatewayStatus` describing whether the gateway is reachable and
 * how recently it (re)started. Never throws — failures are encoded into the
 * return value so the banner component can render a degraded state cleanly.
 *
 * Uses a tight 4s timeout so the banner's poll cycle doesn't itself stall the
 * UI if the gateway is wedged.
 */
export async function getGatewayStatus(
  timeoutMs: number = 4000,
): Promise<GatewayStatus> {
  try {
    const resp = await fetchWithTimeout(
      GATEWAY_VERSION_PATH,
      { cache: "no-store" },
      timeoutMs,
    );
    if (!resp.ok) {
      return {
        ok: false,
        reachable: true,
        recentlyRestarted: false,
        error: `gateway returned HTTP ${resp.status}`,
      };
    }
    const data = (await resp.json()) as {
      commit_sha?: string;
      short_sha?: string;
      branch?: string;
      process_started_at?: string;
    };
    const startedAt = data.process_started_at
      ? new Date(data.process_started_at).getTime()
      : NaN;
    const processAgeMs = Number.isFinite(startedAt) ? Date.now() - startedAt : undefined;
    const recentlyRestarted =
      processAgeMs !== undefined && processAgeMs >= 0 && processAgeMs < RECENT_RESTART_WINDOW_MS;
    return {
      ok: true,
      reachable: true,
      commitSha: data.commit_sha,
      shortSha: data.short_sha,
      branch: data.branch,
      processStartedAt: data.process_started_at,
      processAgeMs,
      recentlyRestarted,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      reachable: false,
      recentlyRestarted: false,
      error: message,
    };
  }
}
