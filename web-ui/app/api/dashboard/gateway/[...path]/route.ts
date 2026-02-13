import { NextRequest, NextResponse } from "next/server";
import { getDashboardSessionFromCookies } from "@/lib/dashboardAuth";

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

function gatewayBaseUrl(): string {
  const raw =
    (process.env.UA_DASHBOARD_GATEWAY_URL || "").trim()
    || (process.env.NEXT_PUBLIC_GATEWAY_URL || "").trim()
    || (process.env.UA_GATEWAY_URL || "").trim()
    || "http://localhost:8002";
  return raw.replace(/\/$/, "");
}

function gatewayOpsToken(): string {
  return (
    (process.env.UA_DASHBOARD_OPS_TOKEN || "").trim()
    || (process.env.UA_OPS_TOKEN || "").trim()
    || (process.env.NEXT_PUBLIC_UA_OPS_TOKEN || "").trim()
  );
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

async function proxyRequest(request: NextRequest, path: string[]) {
  const session = await getDashboardSessionFromCookies();
  if (!session.authenticated && session.authRequired) {
    return NextResponse.json({ detail: "Dashboard login required." }, { status: 401 });
  }

  const safePath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const upstreamPathname = `/${safePath}`;
  const upstreamUrl = new URL(`${gatewayBaseUrl()}${upstreamPathname}`);
  request.nextUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });
  maybeApplyOwnerFilter(upstreamPathname, upstreamUrl.searchParams, session.ownerId);

  const headers = buildUpstreamHeaders(request, session.ownerId);
  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(upstreamUrl, {
      method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (err) {
    // Most common local-dev failure: web UI started before gateway is reachable.
    return NextResponse.json(
      {
        detail: "Gateway upstream unavailable.",
        upstream: upstreamUrl.toString(),
        error: err instanceof Error ? err.message : String(err),
      },
      { status: 502 },
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
