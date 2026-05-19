/**
 * Tests for the shared fetch utilities.
 *
 * Covers:
 *   - fetchWithTimeout: succeeds on fast responses, throws ApiTimeoutError on slow,
 *     respects caller-supplied AbortSignals.
 *   - getGatewayStatus: parses the version envelope, computes recentlyRestarted
 *     correctly, and degrades cleanly on network failure.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import {
  ApiTimeoutError,
  DEFAULT_API_TIMEOUT_MS,
  GATEWAY_VERSION_PATH,
  RECENT_RESTART_WINDOW_MS,
  fetchWithTimeout,
  getGatewayStatus,
} from "./api";

describe("fetchWithTimeout", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("returns the response when the fetch resolves before the timeout", async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));

    const resp = await fetchWithTimeout("/test", {}, 1000);

    expect(resp.status).toBe(200);
    expect(global.fetch).toHaveBeenCalledOnce();
  });

  it("throws ApiTimeoutError if the fetch exceeds the timeout", async () => {
    // Mock fetch with a fetch that respects the AbortController signal.
    global.fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });

    await expect(fetchWithTimeout("/slow", {}, 50)).rejects.toBeInstanceOf(ApiTimeoutError);
  });

  it("propagates non-timeout fetch errors unchanged", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("network down"));

    await expect(fetchWithTimeout("/down", {}, 1000)).rejects.toThrow("network down");
  });

  it("aborts when the caller-supplied signal fires (not as ApiTimeoutError)", async () => {
    global.fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });

    const controller = new AbortController();
    const p = fetchWithTimeout("/cancellable", { signal: controller.signal }, 5000);
    controller.abort();

    await expect(p).rejects.not.toBeInstanceOf(ApiTimeoutError);
  });

  it("uses DEFAULT_API_TIMEOUT_MS when no timeout is supplied", () => {
    expect(DEFAULT_API_TIMEOUT_MS).toBe(5000);
  });
});

describe("getGatewayStatus", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("marks gateway as recentlyRestarted when process_started_at is within the window", async () => {
    const now = new Date("2026-05-19T03:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(now);

    global.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          commit_sha: "abc123",
          short_sha: "abc123",
          branch: "main",
          process_started_at: new Date(now - 10_000).toISOString(),
        }),
        { status: 200 },
      ),
    );

    const status = await getGatewayStatus();

    expect(status.ok).toBe(true);
    expect(status.reachable).toBe(true);
    expect(status.recentlyRestarted).toBe(true);
    expect(status.processAgeMs).toBe(10_000);
    expect(status.shortSha).toBe("abc123");
    const url = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(url).toBe(GATEWAY_VERSION_PATH);
  });

  it("does not mark recentlyRestarted when the process is old", async () => {
    const now = new Date("2026-05-19T03:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(now);

    global.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          process_started_at: new Date(now - (RECENT_RESTART_WINDOW_MS + 5_000)).toISOString(),
        }),
        { status: 200 },
      ),
    );

    const status = await getGatewayStatus();

    expect(status.ok).toBe(true);
    expect(status.recentlyRestarted).toBe(false);
  });

  it("returns an unreachable status when the fetch fails", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("network down"));

    const status = await getGatewayStatus();

    expect(status.ok).toBe(false);
    expect(status.reachable).toBe(false);
    expect(status.recentlyRestarted).toBe(false);
    expect(status.error).toContain("network down");
  });

  it("returns ok=false reachable=true when the gateway returns a non-200", async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response("oops", { status: 503 }));

    const status = await getGatewayStatus();

    expect(status.ok).toBe(false);
    expect(status.reachable).toBe(true);
    expect(status.error).toContain("503");
  });
});
