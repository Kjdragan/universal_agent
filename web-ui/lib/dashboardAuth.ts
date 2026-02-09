import crypto from "crypto";
import { cookies } from "next/headers";

export const DASHBOARD_AUTH_COOKIE = "ua_dashboard_auth";

const DEFAULT_OWNER = "owner_primary";
const OWNER_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;

export type DashboardSession = {
  authenticated: boolean;
  authRequired: boolean;
  ownerId: string;
  expiresAt: number | null;
};

function envFlag(name: string): boolean | null {
  const raw = (process.env[name] || "").trim().toLowerCase();
  if (!raw) return null;
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  if (["0", "false", "no", "off"].includes(raw)) return false;
  return null;
}

export function dashboardAuthRequired(): boolean {
  const explicit = envFlag("UA_DASHBOARD_AUTH_ENABLED");
  if (explicit !== null) return explicit;
  return Boolean((process.env.UA_DASHBOARD_PASSWORD || "").trim());
}

function dashboardPassword(): string {
  return (process.env.UA_DASHBOARD_PASSWORD || "").trim();
}

function dashboardOwnerDefault(): string {
  return normalizeOwnerId(process.env.UA_DASHBOARD_OWNER_ID || DEFAULT_OWNER);
}

function dashboardSessionTtlSeconds(): number {
  const raw = Number((process.env.UA_DASHBOARD_SESSION_TTL_SECONDS || "86400").trim());
  if (!Number.isFinite(raw) || raw <= 60) return 86400;
  return Math.floor(raw);
}

function dashboardSessionSecret(): string {
  const secret =
    (process.env.UA_DASHBOARD_SESSION_SECRET || "").trim()
    || (process.env.UA_OPS_TOKEN || "").trim()
    || dashboardPassword();
  if (secret) return secret;
  return "ua-dashboard-dev-secret";
}

function toBase64Url(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

function fromBase64Url(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

function signPayload(payloadB64: string): string {
  return crypto.createHmac("sha256", dashboardSessionSecret()).update(payloadB64).digest("base64url");
}

function safeEqual(a: string, b: string): boolean {
  const aBuf = Buffer.from(a);
  const bBuf = Buffer.from(b);
  if (aBuf.length !== bBuf.length) return false;
  return crypto.timingSafeEqual(aBuf, bBuf);
}

export function normalizeOwnerId(ownerId: string | undefined | null): string {
  const normalized = (ownerId || "").trim();
  if (!normalized) return dashboardOwnerDefault();
  if (!OWNER_PATTERN.test(normalized)) return dashboardOwnerDefault();
  return normalized;
}

export function validateDashboardPassword(candidate: string): boolean {
  const required = dashboardPassword();
  if (!required) return true;
  return safeEqual(candidate, required);
}

export function createDashboardSessionToken(ownerId: string): { token: string; expiresAt: number } {
  const expiresAt = Math.floor(Date.now() / 1000) + dashboardSessionTtlSeconds();
  const payload = JSON.stringify({
    owner_id: normalizeOwnerId(ownerId),
    exp: expiresAt,
  });
  const payloadB64 = toBase64Url(payload);
  const sig = signPayload(payloadB64);
  return { token: `${payloadB64}.${sig}`, expiresAt };
}

export function decodeDashboardSessionToken(token: string): DashboardSession {
  const authRequired = dashboardAuthRequired();
  if (!authRequired) {
    return {
      authenticated: true,
      authRequired,
      ownerId: dashboardOwnerDefault(),
      expiresAt: null,
    };
  }

  const raw = (token || "").trim();
  if (!raw.includes(".")) {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null };
  }

  const [payloadB64, sig] = raw.split(".", 2);
  if (!payloadB64 || !sig) {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null };
  }

  const expectedSig = signPayload(payloadB64);
  if (!safeEqual(sig, expectedSig)) {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null };
  }

  try {
    const payload = JSON.parse(fromBase64Url(payloadB64)) as { owner_id?: string; exp?: number };
    const exp = Number(payload.exp || 0);
    if (!Number.isFinite(exp) || exp <= Math.floor(Date.now() / 1000)) {
      return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null };
    }
    return {
      authenticated: true,
      authRequired,
      ownerId: normalizeOwnerId(payload.owner_id),
      expiresAt: exp,
    };
  } catch {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null };
  }
}

export async function getDashboardSessionFromCookies(
  cookieStore?: Awaited<ReturnType<typeof cookies>>,
): Promise<DashboardSession> {
  const store = cookieStore || (await cookies());
  const token = store.get(DASHBOARD_AUTH_COOKIE)?.value || "";
  return decodeDashboardSessionToken(token);
}
