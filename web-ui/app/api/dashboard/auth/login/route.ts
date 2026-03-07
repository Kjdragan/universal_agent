import { NextRequest, NextResponse } from "next/server";
import {
  DASHBOARD_AUTH_COOKIE,
  createDashboardSessionToken,
  dashboardAuthRequired,
  dashboardSessionSecretConfigured,
  normalizeOwnerId,
  validateDashboardPassword,
} from "@/lib/dashboardAuth";

type LoginPayload = {
  password?: string;
  owner_id?: string;
};

export async function POST(request: NextRequest) {
  let payload: LoginPayload = {};
  try {
    payload = (await request.json()) as LoginPayload;
  } catch {
    payload = {};
  }

  const authRequired = dashboardAuthRequired();
  const password = String(payload.password || "");
  const ownerId = normalizeOwnerId(payload.owner_id);
  if (authRequired && !dashboardSessionSecretConfigured()) {
    return NextResponse.json(
      { detail: "Dashboard session signing secret is not configured." },
      { status: 503 },
    );
  }
  if (authRequired && !validateDashboardPassword(password, ownerId)) {
    return NextResponse.json({ detail: "Invalid credentials." }, { status: 401 });
  }

  const { token, expiresAt } = createDashboardSessionToken(ownerId);

  const response = NextResponse.json({
    ok: true,
    auth_required: authRequired,
    owner_id: ownerId,
    expires_at: expiresAt,
  });

  response.cookies.set({
    name: DASHBOARD_AUTH_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: Math.max(60, expiresAt - Math.floor(Date.now() / 1000)),
  });

  return response;
}
