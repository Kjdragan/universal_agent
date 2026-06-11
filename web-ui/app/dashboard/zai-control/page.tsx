"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = "/api/dashboard/gateway";
const TIERS = ["opus", "sonnet", "mid", "haiku"] as const;
type Tier = (typeof TIERS)[number];

type WindowStat = {
  total: number;
  r429: number;
  fup: number;
  fup_texted: number;
  pct: number;
  tiers: Record<string, { total: number; r429: number; pct?: number }>;
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

export default function ZaiControlPage() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string>("");

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

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

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
          const w10 = windows?.["10m"]?.tiers?.[tier];
          const pct = w10?.pct ?? 0;
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
              <div className={`text-sm ${pctColor(pct)}`}>
                {pct}% 429 (10m){w10 ? ` · ${w10.r429}/${w10.total}` : ""}
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

function Stat({ label, value, warn }: { label: string; value?: number | null; warn?: boolean }) {
  const v = value ?? 0;
  return (
    <div className="rounded border border-border p-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold ${warn && v > 0 ? "text-amber-500" : ""}`}>{v}</div>
    </div>
  );
}
