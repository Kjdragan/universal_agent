"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = "/api/dashboard/gateway";

type SupervisorRegistryItem = {
  id: string;
  label: string;
  enabled?: boolean;
  scope?: string;
  default?: boolean;
};

const DEFAULT_SUPERVISORS: SupervisorRegistryItem[] = [
  { id: "factory-supervisor", label: "Factory Supervisor", enabled: true, scope: "fleet", default: true },
  { id: "csi-supervisor", label: "CSI Supervisor", enabled: true, scope: "intelligence" },
];

type Recommendation = {
  action?: string;
  rationale?: string;
  endpoint_or_command?: string;
  requires_confirmation?: boolean;
};

type SupervisorSnapshot = {
  status?: string;
  supervisor_id?: string;
  generated_at?: string;
  summary?: string;
  severity?: string;
  kpis?: Record<string, unknown>;
  diagnostics?: Record<string, unknown>;
  recommendations?: Recommendation[];
  artifacts?: {
    markdown_path?: string;
    json_path?: string;
    markdown_storage_href?: string;
    json_storage_href?: string;
  };
};

type SupervisorRun = {
  generated_at?: string;
  summary?: string;
  severity?: string;
  artifacts?: {
    markdown_path?: string;
    json_path?: string;
    markdown_storage_href?: string;
    json_storage_href?: string;
  };
};

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function severityClasses(value: string): string {
  const normalized = asText(value).toLowerCase();
  if (normalized === "critical") return "border-rose-600/40 bg-rose-900/20 text-rose-200";
  if (normalized === "warning") return "border-amber-600/40 bg-amber-900/20 text-amber-200";
  return "border-emerald-600/40 bg-emerald-900/20 text-emerald-200";
}

function fmt(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value == null) return "--";
  return String(value);
}

