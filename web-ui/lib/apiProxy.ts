import { NextRequest, NextResponse } from "next/server";

// Shared proxy helper for forwarding requests from Next.js API routes to the
// FastAPI backend at UA_DASHBOARD_API_URL (default http://127.0.0.1:8001).
//
// Created to fix the production issue where /api/viewer/* and /api/link/*
// requests from the UI were being caught by Next.js with no matching route
// (returning 404) instead of reaching the real FastAPI backend that has the
// routes registered. The existing /api/dashboard/gateway/[...path] proxy
// only forwards to the Gateway service on port 8002 — the API service on
// port 8001 (where Track B/C viewer routes and Stripe Link routes live)
// had no proxy at all.

export const HOP_BY_HOP = new Set([
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

const DEFAULT_TOTAL_TIMEOUT_MS = 30000;
const DEFAULT_ATTEMPT_TIMEOUT_MS = 15000;

function boundedPositiveInt(
  value: string | undefined,
  fallback: number,
  max: number,
): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(Math.floor(parsed), max);
}

function apiBaseCandidates(): string[] {
  const configured = [
    (process.env.UA_DASHBOARD_API_URL || "").trim(),
    (process.env.NEXT_PUBLIC_UA_API_URL || "").trim(),
    (process.env.UA_API_URL || "").trim(),
  ].filter(Boolean);
  const fallback = ["http://127.0.0.1:8001", "http://localhost:8001"];
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

function totalTimeoutMs(): number {
  return boundedPositiveInt(
    process.env.UA_DASHBOARD_API_PROXY_TOTAL_TIMEOUT_MS,
    DEFAULT_TOTAL_TIMEOUT_MS,
    30_000,
  );
}

function attemptTimeoutMs(): number {
  return boundedPositiveInt(
    process.env.UA_DASHBOARD_API_PROXY_ATTEMPT_TIMEOUT_MS,
    DEFAULT_ATTEMPT_TIMEOUT_MS,
    totalTimeoutMs(),
  );
}

function buildHeaders(req: NextRequest): Record<string, string> {
  const headers: Record<string, string> = {};
  req.headers.forEach((value, key) => {
    if (HOP_BY_HOP.has(key.toLowerCase())) return;
    headers[key] = value;
  });
  return headers;
}

/**
 * Proxy a request from a Next.js route handler to the FastAPI backend.
 *
 * @param req      the incoming Next.js request
 * @param subpath  the path under /api on the backend (e.g. "viewer/resolve")
 */
export async function proxyApiRequest(
  req: NextRequest,
  subpath: string,
): Promise<NextResponse> {
  const safePath = subpath.replace(/^\/+/, "");
  const search = req.nextUrl.search || "";
  const candidates = apiBaseCandidates();

  const body = req.method === "GET" || req.method === "HEAD"
    ? undefined
    : await req.text();

  const totalDeadline = Date.now() + totalTimeoutMs();
  let lastError: unknown = null;

  for (const base of candidates) {
    if (Date.now() >= totalDeadline) break;
    const targetUrl = `${base}/api/${safePath}${search}`;
    const ac = new AbortController();
    const attemptTimer = setTimeout(
      () => ac.abort(),
      Math.min(attemptTimeoutMs(), totalDeadline - Date.now()),
    );
    try {
      const upstream = await fetch(targetUrl, {
        method: req.method,
        headers: buildHeaders(req),
        body,
        signal: ac.signal,
      });
      clearTimeout(attemptTimer);

      const respHeaders = new Headers();
      upstream.headers.forEach((value, key) => {
        if (HOP_BY_HOP.has(key.toLowerCase())) return;
        respHeaders.set(key, value);
      });

      const respBody = await upstream.arrayBuffer();
      return new NextResponse(respBody, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: respHeaders,
      });
    } catch (err) {
      clearTimeout(attemptTimer);
      lastError = err;
      // Try the next candidate
    }
  }

  return NextResponse.json(
    {
      detail: {
        code: "api_proxy_unreachable",
        message: "Could not reach the API backend.",
        tried: candidates,
        last_error: lastError instanceof Error ? lastError.message : String(lastError),
      },
    },
    { status: 502 },
  );
}
