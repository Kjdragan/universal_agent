import { NextRequest, NextResponse } from "next/server";
import { getDashboardSessionFromCookies } from "@/lib/dashboardAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

function gatewayBaseCandidates(): string[] {
  const configured = [
    (process.env.UA_DASHBOARD_GATEWAY_URL || "").trim(),
    (process.env.NEXT_PUBLIC_GATEWAY_URL || "").trim(),
    (process.env.UA_GATEWAY_URL || "").trim(),
  ].filter(Boolean);
  const fallback = ["http://127.0.0.1:8002", "http://localhost:8002"];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of [...configured, ...fallback]) {
    const normalized = raw.replace(/\/$/, "");
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

function isLocalGateway(url: URL): boolean {
  const host = (url.hostname || "").toLowerCase();
  return host === "127.0.0.1" || host === "localhost";
}

function gatewayOpsToken(): string {
  return (
    (process.env.UA_DASHBOARD_OPS_TOKEN || "").trim()
    || (process.env.UA_OPS_TOKEN || "").trim()
    || (process.env.NEXT_PUBLIC_UA_OPS_TOKEN || "").trim()
  );
}

/**
 * Check if dev-mode stub data should be used when upstream is unavailable.
 * Controlled by UA_DEV_MODE_STUBS env var (default: true in development).
 */
function isDevModeStubsEnabled(): boolean {
  const raw = (process.env.UA_DEV_MODE_STUBS || "").trim().toLowerCase();
  // Explicit opt-out
  if (["0", "false", "no", "off"].includes(raw)) return false;
  // Explicit opt-in
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  // Default: enabled in development, disabled in production
  return process.env.NODE_ENV !== "production";
}

/**
 * Get stub data for a given API path when upstream is unavailable.
 * Used for development and testing when the backend gateway is not running.
 */
function getStubDataForPath(pathname: string): unknown | null {
  // CSI recent signals stub
  if (pathname === "/api/v1/csi/recent") {
    return {
      status: "ok",
      signals: [
        {
          id: "csi-001",
          title: "n8n automation demand surging on Freelancer.com",
          source: "opportunity_bundle",
          relevance_score: 92,
          mission_alignment: "freelance_monetization",
          created_at: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          id: "csi-002",
          title: "AI agent development trending on X",
          source: "x_trends",
          relevance_score: 85,
          mission_alignment: "universal_agent",
          created_at: new Date(Date.now() - 7200000).toISOString(),
        },
        {
          id: "csi-003",
          title: "RAG implementation jobs increasing on Upwork",
          source: "csi_report",
          relevance_score: 78,
          mission_alignment: "freelance_monetization",
          created_at: new Date(Date.now() - 14400000).toISOString(),
        },
        {
          id: "csi-004",
          title: "r/MachineLearning discussing agent orchestration",
          source: "reddit",
          relevance_score: 65,
          mission_alignment: "universal_agent",
          created_at: new Date(Date.now() - 28800000).toISOString(),
        },
      ],
      total: 4,
    };
  }

  // System Resources stub (used by System Resources panel in Mission Control)
  if (pathname === "/api/v1/dashboard/system-resources") {
    return {
      version: 1,
      overall_status: "warn",
      generated_at_utc: new Date().toISOString(),
      summary: "Dev mode stub data. Connect to live gateway for real metrics.",
      metrics: {
        cpu_load_1m: 0.45,
        cpu_load_5m: 0.52,
        cpu_load_15m: 0.61,
        cpu_cores: 8,
        load_per_core: 0.06,
        ram_used_gb: 18.5,
        ram_total_gb: 31.0,
        ram_percent: 60,
        swap_used_gb: 2.1,
        swap_total_gb: 14.0,
        swap_percent: 15,
        disk_used_gb: 23,
        disk_total_gb: 183,
        disk_percent: 14,
        active_agent_sessions: 12,
        gateway_errors_30m: 0,
        dispatch_concurrency: 2,
      },
      findings: [],
    };
  }
  return null;
}

function buildUpstreamHeaders(request: NextRequest, ownerId: string): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower)) return;
    if (lower.startsWith("x-forwarded-")) return;
    headers.set(key, value);
  });

  headers.set("x-ua-dashboard-owner", ownerId);
  const opsToken = gatewayOpsToken();
  if (opsToken) {
    headers.set("x-ua-ops-token", opsToken);
    headers.set("authorization", `Bearer ${opsToken}`);
  }
  return headers;
}

