import { NextRequest, NextResponse } from "next/server";

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

function threadsWebhookUpstreamBase(): string {
  const raw =
    (process.env.UA_THREADS_WEBHOOK_UPSTREAM_URL || "").trim()
    || (process.env.CSI_INGESTER_BASE_URL || "").trim()
    || "http://127.0.0.1:8091";
  return raw.replace(/\/$/, "");
}

function buildForwardHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower)) return;
    if (lower.startsWith("x-forwarded-")) return;
    headers.set(key, value);
  });
  return headers;
}

async function forwardToIngester(request: NextRequest, method: "GET" | "POST"): Promise<NextResponse> {
  const upstreamUrl = new URL(`${threadsWebhookUpstreamBase()}/webhooks/threads`);
  request.nextUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  const headers = buildForwardHeaders(request);
  const body = method === "GET" ? undefined : await request.arrayBuffer();

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(upstreamUrl, {
      method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (error) {
    return NextResponse.json(
      {
        detail: "Threads webhook upstream unavailable.",
        upstream: upstreamUrl.toString(),
        error: error instanceof Error ? error.message : String(error),
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

export async function GET(request: NextRequest): Promise<NextResponse> {
  return forwardToIngester(request, "GET");
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  return forwardToIngester(request, "POST");
}