export default function SupervisorAgentsPage() {
  const [registry, setRegistry] = useState<SupervisorRegistryItem[]>(DEFAULT_SUPERVISORS);
  const [selected, setSelected] = useState<string>("factory-supervisor");
  const [snapshot, setSnapshot] = useState<SupervisorSnapshot | null>(null);
  const [runs, setRuns] = useState<SupervisorRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [isHeadquarters, setIsHeadquarters] = useState(false);

  const loadBase = useCallback(async () => {
    const [capsRes, regRes] = await Promise.all([
      fetch(`${API_BASE}/api/v1/factory/capabilities`, { cache: "no-store" }),
      fetch(`${API_BASE}/api/v1/dashboard/supervisors/registry`, { cache: "no-store" }),
    ]);

    if (!capsRes.ok) {
      throw new Error(`Failed loading capabilities (${capsRes.status})`);
    }
    const capsPayload = await capsRes.json();
    const role = asText(capsPayload?.factory?.factory_role).toUpperCase();
    const gatewayMode = asText(capsPayload?.factory?.gateway_mode).toLowerCase();
    const hq = role === "HEADQUARTERS" && gatewayMode === "full";
    setIsHeadquarters(hq);

    if (!hq) {
      setRegistry([]);
      setSnapshot(null);
      setRuns([]);
      return;
    }

    if (!regRes.ok) {
      throw new Error(`Failed loading supervisor registry (${regRes.status})`);
    }
    const regPayload = await regRes.json();
    const rows = Array.isArray(regPayload?.supervisors)
      ? (regPayload.supervisors as SupervisorRegistryItem[])
      : DEFAULT_SUPERVISORS;
    setRegistry(rows);

    const defaultSupervisor = rows.find((item) => item.default) || rows[0];
    if (defaultSupervisor?.id && !rows.some((item) => item.id === selected)) {
      setSelected(defaultSupervisor.id);
    }
  }, [selected]);

  const loadSelected = useCallback(
    async (silent = false) => {
      if (!isHeadquarters || !selected) return;
      if (silent) setRefreshing(true);
      const [snapshotRes, runsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/supervisors/${encodeURIComponent(selected)}/snapshot`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/supervisors/${encodeURIComponent(selected)}/runs?limit=20`, { cache: "no-store" }),
      ]);

      if (!snapshotRes.ok) {
        throw new Error(`Failed loading snapshot (${snapshotRes.status})`);
      }
      const snapshotPayload = (await snapshotRes.json()) as SupervisorSnapshot;
      setSnapshot(snapshotPayload);

      if (runsRes.ok) {
        const runsPayload = await runsRes.json();
        const rows = Array.isArray(runsPayload?.runs) ? (runsPayload.runs as SupervisorRun[]) : [];
        setRuns(rows);
      } else {
        setRuns([]);
      }

      if (silent) setRefreshing(false);
    },
    [isHeadquarters, selected],
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      await loadBase();
    } catch (err: any) {
      setError(err?.message || "Failed to load supervisor agents.");
    } finally {
      setLoading(false);
    }
  }, [loadBase]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!isHeadquarters || !selected) return;
    setError("");
    void loadSelected(false).catch((err: any) => {
      setError(err?.message || "Failed to load supervisor snapshot.");
      setRefreshing(false);
    });
  }, [isHeadquarters, selected, loadSelected]);

  useEffect(() => {
    if (!isHeadquarters || !selected) return;
    const timer = setInterval(() => {
      void loadSelected(true).catch(() => setRefreshing(false));
    }, 15000);
    return () => clearInterval(timer);
  }, [isHeadquarters, selected, loadSelected]);

  const handleRunNow = useCallback(async () => {
    if (!selected) return;
    setRunning(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/supervisors/${encodeURIComponent(selected)}/run`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: "dashboard_manual_run" }),
      });
      if (!res.ok) {
        throw new Error(`Failed to run supervisor (${res.status})`);
      }
      const payload = (await res.json()) as SupervisorSnapshot;
      setSnapshot(payload);
      await loadSelected(true);
    } catch (err: any) {
      setError(err?.message || "Failed to run supervisor.");
    } finally {
      setRunning(false);
    }
  }, [selected, loadSelected]);

  const kpiRows = useMemo(() => {
    const kpis = snapshot?.kpis;
    if (!kpis || typeof kpis !== "object") return [];
    return Object.entries(kpis);
  }, [snapshot]);

  const diagnosticsText = useMemo(() => {
    try {
      return JSON.stringify(snapshot?.diagnostics || {}, null, 2);
    } catch {
      return "{}";
    }
  }, [snapshot]);

  const recommendations = Array.isArray(snapshot?.recommendations) ? snapshot?.recommendations : [];

  if (loading) {
    return <div className="p-6 text-slate-400">Loading Supervisor Agents...</div>;
  }

  if (!isHeadquarters) {
    return (
      <div className="p-6">
        <h1 className="text-xl font-semibold tracking-tight">Supervisor Agents</h1>
        <p className="mt-2 text-sm text-slate-400">
          This tab is HQ-only. Current factory role does not expose corporation-level supervision views.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Supervisor Agents</h1>
          <p className="text-sm text-slate-400">Switch between supervisor snapshots for fleet and CSI visibility.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void loadSelected(false)}
            className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            disabled={refreshing || running}
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button
            onClick={() => void handleRunNow()}
            className="rounded border border-emerald-700 bg-emerald-900/30 px-3 py-1.5 text-xs text-emerald-100 hover:bg-emerald-900/50"
            disabled={running}
          >
            {running ? "Running..." : "Run now"}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {registry.map((item) => {
          const active = item.id === selected;
          return (
            <button
              key={item.id}
              onClick={() => setSelected(item.id)}
              className={[
                "rounded border px-3 py-1.5 text-xs",
                active
                  ? "border-sky-600/60 bg-sky-900/30 text-sky-100"
                  : "border-slate-700 bg-slate-900/20 text-slate-300 hover:bg-slate-800",
              ].join(" ")}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      {error ? <div className="rounded border border-rose-600/40 bg-rose-900/20 px-3 py-2 text-sm text-rose-200">{error}</div> : null}

      <div className="rounded border border-slate-800 bg-slate-950/30 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-400">Status Summary</h2>
          <span className={`rounded border px-2 py-0.5 text-xs ${severityClasses(asText(snapshot?.severity))}`}>
            {asText(snapshot?.severity) || "info"}
          </span>
          <span className="text-xs text-slate-500">{asText(snapshot?.generated_at) || "--"}</span>
        </div>
        <p className="mt-2 text-sm text-slate-200">{asText(snapshot?.summary) || "No summary available."}</p>
      </div>

      <div className="rounded border border-slate-800 bg-slate-950/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-400">KPI Cards</h2>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {kpiRows.length === 0 ? (
            <div className="text-sm text-slate-500">No KPI data.</div>
          ) : (
            kpiRows.map(([key, value]) => (
              <div key={key} className="rounded border border-slate-800 bg-slate-900/30 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{key}</div>
                <div className="mt-1 text-sm text-slate-100">{fmt(value)}</div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="rounded border border-slate-800 bg-slate-950/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-400">Flow Diagnostics</h2>
        <pre className="mt-3 max-h-[360px] overflow-auto rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200">
          {diagnosticsText}
        </pre>
      </div>

      <div className="rounded border border-slate-800 bg-slate-950/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-400">Recommendations</h2>
        {recommendations.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No recommendations.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {recommendations.map((rec, idx) => (
              <div key={`${idx}-${rec.action || "recommendation"}`} className="rounded border border-slate-800 bg-slate-900/30 p-3">
                <div className="text-sm text-slate-100">{rec.action || "Recommendation"}</div>
                <div className="mt-1 text-xs text-slate-400">{rec.rationale || ""}</div>
                <div className="mt-1 text-[11px] text-slate-500">{rec.endpoint_or_command || ""}</div>
                <div className="mt-1 text-[11px] text-slate-500">
                  Requires confirmation: {rec.requires_confirmation ? "yes" : "no"}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded border border-slate-800 bg-slate-950/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-400">Latest Brief</h2>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
          {asText(snapshot?.artifacts?.markdown_storage_href) ? (
            <a href={asText(snapshot?.artifacts?.markdown_storage_href)} className="text-sky-300 hover:text-sky-200" target="_blank" rel="noreferrer">
              Open Markdown Brief
            </a>
          ) : (
            <span className="text-slate-500">No markdown brief yet.</span>
          )}
          {asText(snapshot?.artifacts?.json_storage_href) ? (
            <a href={asText(snapshot?.artifacts?.json_storage_href)} className="text-sky-300 hover:text-sky-200" target="_blank" rel="noreferrer">
              Open JSON Snapshot
            </a>
          ) : null}
        </div>
        {runs.length > 0 ? (
          <div className="mt-3 space-y-1 text-xs text-slate-300">
            {runs.slice(0, 5).map((run, idx) => (
              <div key={`${idx}-${run.generated_at || "run"}`} className="rounded border border-slate-800 bg-slate-900/20 px-2 py-1">
                <span className="text-slate-400">{asText(run.generated_at) || "--"}</span>
                <span className="mx-2">•</span>
                <span>{asText(run.summary) || "Supervisor brief"}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