function maybeApplyOwnerFilter(pathname: string, params: URLSearchParams, ownerId: string): void {
  const enforceRaw = (process.env.UA_DASHBOARD_ENFORCE_OWNER_FILTER || "").trim().toLowerCase();
  const enforceOwnerFilter = ["1", "true", "yes", "on"].includes(enforceRaw);
  if (!enforceOwnerFilter) return;
  if (params.has("owner")) return;
  if (!ownerId) return;

  if (pathname === "/api/v1/ops/sessions" || pathname === "/api/v1/ops/calendar/events") {
    params.set("owner", ownerId);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithTransientRetry(
  upstreamUrl: URL,
  init: RequestInit,
  method: string,
): Promise<Response> {
  const upperMethod = method.toUpperCase();
  const retryable = upperMethod === "GET" || upperMethod === "HEAD";
  const maxAttempts = retryable ? 3 : 1;
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fetch(upstreamUrl, init);
    } catch (err) {
      lastError = err;
      if (attempt >= maxAttempts) break;
      // Transient gateway restarts can drop localhost:8002 briefly.
      await sleep(120 * attempt);
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

async function proxyRequest(request: NextRequest, path: string[]) {
  const session = await getDashboardSessionFromCookies();
  if (!session.authenticated && session.authRequired) {
    return NextResponse.json({ detail: "Dashboard login required." }, { status: 401 });
  }

  const safePath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const upstreamPathname = `/${safePath}`;
  const queryEntries = Array.from(request.nextUrl.searchParams.entries());

  const headers = buildUpstreamHeaders(request, session.ownerId);
  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  const candidates = gatewayBaseCandidates();
  let upstreamResponse: Response | null = null;
  let upstreamUrl: URL | null = null;
  let lastFetchError: unknown = null;
  for (const base of candidates) {
    const candidateUrl = new URL(`${base}${upstreamPathname}`);
    for (const [key, value] of queryEntries) {
      candidateUrl.searchParams.set(key, value);
    }
    maybeApplyOwnerFilter(upstreamPathname, candidateUrl.searchParams, session.ownerId);
    try {
      const response = await fetchWithTransientRetry(
        candidateUrl,
        {
          method,
          headers,
          body,
          cache: "no-store",
          redirect: "manual",
        },
        method,
      );
      const contentType = (response.headers.get("content-type") || "").toLowerCase();
      const shouldTryNext =
        !isLocalGateway(candidateUrl)
        && response.status >= 500
        && response.status <= 504
        && (
          contentType.includes("text/html")
          || response.status === 502
          || response.status === 503
          || response.status === 504
        );
      upstreamResponse = response;
      upstreamUrl = candidateUrl;
      if (shouldTryNext) {
        continue;
      }
      break;
    } catch (err) {
      lastFetchError = err;
      continue;
    }
  }

  // Dev-mode fallback: return stub data if upstream is unavailable (when enabled)
  if (!upstreamResponse || !upstreamUrl) {
    if (isDevModeStubsEnabled()) {
      const stubData = getStubDataForPath(upstreamPathname);
      if (stubData) {
        return NextResponse.json(stubData);
      }
    }
    return NextResponse.json(
      {
        detail: "Gateway upstream unavailable.",
        upstream: `${candidates[0] || "http://localhost:8002"}${upstreamPathname}`,
        error: lastFetchError instanceof Error ? lastFetchError.message : String(lastFetchError),
      },
      { status: 502 },
    );
  }

  const upstreamContentType = (upstreamResponse.headers.get("content-type") || "").toLowerCase();
  if (upstreamResponse.status >= 500 && upstreamContentType.includes("text/html")) {
    // Dev-mode fallback: return stub data if upstream returns 500+ HTML error (when enabled)
    if (isDevModeStubsEnabled()) {
      const stubData = getStubDataForPath(upstreamPathname);
      if (stubData) {
        return NextResponse.json(stubData);
      }
    }
    const htmlSnippet = (await upstreamResponse.text().catch(() => "")).replace(/\s+/g, " ").trim().slice(0, 180);
    return NextResponse.json(
      {
        detail: "Gateway upstream returned an invalid HTML error response.",
        upstream: upstreamUrl.toString(),
        status: upstreamResponse.status,
        error: htmlSnippet || "Upstream error",
      },
      { status: upstreamResponse.status },
    );
  }

  const responseHeaders = new Headers();
  upstreamResponse.headers.forEach((value, key) => {
    if (HOP_BY_HOP.has(key.toLowerCase())) return;
    responseHeaders.set(key, value);
  });
  responseHeaders.set("cache-control", "no-store");

  return new NextResponse(upstreamResponse.body, {
    status: upstreamResponse.status,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function PUT(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}
