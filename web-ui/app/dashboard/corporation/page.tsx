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

type DelegationHistoryEntry = {
  mission_id?: string;
  mission_type?: string;
  status?: string;
  vp_id?: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
  objective?: string;
};

type DelegationHistoryResponse = {
  missions?: DelegationHistoryEntry[];
  total?: number;
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
  if (normalized === "paused") return "border-sky-600/40 bg-sky-900/20 text-sky-200";
  return "border-amber-600/40 bg-amber-900/20 text-amber-200";
}

function freshnessColor(ageSeconds: number | null): string {
  if (ageSeconds === null) return "text-slate-500";
  if (ageSeconds < 120) return "text-emerald-400";
  if (ageSeconds < 300) return "text-emerald-300/70";
  if (ageSeconds < 900) return "text-amber-400";
  return "text-rose-400";
}

function latencyColor(ms: number | null | undefined): string {
  if (ms == null) return "text-slate-500";
  if (ms < 100) return "text-emerald-400";
  if (ms < 300) return "text-emerald-300/70";
  if (ms < 1000) return "text-amber-400";
  return "text-rose-400";
}

function freshnessLabel(ageSeconds: number | null): string {
  if (ageSeconds === null) return "--";
  if (ageSeconds < 60) return "just now";
  if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m ago`;
  if (ageSeconds < 86400) return `${Math.floor(ageSeconds / 3600)}h ago`;
  return `${Math.floor(ageSeconds / 86400)}d ago`;
}

function missionStatusPill(status: string): string {
  const s = asText(status).toLowerCase();
  if (s === "completed") return "border-emerald-600/40 bg-emerald-900/20 text-emerald-200";
  if (s === "failed" || s === "error") return "border-rose-600/40 bg-rose-900/20 text-rose-200";
  if (s === "running" || s === "claimed") return "border-sky-600/40 bg-sky-900/20 text-sky-200";
  if (s === "queued") return "border-slate-600/40 bg-slate-800/20 text-slate-300";
  return "border-amber-600/40 bg-amber-900/20 text-amber-200";
}

function isGatewayUpstreamUnavailable(status: number, detail: string): boolean {
  if (status !== 502) return false;
  return detail.toLowerCase().includes("gateway upstream unavailable");
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
  const [delegationHistory, setDelegationHistory] = useState<DelegationHistoryEntry[]>([]);
  const [expandedFactory, setExpandedFactory] = useState<string | null>(null);
  const [controllingFactory, setControllingFactory] = useState<string | null>(null);
  const [systemTimers, setSystemTimers] = useState<any[]>([]);

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    if (!silent) setError("");
    try {
      const capsRes = await fetch(`${API_BASE}/api/v1/factory/capabilities`, { cache: "no-store" });

      let nextCaps: FactoryCapabilities | null = null;
      let nextDelegation: DelegationMetrics | null = null;
      if (capsRes.ok) {
        const payload = (await capsRes.json()) as CapabilitiesResponse;
        nextCaps = (payload.factory || null) as FactoryCapabilities | null;
        nextDelegation = (payload.delegation || null) as DelegationMetrics | null;
      } else {
        const detail = await capsRes.text().catch(() => "");
        if (isGatewayUpstreamUnavailable(capsRes.status, detail)) {
          if (!silent) {
            setError("Gateway is temporarily unavailable. Please retry in a few seconds.");
          }
          return;
        }
        throw new Error(`Failed to load factory capabilities (${capsRes.status}) ${detail}`.trim());
      }

      const role = asText(nextCaps?.factory_role).toUpperCase();
      const gatewayMode = asText(nextCaps?.gateway_mode).toLowerCase();
      const headquartersMode = role === "HEADQUARTERS" && gatewayMode === "full";

      setCapabilities(nextCaps);
      setDelegation(nextDelegation);
      setLastUpdatedAt(new Date().toISOString());

      if (!headquartersMode) {
        setRegistrationsForbidden(false);
        setRegistrations([]);
        setHeadquartersFactoryId("");
        setDelegationHistory([]);
        setSystemTimers([]);
        return;
      }

      const [regsRes, histRes, timersRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/factory/registrations?limit=500`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/ops/delegation/history?limit=20`, { cache: "no-store" }).catch(() => null),
        fetch(`${API_BASE}/api/v1/ops/timers`, { cache: "no-store" }).catch(() => null),
      ]);

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

      if (histRes && histRes.ok) {
        const payload = (await histRes.json()) as DelegationHistoryResponse;
        setDelegationHistory(Array.isArray(payload.missions) ? payload.missions : []);
      }

      if (timersRes && timersRes.ok) {
        try {
          const tp = await timersRes.json();
          setSystemTimers(Array.isArray(tp.timers) ? tp.timers : []);
        } catch { setSystemTimers([]); }
      }
    } catch (err: any) {
      if (!silent) {
        setError(err?.message || "Failed to load corporation view.");
      }
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
  const offlineCount = useMemo(
    () => registrations.filter((row) => asText(row.registration_status).toLowerCase() === "offline").length,
    [registrations],
  );
  const staleCount = useMemo(
    () => registrations.filter((row) => {
      const s = asText(row.registration_status).toLowerCase();
      return s === "stale";
    }).length,
    [registrations],
  );
  const pausedCount = useMemo(
    () => registrations.filter((row) => asText(row.registration_status).toLowerCase() === "paused").length,
    [registrations],
  );

  const controlFactory = async (factoryId: string, action: "pause" | "resume") => {
    setControllingFactory(factoryId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/factory/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_factory_id: factoryId, action }),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        setError(`Factory control failed (${res.status}): ${detail}`);
      } else {
        // Refresh data to show new status
        await load(true);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to control factory.");
    } finally {
      setControllingFactory(null);
    }
  };

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
          <div className="mt-1 flex gap-3 text-xs">
            <span className="text-emerald-400">{onlineCount} online</span>
            {pausedCount > 0 && <span className="text-sky-400">{pausedCount} paused</span>}
            {staleCount > 0 && <span className="text-amber-400">{staleCount} stale</span>}
            {offlineCount > 0 && <span className="text-rose-400">{offlineCount} offline</span>}
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Delegation Bus</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">
            {delegation?.connected ? (
              <span className="text-emerald-400">Connected</span>
            ) : delegation?.redis_enabled ? (
              <span className="text-rose-400">Disconnected</span>
            ) : (
              <span className="text-slate-400">Disabled</span>
            )}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            Published: {Number.isFinite(delegation?.published_total) ? String(delegation?.published_total) : "0"}
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Delegation Activity</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">{delegationHistory.length}</div>
          <div className="mt-1 text-xs text-slate-400">
            Recent missions (last 24h)
          </div>
        </div>
      </section>

      {!isHeadquarters || registrationsForbidden ? (
        <section className="rounded-xl border border-amber-700/40 bg-amber-900/20 p-4 text-sm text-amber-100">
          This page is only available when FACTORY_ROLE is HEADQUARTERS.
        </section>
      ) : (
        <>
          <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-semibold text-slate-100">Registered Factories</h2>
              <div className="text-xs text-slate-400">HQ: {headquartersFactoryId || "--"}</div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-2 py-2">Factory</th>
                    <th className="px-2 py-2">Role</th>
                    <th className="px-2 py-2">Status</th>
                    <th className="px-2 py-2">Latency</th>
                    <th className="px-2 py-2">Freshness</th>
                    <th className="px-2 py-2">Last Seen</th>
                    <th className="px-2 py-2">Capabilities</th>
                    <th className="px-2 py-2">Control</th>
                  </tr>
                </thead>
                <tbody>
                  {registrations.length === 0 ? (
                    <tr>
                      <td className="px-2 py-4 text-slate-400" colSpan={8}>
                        No registrations available.
                      </td>
                    </tr>
                  ) : (
                    registrations.map((row) => {
                      const capabilityLabels = Array.isArray(row.capabilities) ? row.capabilities : [];
                      const ageSeconds = secondsSince(asText(row.last_seen_at));
                      const isExpanded = expandedFactory === row.factory_id;
                      const meta = (row.metadata && typeof row.metadata === "object") ? row.metadata : {};

                      return (
                        <tr
                          key={`${row.factory_id}-${row.last_seen_at || ""}`}
                          className="border-t border-slate-800/80 align-top cursor-pointer hover:bg-slate-800/30 transition-colors"
                          onClick={() => setExpandedFactory(isExpanded ? null : row.factory_id)}
                        >
                          <td className="px-2 py-2">
                            <div className="text-slate-200 font-medium">{asText(row.factory_id) || "--"}</div>
                            <div className="text-xs text-slate-500">{asText(row.deployment_profile) || "--"}</div>
                            {isExpanded && (
                              <div className="mt-2 space-y-1 text-xs text-slate-400">
                                <div>Source: {asText(row.source) || "--"}</div>
                                <div>First seen: {formatDateTimeTz(asText(row.first_seen_at) || undefined, { placeholder: "--" })}</div>
                                {Object.keys(meta).length > 0 && (
                                  <div className="mt-1">
                                    <div className="text-slate-500 mb-1">Metadata:</div>
                                    <pre className="rounded bg-slate-900 p-2 text-[10px] text-slate-400 overflow-x-auto max-w-xs">
                                      {JSON.stringify(meta, null, 2).slice(0, 500)}
                                    </pre>
                                  </div>
                                )}
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-2 text-slate-300">{asText(row.factory_role) || "--"}</td>
                          <td className="px-2 py-2">
                            <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${statusPill(asText(row.registration_status))}`}>
                              {asText(row.registration_status) || "--"}
                            </span>
                          </td>
                          <td className="px-2 py-2">
                            <span className={latencyColor(row.heartbeat_latency_ms)}>
                              {row.heartbeat_latency_ms != null ? `${Number(row.heartbeat_latency_ms).toFixed(1)} ms` : "--"}
                            </span>
                          </td>
                          <td className="px-2 py-2">
                            <div className="flex items-center gap-1.5">
                              <span className={`inline-block h-2 w-2 rounded-full ${
                                ageSeconds === null ? "bg-slate-600" :
                                ageSeconds < 120 ? "bg-emerald-500" :
                                ageSeconds < 300 ? "bg-emerald-500/60" :
                                ageSeconds < 900 ? "bg-amber-500" :
                                "bg-rose-500"
                              }`} />
                              <span className={`text-xs ${freshnessColor(ageSeconds)}`}>
                                {freshnessLabel(ageSeconds)}
                              </span>
                            </div>
                          </td>
                          <td className="px-2 py-2 text-slate-400 text-xs">
                            {formatDateTimeTz(asText(row.last_seen_at) || undefined, { placeholder: "--" })}
                          </td>
                          <td className="px-2 py-2">
                            <div className="flex max-w-xs flex-wrap gap-1">
                              {capabilityLabels.length === 0 ? (
                                <span className="text-xs text-slate-500">--</span>
                              ) : (
                                capabilityLabels.slice(0, 8).map((label) => (
                                  <span key={label} className="rounded border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-300">
                                    {label}
                                  </span>
                                ))
                              )}
                              {capabilityLabels.length > 8 && (
                                <span className="text-[10px] text-slate-500">+{capabilityLabels.length - 8}</span>
                              )}
                            </div>
                          </td>
                          <td className="px-2 py-2">
                            {asText(row.factory_role).toUpperCase() !== "HEADQUARTERS" && (
                              (() => {
                                const currentStatus = asText(row.registration_status).toLowerCase();
                                const isPaused = currentStatus === "paused";
                                const isControlling = controllingFactory === row.factory_id;
                                return (
                                  <button
                                    type="button"
                                    disabled={isControlling}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      void controlFactory(row.factory_id, isPaused ? "resume" : "pause");
                                    }}
                                    className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                                      isControlling
                                        ? "border-slate-700 bg-slate-800/50 text-slate-500 cursor-wait"
                                        : isPaused
                                          ? "border-emerald-700/50 bg-emerald-900/30 text-emerald-300 hover:bg-emerald-900/50"
                                          : "border-amber-700/50 bg-amber-900/30 text-amber-300 hover:bg-amber-900/50"
                                    }`}
                                  >
                                    {isControlling ? "..." : isPaused ? "Resume" : "Pause"}
                                  </button>
                                );
                              })()
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {delegationHistory.length > 0 && (
            <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <h2 className="mb-3 text-lg font-semibold text-slate-100">Delegation History</h2>
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-2 py-2">Mission</th>
                      <th className="px-2 py-2">Type</th>
                      <th className="px-2 py-2">Status</th>
                      <th className="px-2 py-2">VP</th>
                      <th className="px-2 py-2">Source</th>
                      <th className="px-2 py-2">Created</th>
                      <th className="px-2 py-2">Objective</th>
                    </tr>
                  </thead>
                  <tbody>
                    {delegationHistory.map((m, idx) => (
                      <tr key={m.mission_id || idx} className="border-t border-slate-800/80 align-top">
                        <td className="px-2 py-2 text-slate-300 font-mono text-xs">{asText(m.mission_id).slice(0, 20) || "--"}</td>
                        <td className="px-2 py-2 text-slate-300 text-xs">{asText(m.mission_type) || "--"}</td>
                        <td className="px-2 py-2">
                          <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${missionStatusPill(asText(m.status))}`}>
                            {asText(m.status) || "--"}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-slate-400 text-xs">{asText(m.vp_id) || "--"}</td>
                        <td className="px-2 py-2 text-slate-400 text-xs">{asText(m.source) || "--"}</td>
                        <td className="px-2 py-2 text-slate-400 text-xs">
                          {formatDateTimeTz(asText(m.created_at) || undefined, { placeholder: "--" })}
                        </td>
                        <td className="px-2 py-2 text-slate-400 text-xs max-w-xs truncate">
                          {asText(m.objective).slice(0, 80) || "--"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {systemTimers.length > 0 && (
            <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <h2 className="mb-3 text-lg font-semibold text-slate-100">System Timers ({systemTimers.length})</h2>
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-2 py-2">Timer</th>
                      <th className="px-2 py-2">Next</th>
                      <th className="px-2 py-2">Last</th>
                      <th className="px-2 py-2">Activates</th>
                    </tr>
                  </thead>
                  <tbody>
                    {systemTimers.map((t: any, idx: number) => {
                      const unit = asText(t.unit || t.UNIT || "");
                      const next = asText(t.next || t.NEXT || "");
                      const last = asText(t.last || t.LAST || "");
                      const activates = asText(t.activates || t.ACTIVATES || "");
                      return (
                        <tr key={unit || idx} className="border-t border-slate-800/80 align-top">
                          <td className="px-2 py-2 text-slate-200 font-mono text-xs">{unit || "--"}</td>
                          <td className="px-2 py-2 text-slate-400 text-xs">{next || "--"}</td>
                          <td className="px-2 py-2 text-slate-400 text-xs">{last || "--"}</td>
                          <td className="px-2 py-2 text-slate-400 text-xs">{activates || "--"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
