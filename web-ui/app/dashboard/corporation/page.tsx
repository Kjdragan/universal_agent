"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";

type FactoryCapabilities = {
  factory_id?: string;
  factory_role?: string;
  deployment_profile?: string;
  gateway_mode?: string;
  delegation_mode?: string;
  heartbeat_scope?: string;
  start_ui?: boolean;
  enable_telegram_poll?: boolean;
  enable_vp_coder?: boolean;
  llm_provider_override?: string | null;
  redis_delegation_enabled?: boolean;
  redis_stream_name?: string | null;
  redis_consumer_group?: string | null;
  updated_at?: string;
};

type DelegationMetrics = {
  redis_enabled?: boolean;
  connected?: boolean;
  last_error?: string | null;
  last_publish_at?: string | null;
  published_total?: number;
};

type CapabilitiesResponse = {
  factory?: FactoryCapabilities;
  delegation?: DelegationMetrics;
};

type FactoryRegistration = {
  factory_id: string;
  factory_role?: string;
  deployment_profile?: string;
  source?: string;
  registration_status?: string;
  heartbeat_latency_ms?: number | null;
  capabilities?: string[];
  metadata?: Record<string, unknown>;
  first_seen_at?: string;
  last_seen_at?: string;
  updated_at?: string;
};

type RegistrationsResponse = {
  registrations?: FactoryRegistration[];
  count?: number;
  headquarters_factory_id?: string;
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function secondsSince(value: string): number | null {
  const ts = toEpochMs(value);
  if (ts === null) return null;
  return Math.max(0, (Date.now() - ts) / 1000);
}

function statusPill(status: string): string {
  const normalized = asText(status).toLowerCase();
  if (normalized === "online") return "border-emerald-600/40 bg-emerald-900/20 text-emerald-200";
  if (normalized === "offline") return "border-rose-600/40 bg-rose-900/20 text-rose-200";
  return "border-amber-600/40 bg-amber-900/20 text-amber-200";
}

export default function DashboardCorporationPage() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [capabilities, setCapabilities] = useState<FactoryCapabilities | null>(null);
  const [delegation, setDelegation] = useState<DelegationMetrics | null>(null);
  const [registrations, setRegistrations] = useState<FactoryRegistration[]>([]);
  const [headquartersFactoryId, setHeadquartersFactoryId] = useState("");
  const [registrationsForbidden, setRegistrationsForbidden] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string>("");

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const [capsRes, regsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/factory/capabilities`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/factory/registrations?limit=500`, { cache: "no-store" }),
      ]);

      let nextCaps: FactoryCapabilities | null = null;
      let nextDelegation: DelegationMetrics | null = null;
      if (capsRes.ok) {
        const payload = (await capsRes.json()) as CapabilitiesResponse;
        nextCaps = (payload.factory || null) as FactoryCapabilities | null;
        nextDelegation = (payload.delegation || null) as DelegationMetrics | null;
      } else {
        const detail = await capsRes.text().catch(() => "");
        throw new Error(`Failed to load factory capabilities (${capsRes.status}) ${detail}`.trim());
      }

      if (regsRes.status === 403) {
        setRegistrationsForbidden(true);
        setRegistrations([]);
        setHeadquartersFactoryId("");
      } else if (regsRes.ok) {
        const payload = (await regsRes.json()) as RegistrationsResponse;
        setRegistrationsForbidden(false);
        setRegistrations(Array.isArray(payload.registrations) ? payload.registrations : []);
        setHeadquartersFactoryId(asText(payload.headquarters_factory_id));
      } else {
        const detail = await regsRes.text().catch(() => "");
        throw new Error(`Failed to load registrations (${regsRes.status}) ${detail}`.trim());
      }

      setCapabilities(nextCaps);
      setDelegation(nextDelegation);
      setLastUpdatedAt(new Date().toISOString());
    } catch (err: any) {
      setError(err?.message || "Failed to load corporation view.");
    } finally {
      if (silent) setRefreshing(false);
      else setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    const timer = setInterval(() => {
      void load(true);
    }, 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const isHeadquarters = asText(capabilities?.factory_role).toUpperCase() === "HEADQUARTERS";
  const onlineCount = useMemo(
    () => registrations.filter((row) => asText(row.registration_status).toLowerCase() === "online").length,
    [registrations],
  );
  const staleCount = useMemo(
    () => registrations.filter((row) => {
      const age = secondsSince(asText(row.last_seen_at));
      return age !== null && age > 5 * 60;
    }).length,
    [registrations],
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">Corporation View</h1>
          <p className="text-sm text-slate-400">
            Fleet visibility for Headquarters: registrations, role posture, and delegation bus status.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void load(false)}
            className="rounded-md border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800/70"
          >
            Refresh
          </button>
          <span className="text-xs text-slate-500">
            {refreshing ? "Refreshing..." : `Updated ${formatDateTimeTz(lastUpdatedAt || undefined, { placeholder: "--" })}`}
          </span>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-700/40 bg-rose-900/20 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      {loading && !capabilities ? (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 px-4 py-4 text-sm text-slate-300">
          Loading fleet state...
        </div>
      ) : null}

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Factory Role</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">{asText(capabilities?.factory_role) || "--"}</div>
          <div className="mt-1 text-xs text-slate-400">Gateway mode: {asText(capabilities?.gateway_mode) || "--"}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Fleet Size</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">{registrations.length}</div>
          <div className="mt-1 text-xs text-slate-400">Online: {onlineCount}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Stale Factories (&gt;5m)</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">{staleCount}</div>
          <div className="mt-1 text-xs text-slate-400">Based on last_seen_at heartbeat</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Delegation Bus</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">
            {delegation?.connected ? "Connected" : delegation?.redis_enabled ? "Disconnected" : "Disabled"}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            Published: {Number.isFinite(delegation?.published_total) ? String(delegation?.published_total) : "0"}
          </div>
        </div>
      </section>

      {!isHeadquarters || registrationsForbidden ? (
        <section className="rounded-xl border border-amber-700/40 bg-amber-900/20 p-4 text-sm text-amber-100">
          This page is only available when FACTORY_ROLE is HEADQUARTERS.
        </section>
      ) : (
        <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-slate-100">Registered Factories</h2>
            <div className="text-xs text-slate-400">Headquarters factory_id: {headquartersFactoryId || "--"}</div>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-2 py-2">Factory</th>
                  <th className="px-2 py-2">Role</th>
                  <th className="px-2 py-2">Status</th>
                  <th className="px-2 py-2">Profile</th>
                  <th className="px-2 py-2">Heartbeat</th>
                  <th className="px-2 py-2">Last Seen</th>
                  <th className="px-2 py-2">Capabilities</th>
                </tr>
              </thead>
              <tbody>
                {registrations.length === 0 ? (
                  <tr>
                    <td className="px-2 py-4 text-slate-400" colSpan={7}>
                      No registrations available.
                    </td>
                  </tr>
                ) : (
                  registrations.map((row) => {
                    const capabilityLabels = Array.isArray(row.capabilities) ? row.capabilities : [];
                    return (
                      <tr key={`${row.factory_id}-${row.last_seen_at || ""}`} className="border-t border-slate-800/80 align-top">
                        <td className="px-2 py-2 text-slate-200">{asText(row.factory_id) || "--"}</td>
                        <td className="px-2 py-2 text-slate-300">{asText(row.factory_role) || "--"}</td>
                        <td className="px-2 py-2">
                          <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${statusPill(asText(row.registration_status))}`}>
                            {asText(row.registration_status) || "--"}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-slate-300">{asText(row.deployment_profile) || "--"}</td>
                        <td className="px-2 py-2 text-slate-300">
                          {row.heartbeat_latency_ms != null ? `${Number(row.heartbeat_latency_ms).toFixed(1)} ms` : "--"}
                        </td>
                        <td className="px-2 py-2 text-slate-300">
                          <div>{formatDateTimeTz(asText(row.last_seen_at) || undefined, { placeholder: "--" })}</div>
                          <div className="text-xs text-slate-500">
                            {(() => {
                              const age = secondsSince(asText(row.last_seen_at));
                              if (age === null) return "--";
                              if (age < 60) return "just now";
                              if (age < 3600) return `${Math.floor(age / 60)}m ago`;
                              if (age < 86400) return `${Math.floor(age / 3600)}h ago`;
                              return `${Math.floor(age / 86400)}d ago`;
                            })()}
                          </div>
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex max-w-md flex-wrap gap-1">
                            {capabilityLabels.length === 0 ? (
                              <span className="text-xs text-slate-500">--</span>
                            ) : (
                              capabilityLabels.slice(0, 10).map((label) => (
                                <span key={label} className="rounded border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-xs text-slate-300">
                                  {label}
                                </span>
                              ))
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

