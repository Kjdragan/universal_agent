import { NextResponse } from "next/server";
import { getDashboardSessionFromCookies } from "@/lib/dashboardAuth";

export async function GET() {
  const session = await getDashboardSessionFromCookies();
  if (!session.authenticated && session.authRequired) {
    return NextResponse.json(
      {
        authenticated: false,
        auth_required: session.authRequired,
        owner_id: session.ownerId,
      },
      { status: 401 },
    );
  }

  return NextResponse.json({
    authenticated: session.authenticated,
    auth_required: session.authRequired,
    owner_id: session.ownerId,
    expires_at: session.expiresAt,
  });
}
