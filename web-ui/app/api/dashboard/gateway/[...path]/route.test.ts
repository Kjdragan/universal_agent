import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "./route";

vi.mock("@/lib/dashboardAuth", () => ({
  getDashboardSessionFromCookies: vi.fn(async () => ({
    authenticated: true,
    authRequired: false,
    ownerId: "owner_primary",
  })),
}));

describe("dashboard gateway proxy route", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    vi.unstubAllGlobals();
    process.env = { ...originalEnv };
    process.env.UA_DEV_MODE_STUBS = "0";
    process.env.UA_DASHBOARD_GATEWAY_URL = "http://gateway-a.test";
    process.env.NEXT_PUBLIC_GATEWAY_URL = "";
    process.env.UA_GATEWAY_URL = "";
    process.env.UA_DASHBOARD_GATEWAY_PROXY_TOTAL_TIMEOUT_MS = "35";
    process.env.UA_DASHBOARD_GATEWAY_PROXY_ATTEMPT_TIMEOUT_MS = "10";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    process.env = originalEnv;
  });

  it("returns a clear 502 within the proxy timeout budget when upstream hangs", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal | undefined;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener(
          "abort",
          () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          { once: true },
        );
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const startedAt = Date.now();
    const response = await GET(
      new NextRequest("https://app.test/api/dashboard/gateway/api/v1/dashboard/events"),
      { params: Promise.resolve({ path: ["api", "v1", "dashboard", "events"] }) },
    );
    const elapsedMs = Date.now() - startedAt;
    const payload = await response.json();

    expect(response.status).toBe(502);
    expect(payload.detail).toBe("Gateway upstream unavailable.");
    expect(payload.error).toContain("Gateway timeout connecting to backend");
    expect(elapsedMs).toBeLessThan(250);
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(2);
  });

  it("returns dev-mode stubs immediately when enabled", async () => {
    process.env.UA_DEV_MODE_STUBS = "1";
    const fetchMock = vi.fn(async () => (
      new Response(
        JSON.stringify({
          sessions: [{ session_id: "real-session", status: "active" }],
          total: 1,
          limit: 25,
          offset: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      )
    ));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(
      new NextRequest("https://app.test/api/dashboard/gateway/api/v1/ops/sessions"),
      { params: Promise.resolve({ path: ["api", "v1", "ops", "sessions"] }) },
    );
    const payload = await response.json();

    expect(response.status).toBe(200);
    expect(payload.sessions[0].session_id).toBe("stub-session-1");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
