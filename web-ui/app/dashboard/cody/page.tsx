"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, RefreshCw, RotateCcw, ChevronDown, ChevronRight, AlertCircle } from "lucide-react";

const API_BASE = "/api/dashboard/gateway";

// ── Types ──────────────────────────────────────────────────────────────────

type CodyMode = "anthropic" | "zai";

type VpModeState = {
  vp_id: string;
  display_name: string;
  mode: CodyMode;
  // db_setting_vp = per-VP operator pin; db_setting_global = inherited from
  // the global setting; env_var = env override; profile_default = the
  // agent's own default (CODIE→anthropic, ATLAS→zai).
  source: "db_setting_vp" | "db_setting_global" | "env_var" | "profile_default" | string;
  profile_default: CodyMode | string;
  updated_at: string;
  updated_by: string;
};

type ModeSettingResponse = {
  mode: CodyMode;
  source: "db_setting" | "env_var" | "hardcoded_default" | string;
  updated_at: string;
  updated_by: string;
  available_modes: string[];
  vps?: VpModeState[];
};

type ModelBreakdownRow = {
  model: string;
  missions: number;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
};

type TokenTrackingResponse = {
  reset_at: string;
  reset_by: string;
  days_in_window: number;
  mission_count: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_cost_usd: number;
  model_breakdown: ModelBreakdownRow[];
  cody_mode_filter: string | null;
};

// ── Formatting helpers ─────────────────────────────────────────────────────

function formatCost(usd: number): string {
  if (!Number.isFinite(usd)) return "$0.00";
  if (usd < 0.01 && usd > 0) return `<$0.01`;
  if (usd >= 100) return `$${usd.toFixed(0)}`;
  return `$${usd.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}K`;
  if (n >= 1000) return `${(n / 1000).toFixed(2)}K`;
  return String(n);
}

function formatDays(days: number): string {
  if (!Number.isFinite(days) || days < 0) return "—";
  if (days < 1 / 24) {
    const mins = Math.max(0, Math.round(days * 24 * 60));
    return `${mins} min`;
  }
  if (days < 1) {
    const hrs = Math.max(0, Math.round(days * 24));
    return `${hrs} hr`;
  }
  return `${days.toFixed(1)} days`;
}

function formatResetAt(iso: string): string {
  if (!iso) return "—";
  // Treat epoch zero / pre-2000 as "never reset"
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts) || ts < Date.parse("2000-01-01")) return "Never reset";
  const d = new Date(ts);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function describeVpSource(source: string, updatedBy: string, updatedAt: string): string {
  switch (source) {
    case "db_setting_vp": {
      const who = updatedBy ? ` by ${updatedBy}` : "";
      const daysAgo = daysSince(updatedAt);
      const when =
        daysAgo !== null && Number.isFinite(daysAgo)
          ? daysAgo < 1
            ? " (today)"
            : ` (${daysAgo.toFixed(1)} days ago)`
          : "";
      return `Pinned${who}${when}`;
    }
    case "db_setting_global":
      return "From global default";
    case "env_var":
      return "Environment override";
    case "profile_default":
      return "Agent default";
    default:
      return source || "—";
  }
}

