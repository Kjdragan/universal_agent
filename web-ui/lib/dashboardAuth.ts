import crypto from "crypto";
import fs from "fs";
import path from "path";
import { cookies } from "next/headers";

export const DASHBOARD_AUTH_COOKIE = "ua_dashboard_auth";

const DEFAULT_OWNER = "owner_primary";
const OWNER_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;
const OWNERS_CACHE_TTL_MS = 10_000;

type OwnerRecord = {
  ownerId: string;
  active: boolean;
  passwordHash: string;
  roles: string[];
};

let ownersCache: { expiresAt: number; records: Map<string, OwnerRecord> } | null = null;

export type DashboardSession = {
  authenticated: boolean;
  authRequired: boolean;
  ownerId: string;
  expiresAt: number | null;
  roles?: string[];
};

function envFlag(name: string): boolean | null {
  const raw = (process.env[name] || "").trim().toLowerCase();
  if (!raw) return null;
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  if (["0", "false", "no", "off"].includes(raw)) return false;
  return null;
}

function dashboardPassword(): string {
  return (process.env.UA_DASHBOARD_PASSWORD || "").trim();
}

function dashboardOwnerDefault(): string {
  return normalizeOwnerId(process.env.UA_DASHBOARD_OWNER_ID || DEFAULT_OWNER);
}

function dashboardOwnersFile(): string {
  const configured = (process.env.UA_DASHBOARD_OWNERS_FILE || "").trim();
  if (configured) return configured;
  return path.resolve(process.cwd(), "..", "config", "dashboard_owners.json");
}

function safeEqual(a: string, b: string): boolean {
  const aBuf = Buffer.from(a, "utf8");
  const bBuf = Buffer.from(b, "utf8");
  if (aBuf.length !== bBuf.length) return false;
  return crypto.timingSafeEqual(aBuf, bBuf);
}

function normalizeRoles(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const roles = raw
    .map((item) => String(item || "").trim())
    .filter((item) => item.length > 0);
  return Array.from(new Set(roles));
}

function normalizeOwnerRecord(raw: unknown): OwnerRecord | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const ownerId = normalizeOwnerId(row.owner_id as string | undefined);
  if (!ownerId || !OWNER_PATTERN.test(ownerId)) return null;
  const active = row.active === undefined ? true : Boolean(row.active);
  const passwordHash = String(row.password_hash || "").trim();
  if (!passwordHash) return null;
  return {
    ownerId,
    active,
    passwordHash,
    roles: normalizeRoles(row.roles),
  };
}

function parseOwnersPayload(payload: unknown): Map<string, OwnerRecord> {
  const rows = Array.isArray(payload)
    ? payload
    : (payload && typeof payload === "object" && Array.isArray((payload as Record<string, unknown>).owners))
      ? ((payload as Record<string, unknown>).owners as unknown[])
      : [];
  const records = new Map<string, OwnerRecord>();
  for (const row of rows) {
    const normalized = normalizeOwnerRecord(row);
    if (!normalized) continue;
    records.set(normalized.ownerId, normalized);
  }
  return records;
}

function ownersFromFile(): Map<string, OwnerRecord> {
  const filePath = dashboardOwnersFile();
  try {
    if (!fs.existsSync(filePath)) return new Map();
    const raw = fs.readFileSync(filePath, "utf8");
    if (!raw.trim()) return new Map();
    const parsed = JSON.parse(raw) as unknown;
    return parseOwnersPayload(parsed);
  } catch (error) {
    console.warn(`Failed to load dashboard owners file: ${filePath}`, error);
    return new Map();
  }
}

function ownersFromEnv(): Map<string, OwnerRecord> {
  const raw = (process.env.UA_DASHBOARD_OWNERS_JSON || "").trim();
  if (!raw) return new Map();
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parseOwnersPayload(parsed);
  } catch (error) {
    console.warn("Failed to parse UA_DASHBOARD_OWNERS_JSON", error);
    return new Map();
  }
}

function dashboardOwners(): Map<string, OwnerRecord> {
  const now = Date.now();
  if (ownersCache && ownersCache.expiresAt > now) return ownersCache.records;

  // Env-defined owners override file-defined owners by owner_id.
  const merged = ownersFromFile();
  for (const [ownerId, record] of ownersFromEnv()) {
    merged.set(ownerId, record);
  }
  ownersCache = {
    records: merged,
    expiresAt: now + OWNERS_CACHE_TTL_MS,
  };
  return merged;
}

