import { NextRequest } from "next/server";

import { proxyApiRequest } from "@/lib/apiProxy";

// Next.js proxy for /api/link/* — forwards to the FastAPI backend at
// UA_DASHBOARD_API_URL (default http://127.0.0.1:8001) where the Stripe
// Link router from src/universal_agent/api/link_routes.py is mounted.
//
// Same rationale as /api/viewer/[...path]: Next.js has no matching route,
// so without this proxy every Link API call returns 404. Inert until the
// master switch UA_ENABLE_LINK is set, but kept symmetric with the viewer
// proxy so both routers are reachable when needed.
//
// Export pattern matches /api/dashboard/gateway/[...path]/route.ts
// (named-function `export async function`).

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const subpath = ["link", ...(path || [])].join("/");
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
