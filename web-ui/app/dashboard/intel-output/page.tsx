"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Sparkles,
  RefreshCw,
  ExternalLink,
  AlertCircle,
  FileText,
  Bot,
  Clock,
  Mail,
  Send,
  CheckCircle2,
  Inbox,
  Tag,
  Link2,
} from "lucide-react";

const API_BASE = "/api/dashboard/gateway";

// Intel briefs live in the proactive_artifacts table. We fetch the flat
// list and filter to intel_brief client-side — the endpoint has no
// artifact_type param, and a read-only dashboard should not pay for a
// backend change. sync_signals=false avoids the per-call write-lock.
const ARTIFACT_TYPE_INTEL_BRIEF = "intel_brief";

type Brief = {
  artifact_id: string;
  artifact_type: string;
  title: string;
  summary?: string;
  status?: string;
  delivery_state?: string;
  priority?: number;
  source_kind?: string;
  source_url?: string;
  topic_tags?: string[];
  created_at?: string;
  updated_at?: string;
  delivered_at?: string;
};

// Cody demos are a SEPARATE source: filesystem manifests under
// /opt/ua_demos, surfaced by the claude-code-intel/demos endpoint. That
// endpoint already screens demos by manifest.json existence — the
// project's existing demo-screening methodology — so we reuse it rather
// than invent a new "demoable" check. The fuller demo surface lives on
// the Claude Code Intel tab; here we show the screened net output.
type Demo = {
  demo_id: string;
  feature?: string;
  endpoint_hit?: string;
  marker_verified?: boolean;
  timestamp?: string;
  entity_slug?: string;
  linked_from_entity?: boolean;
};

type ViewMode = "all" | "briefs" | "demos";

const DELIVERY_LABELS: Record<string, string> = {
  emailed: "Emailed",
  digest_queued: "Queued for digest",
  reviewed: "Reviewed",
  not_surfaced: "Not surfaced",
  email_failed: "Email failed",
};

function deliveryBadgeClasses(state: string | undefined): string {
  switch ((state || "").toLowerCase()) {
    case "emailed":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
    case "reviewed":
      return "border-cyan-500/30 bg-cyan-500/10 text-cyan-200";
    case "digest_queued":
      return "border-amber-500/30 bg-amber-500/10 text-amber-300";
    case "email_failed":
      return "border-red-500/30 bg-red-500/10 text-red-300";
    default:
      return "border-white/10 bg-white/5 text-slate-400";
  }
}

function deliveryIcon(state: string | undefined) {
  switch ((state || "").toLowerCase()) {
    case "emailed":
      return <Mail className="h-3 w-3" />;
    case "digest_queued":
      return <Send className="h-3 w-3" />;
    case "reviewed":
      return <CheckCircle2 className="h-3 w-3" />;
    default:
      return <Inbox className="h-3 w-3" />;
  }
}

