"use client";

import { Fragment, useCallback, useEffect, useState } from "react";

const API_BASE = "/api/dashboard/gateway";
const TIERS = ["opus", "sonnet", "mid", "haiku"] as const;
type Tier = (typeof TIERS)[number];

type WindowStat = {
  total: number;
  r429: number;
  fup: number;
  fup_texted: number;
  pct: number;
  tiers: Record<string, { total: number; r429: number; pct?: number; fup?: number; fup_texted?: number }>;
};

type StatusPayload = {
  generated_at?: number;
  events?: {
    available?: boolean;
    windows?: Record<string, WindowStat>;
    callers_429_60m?: { caller: string; count: number }[];
  };
  snapshot?: {
    tier_caps?: Record<string, number | null>;
    total_429s?: number | null;
    total_fup_events?: number | null;
    total_429s_exhausted?: number | null;
    total_succeeded_after_retry?: number | null;
    acquire_pause_until?: number | null;
    cross_loop_conflicts?: number | null;
    process_name?: string | null;
    snapshot_written_at?: number | null;
  };
  control?: {
    intervention_level?: number;
    global_pause_active?: boolean;
    global_pause?: { until?: number | null; reason?: string };
    tier_pause?: Record<string, boolean>;
    tier_overrides?: Record<string, { cap?: number; max?: number }>;
    updated_by?: string;
  };
  error?: string;
};

type ActivityHealth = {
  status: "healthy" | "degraded" | "critical" | "no_probe" | "unknown";
  source: "invariant" | "systemd" | "none";
  detail: string;
  deep_probe: boolean;
  finding_id?: string | null;
};

type ActivityItem = {
  unit: string;
  label: string;
  group: string;
  heavy_zai: boolean;
  watchdog_guarded: boolean;
  off_actions: string[];
  on_actions: string[];
  active_state: string;
  sub_state: string;
  unit_file_state: string;
  is_active: boolean;
  is_enabled: boolean;
  is_masked: boolean;
  last_run: string;
  next_run: string;
  health?: ActivityHealth; // per-process health verdict (optional — degrades if backend is older)
};

type InprocLoop = {
  key: string;
  label: string;
  env_var: string;
  enabled: boolean;
  note: string;
  health?: ActivityHealth;
};

type ActivitiesPayload = {
  actions_allowed?: string[];
  activities?: ActivityItem[];
  inprocess?: InprocLoop[];
  watchdog_guarded_units?: string[];
  error?: string;
};

function primaryToggle(a: ActivityItem): { label: string; actions: string[]; kind: "on" | "off" } {
  if (a.is_masked) return { label: "Unmask & start", actions: a.on_actions, kind: "on" };
  if (a.is_active) return { label: a.watchdog_guarded ? "Stop + mask" : "Stop", actions: a.off_actions, kind: "off" };
  return { label: "Start", actions: a.on_actions, kind: "on" };
}

const LEVEL_LABELS: Record<number, { name: string; desc: string }> = {
  0: { name: "L0 Normal", desc: "Env-default caps, no pause" },
  1: { name: "L1 Trim", desc: "Halve the hot tiers" },
  2: { name: "L2 Minimal", desc: "Serialize every tier (cap 1)" },
  3: { name: "L3 Cheap-only", desc: "Cap 1 + hard-stop opus & mid" },
  4: { name: "L4 Global pause", desc: "Abort ALL ZAI (TTL'd) — the nuke" },
};

function fmtAgo(ts?: number | null): string {
  if (!ts) return "—";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 0) return "now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function fmtCountdown(until?: number | null): string {
  if (!until) return "no expiry";
  const s = Math.round(until - Date.now() / 1000);
  if (s <= 0) return "expired";
  if (s < 60) return `${s}s left`;
  return `${Math.round(s / 60)}m left`;
}

function pctColor(pct: number): string {
  if (pct >= 50) return "text-red-500";
  if (pct >= 20) return "text-amber-500";
  if (pct > 0) return "text-yellow-400";
  return "text-emerald-500";
}