export function dashboardAuthRequired(): boolean {
  const explicit = envFlag("UA_DASHBOARD_AUTH_ENABLED");
  if (explicit !== null) return explicit;
  return dashboardOwners().size > 0 || Boolean(dashboardPassword());
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

function parsePbkdf2Hash(encoded: string): { iterations: number; salt: Buffer; hash: Buffer } | null {
  const parts = encoded.split("$");
  if (parts.length !== 4) return null;
  if (parts[0] !== "pbkdf2_sha256") return null;
  const iterations = Number(parts[1]);
  if (!Number.isFinite(iterations) || iterations < 100_000) return null;
  try {
    const salt = Buffer.from(parts[2], "base64");
    const hash = Buffer.from(parts[3], "base64");
    if (!salt.length || !hash.length) return null;
    return { iterations: Math.floor(iterations), salt, hash };
  } catch {
    return null;
  }
}

function verifyPasswordHash(candidate: string, encoded: string): boolean {
  const parsed = parsePbkdf2Hash(encoded);
  if (!parsed) return false;
  const derived = crypto.pbkdf2Sync(candidate, parsed.salt, parsed.iterations, parsed.hash.length, "sha256");
  if (derived.length !== parsed.hash.length) return false;
  return crypto.timingSafeEqual(derived, parsed.hash);
}

function ownerRoles(ownerId: string): string[] {
  const owner = dashboardOwners().get(normalizeOwnerId(ownerId));
  if (!owner || !owner.active) return [];
  return owner.roles;
}

export function normalizeOwnerId(ownerId: string | undefined | null): string {
  const normalized = (ownerId || "").trim();
  if (!normalized) return dashboardOwnerDefault();
  if (!OWNER_PATTERN.test(normalized)) return dashboardOwnerDefault();
  return normalized;
}

export function validateDashboardPassword(candidate: string, ownerId?: string): boolean {
  if (!dashboardAuthRequired()) return true;

  const normalizedOwner = normalizeOwnerId(ownerId);
  const owners = dashboardOwners();
  if (owners.size > 0) {
    const owner = owners.get(normalizedOwner);
    if (!owner || !owner.active) return false;
    return verifyPasswordHash(candidate, owner.passwordHash);
  }

  const required = dashboardPassword();
  if (!required) return true;
  return safeEqual(candidate, required);
}

export function createDashboardSessionToken(ownerId: string): { token: string; expiresAt: number } {
  const expiresAt = Math.floor(Date.now() / 1000) + dashboardSessionTtlSeconds();
  const normalizedOwner = normalizeOwnerId(ownerId);
  const payload = JSON.stringify({
    owner_id: normalizedOwner,
    exp: expiresAt,
    roles: ownerRoles(normalizedOwner),
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
      roles: ownerRoles(dashboardOwnerDefault()),
    };
  }

  const raw = (token || "").trim();
  if (!raw.includes(".")) {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null, roles: [] };
  }

  const [payloadB64, sig] = raw.split(".", 2);
  if (!payloadB64 || !sig) {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null, roles: [] };
  }

  const expectedSig = signPayload(payloadB64);
  if (!safeEqual(sig, expectedSig)) {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null, roles: [] };
  }

  try {
    const payload = JSON.parse(fromBase64Url(payloadB64)) as { owner_id?: string; exp?: number; roles?: string[] };
    const exp = Number(payload.exp || 0);
    if (!Number.isFinite(exp) || exp <= Math.floor(Date.now() / 1000)) {
      return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null, roles: [] };
    }
    const normalizedOwner = normalizeOwnerId(payload.owner_id);
    return {
      authenticated: true,
      authRequired,
      ownerId: normalizedOwner,
      expiresAt: exp,
      roles: Array.isArray(payload.roles) ? payload.roles : ownerRoles(normalizedOwner),
    };
  } catch {
    return { authenticated: false, authRequired, ownerId: dashboardOwnerDefault(), expiresAt: null, roles: [] };
  }
}

export async function getDashboardSessionFromCookies(
  cookieStore?: Awaited<ReturnType<typeof cookies>>,
): Promise<DashboardSession> {
  const store = cookieStore || (await cookies());
  const token = store.get(DASHBOARD_AUTH_COOKIE)?.value || "";
  return decodeDashboardSessionToken(token);
}