function compactDateTime(iso: string | undefined | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function briefTimestamp(brief: Brief): string {
  return brief.delivered_at || brief.updated_at || brief.created_at || "";
}

export default function IntelOutputPage() {
  const [briefs, setBriefs] = useState<Brief[]>([]);
  const [demos, setDemos] = useState<Demo[]>([]);
  const [view, setView] = useState<ViewMode>("all");
  const [deliveryFilter, setDeliveryFilter] = useState<string>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [briefsRes, demosRes] = await Promise.all([
        fetch(
          `${API_BASE}/api/v1/dashboard/proactive-artifacts?limit=200&sync_signals=false`,
          { cache: "no-store" },
        ),
        fetch(`${API_BASE}/api/v1/dashboard/claude-code-intel/demos`, {
          cache: "no-store",
        }),
      ]);
      if (!briefsRes.ok) throw new Error(`Briefs load failed (${briefsRes.status})`);
      const briefsData = await briefsRes.json();
      const allArtifacts: Brief[] = Array.isArray(briefsData.artifacts)
        ? briefsData.artifacts
        : [];
      const intelBriefs = allArtifacts
        .filter((a) => a.artifact_type === ARTIFACT_TYPE_INTEL_BRIEF)
        .sort((a, b) => briefTimestamp(b).localeCompare(briefTimestamp(a)));
      setBriefs(intelBriefs);

      // Demos are best-effort: a missing /opt/ua_demos root returns an
      // empty list, and a demos-endpoint failure should not blank the briefs.
      if (demosRes.ok) {
        const demosData = await demosRes.json();
        setDemos(Array.isArray(demosData.demos) ? demosData.demos : []);
      } else {
        setDemos([]);
      }
    } catch (err) {
      setError((err as Error).message || "Failed to load intel output.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const deliveryStates = useMemo(() => {
    const values = new Set<string>();
    briefs.forEach((b) => {
      if (b.delivery_state) values.add(b.delivery_state);
    });
    return Array.from(values).sort();
  }, [briefs]);

  const visibleBriefs = useMemo(
    () =>
      deliveryFilter === "all"
        ? briefs
        : briefs.filter((b) => (b.delivery_state || "") === deliveryFilter),
    [briefs, deliveryFilter],
  );

  const showBriefs = view === "all" || view === "briefs";
  const showDemos = view === "all" || view === "demos";
  const nothingToShow =
    !loading &&
    (!showBriefs || visibleBriefs.length === 0) &&
    (!showDemos || demos.length === 0);

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Sparkles className="w-6 h-6 text-cyan-400" />
            Intel Output
          </h1>
          <p className="text-muted-foreground mt-1">
            Net shipped output of the intelligence lanes — published briefs and
            verified Cody demos, in one place.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-card/60 hover:bg-card border border-border rounded-lg transition-all disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <p className="text-sm">{error}</p>
        </div>
      )}

      {/* View toggle */}
      <div className="grid grid-cols-3 gap-2 max-w-md">
        {(
          [
            ["all", "All", briefs.length + demos.length],
            ["briefs", "Briefs", briefs.length],
            ["demos", "Demos", demos.length],
          ] as const
        ).map(([key, label, count]) => (
          <button
            key={key}
            onClick={() => setView(key)}
            className={`rounded-lg border px-3 py-2 text-left transition-all ${
              view === key
                ? "border-cyan-400/50 bg-cyan-500/10 text-cyan-200"
                : "border-white/10 bg-white/5 text-slate-400 hover:bg-white/10"
            }`}
          >
            <div className="text-[10px] uppercase tracking-widest">{label}</div>
            <div className="text-lg font-bold">{count}</div>
          </button>
        ))}
      </div>

      {/* Delivery-state filter (briefs only) */}
      {showBriefs && deliveryStates.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-[0.18em] text-slate-500">
            Delivery
          </span>
          {["all", ...deliveryStates].map((state) => (
            <button
              key={state}
              onClick={() => setDeliveryFilter(state)}
              className={`rounded-full border px-3 py-1 text-xs transition-all ${
                deliveryFilter === state
                  ? "border-cyan-500/40 bg-cyan-500/15 text-cyan-100"
                  : "border-white/10 bg-white/5 text-slate-400 hover:bg-white/10"
              }`}
            >
              {state === "all" ? "All" : DELIVERY_LABELS[state] || state}
            </button>
          ))}
        </div>
      )}

      {nothingToShow && (
        <div className="flex flex-col items-center justify-center py-20 border border-dashed border-border rounded-xl bg-card/10">
          <Sparkles className="w-12 h-12 text-muted-foreground/30 mb-4" />
          <p className="text-muted-foreground">
            No intel output matches the current filters yet.
          </p>
        </div>
      )}

      {/* Briefs */}
      {showBriefs && visibleBriefs.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-cyan-300">
            <FileText className="w-4 h-4" />
            Intel Briefs
          </div>
          <div className="grid gap-3">
            {visibleBriefs.map((brief) => (
              <a
                key={brief.artifact_id}
                href={`${API_BASE}/briefs/${encodeURIComponent(brief.artifact_id)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="group block rounded-xl border border-white/5 bg-[#0b1326]/40 p-5 transition-all duration-300 hover:border-cyan-500/20"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span
                        className={`flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${deliveryBadgeClasses(
                          brief.delivery_state,
                        )}`}
                      >
                        {deliveryIcon(brief.delivery_state)}
                        {DELIVERY_LABELS[brief.delivery_state || ""] ||
                          brief.delivery_state ||
                          "unknown"}
                      </span>
                      {brief.source_kind && (
                        <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-slate-300">
                          {brief.source_kind}
                        </span>
                      )}
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {brief.artifact_id.slice(0, 12)}
                      </span>
                    </div>
                    <h3 className="truncate text-lg font-semibold text-white transition-colors group-hover:text-cyan-400">
                      {brief.title || "Untitled brief"}
                    </h3>
                    {brief.summary && (
                      <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-slate-400">
                        {brief.summary}
                      </p>
                    )}
                    {brief.topic_tags && brief.topic_tags.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {brief.topic_tags.slice(0, 6).map((tag) => (
                          <span
                            key={tag}
                            className="flex items-center gap-1 rounded-full border border-white/5 bg-white/5 px-2 py-0.5 text-[10px] text-slate-400"
                          >
                            <Tag className="h-2.5 w-2.5" />
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2 text-right">
                    <ExternalLink className="h-4 w-4 text-muted-foreground transition-colors group-hover:text-cyan-400" />
                    {briefTimestamp(brief) && (
                      <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        {compactDateTime(briefTimestamp(brief))}
                      </span>
                    )}
                  </div>
                </div>
              </a>
            ))}
          </div>
        </section>
      )}

      {/* Demos */}
      {showDemos && demos.length > 0 && (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-cyan-300">
              <Bot className="w-4 h-4" />
              Verified Demos
            </div>
            <a
              href="/dashboard/claude-code-intel"
              className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-cyan-300"
            >
              Full demo surface in Claude Code Intel
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {demos.map((demo) => (
              <a
                key={demo.demo_id}
                href="/dashboard/claude-code-intel"
                className="group block rounded-xl border border-white/5 bg-[#0b1326]/40 p-4 transition-all duration-300 hover:border-cyan-500/20"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  {demo.marker_verified ? (
                    <span className="flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300">
                      <CheckCircle2 className="h-3 w-3" />
                      Verified
                    </span>
                  ) : (
                    <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                      Unverified
                    </span>
                  )}
                  {demo.linked_from_entity && (
                    <span className="flex items-center gap-1 rounded border border-cyan-500/20 bg-cyan-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-cyan-200">
                      <Link2 className="h-3 w-3" />
                      Linked
                    </span>
                  )}
                </div>
                <h3 className="truncate text-sm font-semibold text-white transition-colors group-hover:text-cyan-400">
                  {demo.feature || demo.demo_id}
                </h3>
                {demo.endpoint_hit && (
                  <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
                    {demo.endpoint_hit}
                  </p>
                )}
                <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                  <span className="truncate">{demo.entity_slug || demo.demo_id}</span>
                  {demo.timestamp && (
                    <span className="flex shrink-0 items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {compactDateTime(demo.timestamp)}
                    </span>
                  )}
                </div>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
