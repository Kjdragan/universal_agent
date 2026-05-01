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

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Params = { path: string[] };

async function handler(
  req: NextRequest,
  ctx: { params: Promise<Params> },
): Promise<Response> {
  const { path } = await ctx.params;
  const subpath = ["link", ...(path || [])].join("/");
  return proxyApiRequest(req, subpath);
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
export const OPTIONS = handler;
