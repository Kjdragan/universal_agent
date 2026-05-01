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

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Params = { path: string[] };

async function handler(
  req: NextRequest,
  ctx: { params: Promise<Params> },
): Promise<Response> {
  const { path } = await ctx.params;
  const subpath = ["viewer", ...(path || [])].join("/");
  return proxyApiRequest(req, subpath);
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
export const OPTIONS = handler;
