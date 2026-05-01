import { NextRequest } from "next/server";

import { proxyApiRequest } from "@/lib/apiProxy";

// Next.js proxy for /api/viewer/* — forwards to the FastAPI backend at
// UA_DASHBOARD_API_URL (default http://127.0.0.1:8001) where the viewer
// router from src/universal_agent/api/viewer_routes.py is mounted.
//
// This is the missing piece that caused production 404s on the Task Hub
// "Workspace" button: openViewer() in lib/viewer/openViewer.ts calls
// /api/viewer/resolve, but Next.js had no matching route → 404 → frontend
// alerted "Could not resolve a viewer target for this item." The 404 was
// from Next.js, not from the backend resolver. The resolver was never even
// invoked. This proxy fixes that by forwarding to port 8001.
//
// Export pattern matches /api/dashboard/gateway/[...path]/route.ts
// (named-function `export async function`) which is the verified-working
// shape for Next.js 16 / Turbopack route discovery. The `export const GET =
// handler` alias style does not get picked up reliably.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const subpath = ["viewer", ...(path || [])].join("/");
  return proxyApiRequest(request, subpath);
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  return forward(request, context);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  return forward(request, context);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  return forward(request, context);
}

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  return forward(request, context);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  return forward(request, context);
}

export async function OPTIONS(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  return forward(request, context);
}