function daysSince(iso: string): number | null {
  if (!iso) return null;
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts) || ts < Date.parse("2000-01-01")) return null;
  return (Date.now() - ts) / (1000 * 60 * 60 * 24);
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function CodyDashboardPage() {
  const [modeState, setModeState] = useState<ModeSettingResponse | null>(null);
  const [tokens, setTokens] = useState<TokenTrackingResponse | null>(null);
  const [zaiTokens, setZaiTokens] = useState<TokenTrackingResponse | null>(null);
  const [showZai, setShowZai] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatingVp, setUpdatingVp] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [showModelBreakdown, setShowModelBreakdown] = useState(false);

  const loadMode = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/cody/mode-setting`, { cache: "no-store" });
      if (!res.ok) throw new Error(`mode-setting failed (${res.status})`);
      const data = (await res.json()) as ModeSettingResponse;
      setModeState(data);
      return data;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, []);

  const loadTokens = useCallback(async (modeFilter: CodyMode | "all") => {
    const res = await fetch(
      `${API_BASE}/api/v1/cody/anthropic-token-tracking?mode=${encodeURIComponent(modeFilter)}`,
      { cache: "no-store" },
    );
    if (!res.ok) throw new Error(`token-tracking failed (${res.status})`);
    return (await res.json()) as TokenTrackingResponse;
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await loadMode();
      const [anth, zai] = await Promise.all([loadTokens("anthropic"), loadTokens("zai")]);
      setTokens(anth);
      setZaiTokens(zai);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [loadMode, loadTokens]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  // Pin a single VP to a mode (or "clear" to revert it to its agent default).
  // POSTs with vp_id; the response carries the refreshed `vps` array.
  const handleSetVpMode = useCallback(
    async (vpId: string, nextMode: CodyMode | "clear") => {
      if (updatingVp) return;
      setUpdatingVp(vpId);
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/api/v1/cody/mode-setting`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: nextMode, vp_id: vpId, updated_by: "operator" }),
        });
        if (!res.ok) {
          const detail = await res.text().catch(() => "");
          throw new Error(`Mode update failed (${res.status}) ${detail}`);
        }
        const data = (await res.json()) as ModeSettingResponse;
        setModeState(data);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setUpdatingVp(null);
      }
    },
    [updatingVp],
  );

  const handleResetTokens = useCallback(async () => {
    if (resetting) return;
    const confirmed = window.confirm(
      "Reset Anthropic token tracking? This zeros all counters and sets the tracking-since date to now. The Cody mission history itself is preserved.",
    );
    if (!confirmed) return;
    setResetting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/cody/anthropic-token-tracking/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reset_by: "operator" }),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        throw new Error(`Reset failed (${res.status}) ${detail}`);
      }
      // Reset endpoint returns fresh anthropic-mode summary
      const data = (await res.json()) as TokenTrackingResponse;
      setTokens(data);
      // ZAI window doesn't share the reset, but re-load to be consistent
      try {
        const zai = await loadTokens("zai");
        setZaiTokens(zai);
      } catch {
        /* non-fatal */
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setResetting(false);
    }
  }, [resetting, loadTokens]);

  const vps = useMemo<VpModeState[]>(() => modeState?.vps ?? [], [modeState]);

  /* ── Render ─────────────────────────────────────────────────────────── */

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-foreground">
            <Bot className="h-5 w-5 text-kcd-cyan" />
            Cody Execution Mode &amp; Token Usage
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Operator toggle for Cody&apos;s model backend and cumulative Anthropic token tracking.
          </p>
        </div>
        <button
          type="button"
          onClick={refreshAll}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-border/40 bg-card/15 px-2.5 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground transition hover:bg-card/30 disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
          Reload
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-kcd-red/30 bg-kcd-red/5 px-4 py-3 text-xs text-kcd-red">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="font-mono">{error}</span>
        </div>
      )}

      {/* ── Per-VP execution mode ────────────────────────────────────── */}
      <section className="rounded-xl border border-border/40 bg-card/10 p-5">
        <div className="mb-4 flex items-baseline justify-between gap-3">
          <h2 className="text-sm font-semibold text-foreground">Execution Mode (per agent)</h2>
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {modeState ? `${vps.length} agent${vps.length === 1 ? "" : "s"}` : "loading…"}
          </span>
        </div>

        <div className="space-y-3">
          {vps.length === 0 && (
            <p className="text-[11px] italic text-muted-foreground">
              {loading ? "Loading agents…" : "No VP agents enabled."}
            </p>
          )}
          {vps.map((vp) => {
            const busy = updatingVp === vp.vp_id;
            const pinned = vp.source === "db_setting_vp";
            return (
              <div
                key={vp.vp_id}
                className="rounded-xl border border-border/30 bg-card/10 p-3"
              >
                <div className="mb-2 flex items-baseline justify-between gap-3">
                  <div className="flex items-baseline gap-2">
                    <span className="text-xs font-semibold text-foreground">{vp.display_name}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{vp.vp_id}</span>
                  </div>
                  <span className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
                    {describeVpSource(vp.source, vp.updated_by, vp.updated_at)}
                    {pinned && (
                      <button
                        type="button"
                        onClick={() => handleSetVpMode(vp.vp_id, "clear")}
                        disabled={busy}
                        className="rounded border border-border/40 px-1.5 py-0.5 text-[9px] normal-case text-muted-foreground transition hover:bg-card/30 hover:text-foreground disabled:opacity-50"
                        title={`Revert to agent default (${vp.profile_default})`}
                      >
                        clear pin
                      </button>
                    )}
                  </span>
                </div>
                <div className="flex gap-1 rounded-xl border border-border/40 bg-card/10 p-1">
                  {(["anthropic", "zai"] as const).map((m) => {
                    const active = vp.mode === m;
                    const label = m === "anthropic" ? "Anthropic (Max plan)" : "ZAI (GLM proxy)";
                    return (
                      <button
                        key={m}
                        type="button"
                        onClick={() => handleSetVpMode(vp.vp_id, m)}
                        disabled={busy}
                        className={[
                          "flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium uppercase tracking-wider transition disabled:cursor-not-allowed disabled:opacity-50",
                          active
                            ? "bg-kcd-cyan/10 text-kcd-cyan ring-1 ring-kcd-cyan/30"
                            : "text-muted-foreground hover:bg-card/25 hover:text-foreground",
                        ].join(" ")}
                      >
                        <span
                          className={`h-2 w-2 rounded-full ${
                            active ? "bg-kcd-cyan shadow-[0_0_6px] shadow-kcd-cyan/50" : "bg-muted"
                          }`}
                        />
                        {label}
                        {active && busy && <RefreshCw className="h-3 w-3 animate-spin" />}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          Inference is bound to the <span className="text-foreground/80">agent</span>: CODIE (coder)
          defaults to <span className="font-mono text-foreground/80">anthropic</span> (Max plan, for
          production-quality coding and Anthropic-specific features); ATLAS (generalist research /
          intel) defaults to <span className="font-mono text-foreground/80">zai</span> (GLM proxy) to
          keep autonomous work off the scarce Max credits. Pinning here overrides an agent&apos;s
          default; <span className="text-foreground/80">clear pin</span> reverts it. Changes apply to
          the next mission that agent starts; in-flight missions finish on their original backend.
        </p>
      </section>

      {/* ── Anthropic token usage tile ──────────────────────────────── */}
      <section className="rounded-xl border border-kcd-cyan/30 bg-card/10 p-5">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-foreground">Anthropic Token Usage</h2>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Tracking since{" "}
              <span className="font-mono text-foreground/80">
                {tokens ? formatResetAt(tokens.reset_at) : "—"}
              </span>{" "}
              · window:{" "}
              <span className="font-mono text-foreground/80">
                {tokens ? formatDays(tokens.days_in_window) : "—"}
              </span>
              {tokens?.reset_by ? (
                <>
                  {" "}· reset by{" "}
                  <span className="font-mono text-foreground/80">{tokens.reset_by}</span>
                </>
              ) : null}
            </p>
          </div>
          <button
            type="button"
            onClick={handleResetTokens}
            disabled={resetting || !tokens}
            className="flex items-center gap-1.5 rounded-lg border border-kcd-amber/40 bg-kcd-amber/10 px-3 py-1.5 text-[10px] uppercase tracking-wider text-kcd-amber transition hover:bg-kcd-amber/20 disabled:opacity-50"
          >
            <RotateCcw className={`h-3 w-3 ${resetting ? "animate-spin" : ""}`} />
            {resetting ? "Resetting…" : "Refresh / Reset Counters"}
          </button>
        </div>

        {/* Headline cost */}
        <div className="mb-4 rounded-xl border border-border/30 bg-card/15 px-5 py-4">
          <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Total cost
          </div>
          <div className="mt-1 font-mono text-3xl font-semibold text-kcd-cyan">
            {tokens ? formatCost(tokens.total_cost_usd) : "—"}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            across{" "}
            <span className="font-mono text-foreground/80">
              {tokens?.mission_count ?? "—"}
            </span>{" "}
            mission{tokens?.mission_count === 1 ? "" : "s"}
          </div>
        </div>

        {/* Token grid */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { label: "Input tokens", value: tokens?.input_tokens ?? 0 },
            { label: "Output tokens", value: tokens?.output_tokens ?? 0 },
            { label: "Cache create", value: tokens?.cache_creation_input_tokens ?? 0 },
            { label: "Cache read", value: tokens?.cache_read_input_tokens ?? 0 },
          ].map((item) => (
            <div
              key={item.label}
              className="rounded-lg border border-border/25 bg-card/10 px-3 py-2.5"
            >
              <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                {item.label}
              </div>
              <div className="mt-1 font-mono text-sm font-medium text-foreground">
                {formatTokens(item.value)}
              </div>
            </div>
          ))}
        </div>

        {/* Model breakdown (collapsed by default) */}
        <button
          type="button"
          onClick={() => setShowModelBreakdown((v) => !v)}
          className="mt-4 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground transition hover:text-foreground"
        >
          {showModelBreakdown ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          Model breakdown
          {tokens?.model_breakdown?.length ? (
            <span className="font-mono text-muted-foreground">
              ({tokens.model_breakdown.length})
            </span>
          ) : null}
        </button>
        {showModelBreakdown && (
          <div className="mt-3 overflow-x-auto rounded-lg border border-border/25 bg-card/5">
            {tokens && tokens.model_breakdown.length > 0 ? (
              <table className="w-full text-xs">
                <thead className="border-b border-border/25 bg-card/10">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <th className="px-3 py-2 font-medium">Model</th>
                    <th className="px-3 py-2 font-medium text-right">Missions</th>
                    <th className="px-3 py-2 font-medium text-right">Input</th>
                    <th className="px-3 py-2 font-medium text-right">Output</th>
                    <th className="px-3 py-2 font-medium text-right">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.model_breakdown.map((row) => (
                    <tr
                      key={row.model}
                      className="border-b border-border/15 last:border-b-0 hover:bg-card/5"
                    >
                      <td className="px-3 py-2 font-mono text-foreground/90">{row.model}</td>
                      <td className="px-3 py-2 text-right font-mono text-muted-foreground">
                        {row.missions}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-muted-foreground">
                        {formatTokens(row.input_tokens)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-muted-foreground">
                        {formatTokens(row.output_tokens)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-kcd-cyan">
                        {formatCost(row.total_cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="px-3 py-4 text-center text-[11px] italic text-muted-foreground">
                No model usage in this window yet.
              </p>
            )}
          </div>
        )}
      </section>

      {/* ── Optional ZAI secondary view ─────────────────────────────── */}
      <section className="rounded-xl border border-border/40 bg-card/5 p-5">
        <button
          type="button"
          onClick={() => setShowZai((v) => !v)}
          className="flex w-full items-center justify-between text-left"
        >
          <span className="flex items-center gap-2 text-sm font-semibold text-foreground/80">
            {showZai ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
            ZAI Mode Usage
            <span className="text-[10px] font-normal uppercase tracking-wider text-muted-foreground">
              (secondary view)
            </span>
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            {zaiTokens ? `${zaiTokens.mission_count} missions` : "—"}
          </span>
        </button>
        {showZai && (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: "Missions", value: String(zaiTokens?.mission_count ?? 0) },
              { label: "Input tokens", value: formatTokens(zaiTokens?.input_tokens ?? 0) },
              { label: "Output tokens", value: formatTokens(zaiTokens?.output_tokens ?? 0) },
              { label: "Tracking since", value: zaiTokens ? formatResetAt(zaiTokens.reset_at) : "—" },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-lg border border-border/20 bg-card/10 px-3 py-2.5"
              >
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  {item.label}
                </div>
                <div className="mt-1 font-mono text-sm font-medium text-foreground/85">
                  {item.value}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
