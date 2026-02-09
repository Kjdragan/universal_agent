import { NextResponse } from "next/server";
import { DASHBOARD_AUTH_COOKIE } from "@/lib/dashboardAuth";

export async function POST() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set({
    name: DASHBOARD_AUTH_COOKIE,
    value: "",
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
  return response;
}