// Per-process HEALTH badge. GREEN ("ok") means a real last-run success AND a deep
// invariant covers the job. "ok?" (zinc) = running but output not deeply verified
// (the blind-spot class — green systemd, no deep probe). "no probe" = no signal at
// all. Returns null when health is absent (older backend) so nothing renders.
function healthBadge(h?: ActivityHealth): { text: string; cls: string } | null {
  if (!h) return null;
  switch (h.status) {
    case "critical":
      return { text: "critical", cls: "bg-red-500/15 text-red-400" };
    case "degraded":
      return { text: "degraded", cls: "bg-amber-500/15 text-amber-500" };
    case "healthy":
      return h.deep_probe
        ? { text: "ok", cls: "bg-emerald-500/15 text-emerald-500" }
        : { text: "ok?", cls: "bg-zinc-500/15 text-zinc-400" };
    case "no_probe":
      return { text: "no probe", cls: "bg-zinc-500/15 text-zinc-400" };
    default:
      return { text: "—", cls: "bg-zinc-500/10 text-zinc-500" };
  }
}

function HealthBadge({ health }: { health?: ActivityHealth }) {
  const hb = healthBadge(health);
  if (!hb) return null;
  const tip = health?.detail ? `${health.status}: ${health.detail}` : health?.status;
  return (
    <span className={`shrink-0 rounded px-1 text-[10px] font-medium ${hb.cls}`} title={tip}>
      {hb.text}
    </span>
  );
}

// ── Token use by process ─────────────────────────────────────────────────────
// On-demand panel (Refresh / window-change only — NO live polling) backed by the
// pure-Python /ops/zai/token-usage aggregation + the committed function catalog.

const TOKEN_WINDOWS: { label: string; hours: number }[] = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "12h", hours: 12 },
  { label: "24h", hours: 24 },
  { label: "3d", hours: 72 },
  { label: "6d", hours: 144 },
];

type StageCatalog = {
  label?: string;
  description?: string;
  role?: string;
  tier_current?: string;
  tier_verdict?: string;
  notes?: string;
  stale?: boolean;
} | null;

type TokenStage = {
  caller_fn: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  r429: number;
  catalog?: StageCatalog;
};

type TokenProcess = {
  caller: string;
  requests: number;
  r429: number;
  reject_pct: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
  total_cost_usd?: number;
  retry_input_tokens: number;
  retry_multiplier: number | null;
  dormant_tokens: number;
  stages: TokenStage[];
  catalog?: StageCatalog;
  source?: string;
};

type TokenTotals = {
  requests: number;
  r429: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
  retry_input_tokens: number;
  dormant_tokens: number;
};

type TokenCatalog = {
  version?: number;
  generated_at?: string;
  coverage?: { described_count?: number; undescribed_count?: number; undescribed?: string[] };
};

type TokenSource = {
  source: string;
  label?: string;
  available?: boolean;
  token_events_seen?: number;
  totals: TokenTotals;
  processes: TokenProcess[];
  catalog?: TokenCatalog;
};

type TrendSeries = { key: string; tokens: number[]; runs: number[] };
type TokenTrend = { buckets: string[]; series: TrendSeries[] };

type TokenUsagePayload = {
  available?: boolean;
  generated_at?: number;
  token_events_seen?: number;
  totals?: TokenTotals;
  processes?: TokenProcess[];
  catalog?: TokenCatalog;
  sources?: TokenSource[];
  consolidated?: { totals: TokenTotals; processes: TokenProcess[]; token_events_seen?: number };
  trend?: TokenTrend;
  error?: string;
};

// Source tabs (rendered left→right). "consolidated" = all lanes summed.
const TOKEN_SOURCES: { key: string; label: string }[] = [
  { key: "consolidated", label: "All lanes" },
  { key: "cli-in-process", label: "in-proc SDK" },
  { key: "httpx-zai", label: "httpx" },
  { key: "cli-subprocess", label: "subprocess" },
  { key: "csi-ingester", label: "CSI" },
];

function fmtTok(n?: number | null): string {
  const v = Number(n || 0);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return `${v}`;
}

function shortCaller(c: string): string {
  return c.replace("universal_agent/services/", "").replace("universal_agent/", "");
}

function retryColor(m: number | null): string {
  if (m === null || m >= 3) return "text-red-500";
  if (m >= 1.5) return "text-amber-500";
  return "text-muted-foreground";
}

function verdictBadge(verdict?: string): { text: string; cls: string } | null {
  if (verdict === "review") return { text: "review tier", cls: "bg-amber-500/15 text-amber-500" };
  if (verdict === "appropriate") return { text: "tier ok", cls: "bg-emerald-500/15 text-emerald-500" };
  return null;
}

function TokenPanel() {
  const [data, setData] = useState<TokenUsagePayload | null>(null);
  const [hours, setHours] = useState<number>(24);
  const [source, setSource] = useState<string>("consolidated");
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = useCallback(async (h: number) => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/zai/token-usage?hours=${h}`, { cache: "no-store" });
      setData(await res.json());
    } catch (e) {
      setData({ error: String(e) });
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on mount + whenever the window changes. NO interval — operator-driven Refresh only.
  useEffect(() => {
    load(hours);
  }, [hours, load]);

  // Active view = the selected source (or the consolidated rollup across all lanes).
  // Falls back to the legacy top-level keys when the backend predates sources[].
  const activeSrc =
    source === "consolidated"
      ? null
      : (data?.sources || []).find((s) => s.source === source) || null;
  const t: TokenTotals | undefined =
    source === "consolidated"
      ? data?.consolidated?.totals ?? data?.totals
      : activeSrc?.totals ?? data?.totals;
  const procs: TokenProcess[] =
    source === "consolidated"
      ? data?.consolidated?.processes ?? data?.processes ?? []
      : activeSrc?.processes ?? [];
  const cov = (source === "consolidated" ? data?.catalog : activeSrc?.catalog)?.coverage;
  const noTokens = data && data.available && (data.token_events_seen ?? 0) === 0;
  // Available source keys present in the payload (so we only show tabs with data).
  const presentSources = new Set((data?.sources || []).map((s) => s.source));

  return (
    <section className="rounded-md border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-medium">Token use by process</h2>
          <p className="text-xs text-muted-foreground">
            Where ZAI tokens go, by process &amp; stage. On-demand (no live polling) — pure-Python aggregation of the
            inference events log. Expand a row for the per-stage breakdown &amp; what each stage does.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded border border-border text-xs">
            {TOKEN_WINDOWS.map((w) => (
              <button
                key={w.label}
                onClick={() => setHours(w.hours)}
                className={`px-2 py-1 ${hours === w.hours ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:bg-muted"}`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => load(hours)}
            disabled={loading}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
          >
            {loading ? "…" : "↻ Refresh"}
          </button>
        </div>
      </div>

      {data?.error ? <p className="text-xs text-red-500">token usage unavailable: {data.error}</p> : null}

      {/* Source tabs — which capture lane to view. "All lanes" = consolidated. */}
      {(data?.sources?.length || 0) > 0 ? (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          {TOKEN_SOURCES.filter(
            (s) => s.key === "consolidated" || presentSources.has(s.key),
          ).map((s) => {
            const srcObj = (data?.sources || []).find((x) => x.source === s.key);
            const unavailable = s.key !== "consolidated" && srcObj && srcObj.available === false;
            return (
              <button
                key={s.key}
                onClick={() => setSource(s.key)}
                title={srcObj?.label || (s.key === "consolidated" ? "all lanes summed (cache-inclusive)" : s.key)}
                className={`rounded px-2 py-0.5 text-[11px] ${
                  source === s.key
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted/40 text-muted-foreground hover:bg-muted"
                } ${unavailable ? "opacity-40" : ""}`}
              >
                {s.label}
              </button>
            );
          })}
        </div>
      ) : null}

      {t ? (
        <p className="mb-2 text-xs text-muted-foreground">
          window total: <span className="text-foreground">{t.requests}</span> calls ·{" "}
          <span className="text-foreground">{fmtTok(t.input_tokens)}</span> in /{" "}
          <span className="text-foreground">{fmtTok(t.output_tokens)}</span> out /{" "}
          <span className="font-medium text-foreground">{fmtTok(t.total_tokens)}</span> total{" "}
          <span className="text-[10px]">(incl cache)</span> · cache-read{" "}
          <span className="text-foreground">{fmtTok(t.cache_read_input_tokens)}</span> · {t.r429} × 429 · retry-waste{" "}
          <span className="text-amber-500">{fmtTok(t.retry_input_tokens)}</span> in · dormant {fmtTok(t.dormant_tokens)}
        </p>
      ) : null}

      {/* Per-day token trend (cache-inclusive) — spot trajectories & runaway spikes. */}
      {(data?.trend?.series?.length || 0) > 0 ? (
        <div className="mb-3 rounded border border-border/50 bg-muted/10 p-2">
          <p className="mb-1 text-[11px] text-muted-foreground">
            Per-day token trend (cache-incl) — top principals over {data?.trend?.buckets.length}d
          </p>
          <div className="space-y-0.5">
            {(data?.trend?.series || []).map((s) => {
              const max = Math.max(1, ...s.tokens);
              const sum = s.tokens.reduce((a, b) => a + b, 0);
              return (
                <div key={s.key} className="flex items-center gap-2">
                  <span className="w-32 shrink-0 truncate text-[11px]" title={s.key}>{s.key}</span>
                  <div className="flex h-6 flex-1 items-end gap-0.5">
                    {s.tokens.map((v, i) => (
                      <div
                        key={i}
                        title={`${data?.trend?.buckets[i]}: ${fmtTok(v)} · ${s.runs[i]} runs`}
                        className="min-w-[6px] flex-1 bg-primary/60 hover:bg-primary"
                        style={{ height: `${Math.max(2, Math.round((v / max) * 24))}px` }}
                      />
                    ))}
                  </div>
                  <span className="w-14 shrink-0 text-right text-[10px] text-muted-foreground">{fmtTok(sum)}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {noTokens ? (
        <p className="mb-2 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-500">
          No token data in this window yet — showing request counts only (capture upgrade not live for this range).
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border">
              <th className="py-1 pr-2">Process / stage</th>
              <th className="px-2 py-1 text-right">calls</th>
              <th className="px-2 py-1 text-right">rej%</th>
              <th className="px-2 py-1 text-right">in</th>
              <th className="px-2 py-1 text-right">out</th>
              <th className="px-2 py-1 text-right">total</th>
              <th className="px-2 py-1 text-right" title="retry multiplier — input prompt re-sends per landed call">
                retry×
              </th>
              <th className="py-1 pl-2 text-right">dormant</th>
            </tr>
          </thead>
          <tbody>
            {procs.map((p) => {
              const vb = verdictBadge(p.catalog?.tier_verdict);
              const rowKey = `${p.source || source}:${p.caller}`;
              const isOpen = !!expanded[rowKey];
              return (
                <Fragment key={rowKey}>
                  <tr
                    className="cursor-pointer border-b border-border/50 hover:bg-muted/30"
                    onClick={() => setExpanded((e) => ({ ...e, [rowKey]: !e[rowKey] }))}
                  >
                    <td className="py-1 pr-2">
                      <span className="text-muted-foreground">{isOpen ? "▾" : "▸"}</span>{" "}
                      <span className="font-medium">{p.catalog?.label || shortCaller(p.caller)}</span>
                      {source === "consolidated" && p.source ? (
                        <span className="ml-1 rounded bg-sky-500/15 px-1 text-[10px] text-sky-400">{p.source}</span>
                      ) : null}
                      {vb ? <span className={`ml-1 rounded px-1 text-[10px] ${vb.cls}`}>{vb.text}</span> : null}
                      {p.catalog?.stale ? (
                        <span
                          className="ml-1 rounded bg-zinc-500/15 px-1 text-[10px] text-zinc-400"
                          title="function source changed since it was described — re-describe"
                        >
                          stale
                        </span>
                      ) : null}
                    </td>
                    <td className="px-2 py-1 text-right">{p.requests}</td>
                    <td className={`px-2 py-1 text-right ${pctColor(p.reject_pct)}`}>{p.reject_pct}</td>
                    <td className="px-2 py-1 text-right">{fmtTok(p.input_tokens)}</td>
                    <td className="px-2 py-1 text-right">{fmtTok(p.output_tokens)}</td>
                    <td className="px-2 py-1 text-right font-medium">{fmtTok(p.total_tokens)}</td>
                    <td className={`px-2 py-1 text-right ${retryColor(p.retry_multiplier)}`}>
                      {p.retry_multiplier === null ? "∞" : `${p.retry_multiplier}×`}
                    </td>
                    <td className="py-1 pl-2 text-right">
                      {p.dormant_tokens > 0 ? <span className="text-amber-500">{fmtTok(p.dormant_tokens)}</span> : "—"}
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr className="bg-muted/20">
                      <td colSpan={8} className="px-3 py-2">
                        {p.catalog?.description ? (
                          <p className="mb-2 text-[11px] text-muted-foreground">
                            <span className="text-foreground">{p.catalog.role}</span> · tier {p.catalog.tier_current}
                            {p.catalog.tier_verdict === "review" ? (
                              <span className="text-amber-500"> · review tier</span>
                            ) : null}{" "}
                            — {p.catalog.description}
                            {p.catalog.notes ? <span className="mt-1 block italic">{p.catalog.notes}</span> : null}
                          </p>
                        ) : null}
                        <table className="w-full text-left text-[11px]">
                          <tbody>
                            {p.stages.map((s) => (
                              <tr key={s.caller_fn} className="border-t border-border/30">
                                <td className="py-1 pr-2">
                                  {s.catalog?.label || s.caller_fn.split("::").pop()}
                                  {!s.catalog ? (
                                    <span
                                      className="ml-1 rounded bg-zinc-500/15 px-1 text-[10px] text-zinc-400"
                                      title="no catalog description yet"
                                    >
                                      undescribed
                                    </span>
                                  ) : null}
                                  {s.catalog?.tier_verdict === "review" ? (
                                    <span className="ml-1 rounded bg-amber-500/15 px-1 text-[10px] text-amber-500">review</span>
                                  ) : null}
                                </td>
                                <td className="px-2 py-1 text-right text-muted-foreground">{s.requests} calls</td>
                                <td className="px-2 py-1 text-right">{fmtTok(s.input_tokens)} in</td>
                                <td className="py-1 pl-2 text-right">{fmtTok(s.output_tokens)} out</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {cov && (cov.undescribed_count || 0) > 0 ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {cov.undescribed_count} stage{(cov.undescribed_count || 0) === 1 ? "" : "s"} undescribed (catalog v
          {data?.catalog?.version}). Run the catalog re-population pass to describe them.
        </p>
      ) : null}
    </section>
  );
}

export default function ZaiControlPage() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string>("");
  const [activities, setActivities] = useState<ActivitiesPayload | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/zai/status`, { cache: "no-store" });
      if (!res.ok) {
        setMsg(`status HTTP ${res.status}`);
        return;
      }
      setStatus((await res.json()) as StatusPayload);
      setMsg("");
    } catch (e) {
      setMsg(`status fetch failed: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadActivities = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/zai/activities`, { cache: "no-store" });
      if (!res.ok) return;
      setActivities((await res.json()) as ActivitiesPayload);
    } catch {
      /* fail soft — keep last known activity state */
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    loadActivities();
    // Activities shell out N systemctl calls per poll — poll slower than status.
    const id = setInterval(loadActivities, 10000);
    return () => clearInterval(id);
  }, [loadActivities]);

  // Dispatch an ordered list of systemctl actions against one unit (the
  // declarative off_actions/on_actions policy — e.g. ["stop","mask"] for the
  // watchdog-guarded sweeper).
  const runActivityActions = useCallback(
    async (unit: string, actions: string[]): Promise<boolean> => {
      for (const action of actions) {
        try {
          const res = await fetch(`${API_BASE}/api/v1/ops/zai/activity-control`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ unit, action }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            setMsg(`${action} ${unit} failed: ${data.detail || res.status}`);
            return false;
          }
        } catch (e) {
          setMsg(`${action} ${unit} error: ${String(e)}`);
          return false;
        }
      }
      return true;
    },
    [],
  );

  const activityControl = useCallback(
    async (unit: string, actions: string[]) => {
      setBusy(true);
      setMsg("");
      const ok = await runActivityActions(unit, actions);
      if (ok) setMsg(`applied ${actions.join("+")} → ${unit}`);
      await loadActivities();
      setBusy(false);
    },
    [runActivityActions, loadActivities],
  );

  const stopAllProactive = useCallback(async () => {
    const items = activities?.activities ?? [];
    if (!window.confirm("Stop ALL proactive activities (timers + services)? Core services (gateway/api/webui) stay up.")) return;
    setBusy(true);
    setMsg("Stopping all proactive activities…");
    let stopped = 0;
    for (const a of items) {
      if (!a.is_active) continue; // already down (masked-or-not needs no further stop)
      const ok = await runActivityActions(a.unit, a.off_actions);
      if (ok) stopped += 1;
    }
    setMsg(`stop-all dispatched (${stopped} unit${stopped === 1 ? "" : "s"})`);
    await loadActivities();
    setBusy(false);
  }, [activities, runActivityActions, loadActivities]);

  const control = useCallback(
    async (body: Record<string, unknown>) => {
      setBusy(true);
      setMsg("");
      try {
        const res = await fetch(`${API_BASE}/api/v1/ops/zai/control`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setMsg(`control failed: ${data.detail || res.status}`);
        } else {
          setMsg(`applied: ${body.action}`);
          await load();
        }
      } catch (e) {
        setMsg(`control error: ${String(e)}`);
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const ctrl = status?.control;
  const snap = status?.snapshot;
  const windows = status?.events?.windows;
  const level = ctrl?.intervention_level ?? 0;
  const paused = !!ctrl?.global_pause_active;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">ZAI Control</h1>
          <p className="text-sm text-muted-foreground">
            Emergency inference levers + live 429 / FUP monitor. Reads/writes the live control file;
            changes take effect within ~2s. Controls fail open — a control-plane fault degrades to
            normal operation, never a block.
          </p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          {loading ? "loading…" : `updated ${fmtAgo(status?.generated_at)}`}
          {msg ? <div className="text-amber-500">{msg}</div> : null}
        </div>
      </div>

      {paused ? (
        <div className="rounded-md border border-red-500/50 bg-red-500/10 p-4">
          <div className="font-semibold text-red-400">⛔ GLOBAL ZAI PAUSE ACTIVE</div>
          <div className="text-sm text-muted-foreground">
            All api.z.ai requests are aborted at the httpx hook (every caller). {fmtCountdown(ctrl?.global_pause?.until)}.
            {ctrl?.global_pause?.reason ? ` Reason: ${ctrl.global_pause.reason}.` : ""}
          </div>
        </div>
      ) : null}

      {/* Lever ladder */}
      <section className="rounded-md border border-border bg-card p-4">
        <h2 className="mb-3 text-lg font-medium">Intervention level ladder</h2>
        <div className="flex flex-wrap gap-2">
          {[0, 1, 2, 3, 4].map((lv) => {
            const active = lv === level;
            const isNuke = lv === 4;
            return (
              <button
                key={lv}
                disabled={busy}
                onClick={() => {
                  if (isNuke && !window.confirm("L4 GLOBAL PAUSE aborts ALL ZAI inference. Proceed?")) return;
                  void control({ action: "set_level", level: lv });
                }}
                className={`rounded-md border px-3 py-2 text-left text-sm transition ${
                  active
                    ? "border-primary bg-primary/15 ring-1 ring-primary"
                    : isNuke
                      ? "border-red-500/40 hover:bg-red-500/10"
                      : "border-border hover:bg-muted"
                }`}
              >
                <div className="font-medium">{LEVEL_LABELS[lv].name}</div>
                <div className="text-xs text-muted-foreground">{LEVEL_LABELS[lv].desc}</div>
              </button>
            );
          })}
        </div>
        <div className="mt-3 flex gap-2">
          <button
            disabled={busy}
            onClick={() => void control({ action: "clear" })}
            className="rounded-md border border-emerald-500/40 px-3 py-1.5 text-sm hover:bg-emerald-500/10"
          >
            Reset to normal (L0)
          </button>
          <button
            disabled={busy}
            onClick={() => void control({ action: "set_global_pause", active: !paused, ttl_seconds: 1800 })}
            className={`rounded-md border px-3 py-1.5 text-sm ${
              paused ? "border-emerald-500/40 hover:bg-emerald-500/10" : "border-red-500/40 hover:bg-red-500/10"
            }`}
          >
            {paused ? "Lift global pause" : "Global pause (30m TTL)"}
          </button>
        </div>
      </section>

      {/* Per-tier cards */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {TIERS.map((tier) => {
          const cap = snap?.tier_caps?.[tier];
          const override = ctrl?.tier_overrides?.[tier];
          const tierPaused = !!ctrl?.tier_pause?.[tier];
          return (
            <div key={tier} className="rounded-md border border-border bg-card p-3">
              <div className="flex items-center justify-between">
                <div className="font-medium capitalize">{tier}</div>
                {tierPaused ? <span className="text-xs text-red-400">PAUSED</span> : null}
              </div>
              <div className="mt-1 text-2xl font-semibold">
                cap {cap ?? "—"}
                {override?.cap != null ? <span className="ml-1 text-xs text-amber-500">(override)</span> : null}
              </div>
              {/* Per-window 429 / FUP for THIS tier: 1m · 10m · 60m */}
              <div className="mt-1 space-y-0.5 text-xs">
                {(["1m", "10m", "60m"] as const).map((w) => {
                  const tb = windows?.[w]?.tiers?.[tier];
                  const p = tb?.pct ?? 0;
                  const fup = tb?.fup ?? 0;
                  const t1313 = tb?.fup_texted ?? 0;
                  return (
                    <div key={w} className="flex items-baseline justify-between gap-2">
                      <span className="w-8 shrink-0 text-muted-foreground">{w}</span>
                      <span className={`tabular-nums ${pctColor(p)}`}>{p}%</span>
                      <span className="ml-auto tabular-nums text-muted-foreground">
                        {tb?.r429 ?? 0}/{tb?.total ?? 0} · FUP {fup}
                        {t1313 > 0 ? ` · 1313 ${t1313}` : ""}
                      </span>
                    </div>
                  );
                })}
              </div>
              <div className="mt-2 flex items-center gap-1">
                <button
                  disabled={busy}
                  onClick={() =>
                    void control({
                      action: "set_tier_caps",
                      overrides: { [tier]: { cap: Math.max(1, (cap ?? 1) - 1) } },
                    })
                  }
                  className="rounded border border-border px-2 py-0.5 text-sm hover:bg-muted"
                >
                  −
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void control({
                      action: "set_tier_caps",
                      overrides: { [tier]: { cap: (cap ?? 1) + 1, max: (cap ?? 1) + 1 } },
                    })
                  }
                  className="rounded border border-border px-2 py-0.5 text-sm hover:bg-muted"
                >
                  +
                </button>
                <button
                  disabled={busy}
                  onClick={() => void control({ action: "set_tier_pause", tiers: { [tier]: !tierPaused } })}
                  className={`ml-auto rounded border px-2 py-0.5 text-xs ${
                    tierPaused ? "border-emerald-500/40 hover:bg-emerald-500/10" : "border-red-500/40 hover:bg-red-500/10"
                  }`}
                >
                  {tierPaused ? "resume" : "pause"}
                </button>
              </div>
            </div>
          );
        })}
      </section>

      {/* 429 / FUP windows */}
      <section className="rounded-md border border-border bg-card p-4">
        <h2 className="mb-3 text-lg font-medium">Rejection rate (rolling windows)</h2>
        {status?.events?.available ? (
          <div className="grid grid-cols-3 gap-3 text-center">
            {(["1m", "10m", "60m"] as const).map((w) => {
              const d = windows?.[w];
              const pct = d?.pct ?? 0;
              return (
                <div key={w} className="rounded border border-border p-2">
                  <div className="text-xs text-muted-foreground">{w}</div>
                  <div className={`text-xl font-semibold ${pctColor(pct)}`}>{pct}%</div>
                  <div className="text-xs text-muted-foreground">
                    {d?.r429 ?? 0}/{d?.total ?? 0} · FUP {d?.fup ?? 0} · 1313 {d?.fup_texted ?? 0}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">No events file yet (zero ZAI traffic, or freshly restored).</div>
        )}
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <Stat label="exhausted" value={snap?.total_429s_exhausted} warn />
          <Stat label="succeeded-after-retry" value={snap?.total_succeeded_after_retry} />
          <Stat label="FUP events" value={snap?.total_fup_events} warn />
          <Stat label="cross-loop" value={snap?.cross_loop_conflicts} warn />
        </div>
        {status?.events?.callers_429_60m?.length ? (
          <div className="mt-3">
            <div className="mb-1 text-xs text-muted-foreground">Top 429 callers (60m)</div>
            <div className="flex flex-wrap gap-2 text-xs">
              {status.events.callers_429_60m.map((c) => (
                <span key={c.caller} className="rounded bg-muted px-2 py-0.5">
                  {c.caller} ×{c.count}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {/* Proactive activity controls (per-process on/off) */}
      <section className="rounded-md border border-border bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-medium">Proactive activity controls</h2>
            <p className="text-sm text-muted-foreground">
              Per-process on/off for the ZAI-consuming proactive timers &amp; services. Core units
              (gateway/api/webui) are excluded by allowlist. Toggling is live but not deploy-durable —
              a deploy re-arms timers; use the L4 pause or Infisical flags for durable suppression.
            </p>
          </div>
          <button
            disabled={busy}
            onClick={() => void stopAllProactive()}
            className="shrink-0 rounded-md border border-red-500/40 px-3 py-1.5 text-sm hover:bg-red-500/10"
          >
            Stop all proactive
          </button>
        </div>

        {activities?.error ? (
          <div className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-500">
            activities unavailable: {activities.error}
          </div>
        ) : null}

        {(["timers", "services"] as const).map((group) => {
          const rows = (activities?.activities ?? []).filter((a) => a.group === group);
          if (!rows.length) return null;
          return (
            <div key={group} className="mb-4">
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {group === "timers" ? "Timers (scheduled proactive work)" : "Continuous services"}
              </div>
              <div className="grid grid-cols-1 overflow-hidden rounded border border-border md:grid-cols-2 md:gap-x-6">
                {rows.map((a) => (
                  <ActivityRow key={a.unit} a={a} busy={busy} onAction={activityControl} />
                ))}
              </div>
            </div>
          );
        })}

        {activities?.inprocess?.length ? (
          <div className="mb-1">
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              In-process loops (read-only — flip via Infisical + gateway restart)
            </div>
            <div className="divide-y divide-border rounded border border-border">
              {activities.inprocess.map((l) => (
                <div key={l.key} className="flex items-center justify-between gap-3 p-2 text-sm">
                  <div className="flex items-center gap-2">
                    <span className={`inline-block h-2 w-2 rounded-full ${l.enabled ? "bg-emerald-500" : "bg-zinc-500"}`} />
                    <span>{l.label}</span>
                    <code className="rounded bg-muted px-1 text-xs">{l.env_var}</code>
                    <HealthBadge health={l.health} />
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {l.enabled ? "enabled" : "disabled"} · {l.note}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {/* Token use by process (on-demand; pure-Python aggregation + catalog) */}
      <TokenPanel />

      {/* L5 — out of band */}
      <section className="rounded-md border border-border bg-card p-4">
        <h2 className="mb-2 text-lg font-medium">L5 — Full dark (out of band)</h2>
        <p className="text-sm text-muted-foreground">
          L4 (global pause) already gives zero ZAI with services up and this dashboard alive — prefer
          it. True full-dark (stopping systemd services) is a manual op (the dashboard would stop with
          the gateway). To stop everything from a shell:
        </p>
        <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 text-xs">
{`ssh ua@uaonvps 'sudo systemctl stop "universal-agent-*.timer" \\
  universal-agent-gateway universal-agent-api \\
  universal-agent-mission-control-sweeper "universal-agent-vp-worker@*"'`}
        </pre>
        <p className="mt-1 text-xs text-muted-foreground">
          snapshot writer: {snap?.process_name ?? "—"} · written {fmtAgo(snap?.snapshot_written_at)}
        </p>
      </section>
    </div>
  );
}

function ActivityRow({
  a,
  busy,
  onAction,
}: {
  a: ActivityItem;
  busy: boolean;
  onAction: (unit: string, actions: string[]) => void;
}) {
  const toggle = primaryToggle(a);
  const dotColor = a.is_masked
    ? "bg-purple-500"
    : a.active_state === "failed"
      ? "bg-red-500"
      : a.is_active
        ? "bg-emerald-500"
        : "bg-zinc-500";
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border p-2 text-sm">
      <div className="flex min-w-0 items-center gap-2">
        <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${dotColor}`} title={a.active_state} />
        <span className="truncate">{a.label}</span>
        {a.heavy_zai ? (
          <span className="shrink-0 rounded bg-amber-500/15 px-1 text-[10px] font-medium text-amber-500">HEAVY</span>
        ) : null}
        {a.watchdog_guarded ? (
          <span className="shrink-0 rounded bg-sky-500/15 px-1 text-[10px] text-sky-400" title="service-watchdog will restart this if only stopped — off uses mask">
            guarded
          </span>
        ) : null}
        {a.is_masked ? (
          <span className="shrink-0 rounded bg-purple-500/15 px-1 text-[10px] text-purple-400">masked</span>
        ) : null}
        <HealthBadge health={a.health} />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="hidden text-xs text-muted-foreground sm:inline">
          {a.is_active ? a.sub_state : a.active_state}
        </span>
        {a.is_active && !a.watchdog_guarded ? (
          <button
            disabled={busy}
            onClick={() => onAction(a.unit, ["restart"])}
            className="rounded border border-border px-2 py-0.5 text-xs hover:bg-muted"
          >
            restart
          </button>
        ) : null}
        <button
          disabled={busy}
          onClick={() => onAction(a.unit, toggle.actions)}
          className={`rounded border px-2 py-0.5 text-xs ${
            toggle.kind === "off"
              ? "border-red-500/40 hover:bg-red-500/10"
              : "border-emerald-500/40 hover:bg-emerald-500/10"
          }`}
        >
          {toggle.label}
        </button>
      </div>
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value?: number | null; warn?: boolean }) {
  const v = value ?? 0;
  return (
    <div className="rounded border border-border p-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold ${warn && v > 0 ? "text-amber-500" : ""}`}>{v}</div>
    </div>
  );
}
