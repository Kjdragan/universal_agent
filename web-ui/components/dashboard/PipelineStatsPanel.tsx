"use client";

import { useEffect, useState, useCallback } from "react";
import {
  GitMerge,
  RefreshCw,
  ShieldCheck,
  Cog,
  Rocket,
  Bot,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

type ApprovalItem = {
  approval_id: string;
  title: string;
  status: string;
  task_id: string;
  created_at?: string | number;
  source_kind?: string;
};

type RefinementItem = {
  task_id: string;
  title: string;
  status: string;
  source_kind: string;
  project_key: string;
  priority?: number;
  labels: string[];
  refinement_stage: string;
  updated_at?: string;
  created_at?: string;
};

type DispatchItem = {
  task_id: string;
  title: string;
  status: string;
  source_kind: string;
  project_key: string;
  priority?: number;
  labels: string[];
  eligible: boolean;
  skip_reason?: string | null;
  rank?: number;
  target_agent: string;
  routing_confidence: string;
  routing_reason: string;
  should_delegate: boolean;
  updated_at?: string;
  created_at?: string;
};

type PipelineData = {
  pending_approvals: ApprovalItem[];
  refinement_items: RefinementItem[];
  dispatch_queue: DispatchItem[];
  counts: {
    approvals: number;
    refinement: number;
    dispatch_eligible: number;
    dispatch_total: number;
  };
};

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const AGENT_DISPLAY: Record<string, { label: string; color: string }> = {
  simone: { label: "Simone", color: "text-purple-400" },
  "vp.coder.primary": { label: "CODIE", color: "text-sky-400" },
  "vp.general.primary": { label: "ATLAS", color: "text-emerald-400" },
};

function agentBadge(agentId: string) {
  const info = AGENT_DISPLAY[agentId] || {
    label: agentId,
    color: "text-muted-foreground",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wider uppercase bg-white/5 border border-white/10 ${info.color}`}
    >
      <Bot className="h-3 w-3" />
      {info.label}
    </span>
  );
}

function sourceKindBadge(kind: string) {
  if (!kind) return null;
  const colorMap: Record<string, string> = {
    csi: "text-kcd-cyan bg-kcd-cyan/10 border-kcd-cyan/20",
    email: "text-blue-400 bg-blue-400/10 border-blue-400/20",
    brainstorm: "text-purple-400 bg-purple-400/10 border-purple-400/20",
    calendar: "text-amber-400 bg-amber-400/10 border-amber-400/20",
    manual: "text-gray-400 bg-gray-400/10 border-gray-400/20",
  };
  const cls = colorMap[kind] || "text-gray-400 bg-gray-400/10 border-gray-400/20";
  return (
    <span
      className={`inline-flex rounded px-1 py-0.5 text-[9px] font-medium tracking-wider uppercase border ${cls}`}
    >
      {kind}
    </span>
  );
}

function timeAgo(raw?: string | number | null): string {
  if (!raw) return "";
  const ts = typeof raw === "number" ? raw * 1000 : new Date(raw).getTime();
  if (isNaN(ts)) return "";
  const diffMs = Date.now() - ts;
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/* ------------------------------------------------------------------ */
/* Collapsible Section                                                 */
/* ------------------------------------------------------------------ */

function Section({
  icon: Icon,
  title,
  count,
  accentColor,
  defaultOpen = true,
  children,
}: {
  icon: React.ElementType;
  title: string;
  count: number;
  accentColor: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 py-1.5 text-left group"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted-foreground" />
        )}
        <Icon className={`h-3.5 w-3.5 ${accentColor}`} />
        <span className="text-xs font-medium text-foreground/80 uppercase tracking-wider">
          {title}
        </span>
        <span
          className={`ml-auto inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1.5 text-[10px] font-bold ${
            count > 0
              ? `${accentColor} bg-white/5 border border-white/10`
              : "text-muted-foreground/50"
          }`}
        >
          {count}
        </span>
      </button>
      {open && <div className="pl-5 mt-1 space-y-1">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Empty state                                                         */
/* ------------------------------------------------------------------ */

function EmptyList({ label }: { label: string }) {
  return (
    <p className="text-[11px] text-muted-foreground/60 italic py-1">
      No {label}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Main component                                                      */
/* ------------------------------------------------------------------ */

export function PipelineStatsPanel({ refreshKey }: { refreshKey: number }) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<PipelineData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        "/api/dashboard/gateway/api/v1/dashboard/proactive-pipeline",
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(`Failed to load: ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (err: any) {
      setError(err.message || "Failed to load pipeline data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  /* --- Loading skeleton --- */
  if (loading && !data) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <GitMerge className="h-4 w-4 text-secondary_container" />
          <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">
            Proactive Pipeline
          </h2>
        </div>
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div
              key={i}
              className="animate-pulse rounded bg-card/50 h-5 w-full"
            />
          ))}
        </div>
      </div>
    );
  }

  /* --- Error state --- */
  if (error && !data) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4 flex flex-col items-center justify-center">
        <p className="text-sm text-red-400 mb-2">
          Error loading proactive pipeline
        </p>
        <button
          onClick={load}
          className="text-xs text-primary hover:underline flex items-center gap-1"
        >
          <RefreshCw className="h-3 w-3" /> Retry
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { pending_approvals, refinement_items, dispatch_queue, counts } = data;
  const eligibleDispatch = dispatch_queue.filter((d) => d.eligible);
  const skippedDispatch = dispatch_queue.filter((d) => !d.eligible);

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4 flex flex-col">
      {/* Header */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitMerge className="h-4 w-4 text-secondary_container" />
          <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">
            Proactive Pipeline
          </h2>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Refresh"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
          />
        </button>
      </div>

      {/* ---- PENDING APPROVALS ---- */}
      <Section
        icon={ShieldCheck}
        title="Pending Approvals"
        count={counts.approvals}
        accentColor="text-kcd-amber"
      >
        {pending_approvals.length === 0 ? (
          <EmptyList label="pending approvals" />
        ) : (
          pending_approvals.map((a) => (
            <div
              key={a.approval_id}
              className="flex items-start gap-2 rounded bg-white/[0.03] border border-white/5 px-2.5 py-2 group hover:bg-white/[0.06] transition-colors"
            >
              <AlertTriangle className="h-3.5 w-3.5 text-kcd-amber mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-xs text-foreground/90 leading-snug truncate">
                  {a.title}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  {sourceKindBadge(a.source_kind || "")}
                  <span className="text-[10px] text-muted-foreground">
                    {timeAgo(a.created_at)}
                  </span>
                </div>
              </div>
            </div>
          ))
        )}
      </Section>

      {/* ---- REFINEMENT / DECOMPOSITION ---- */}
      <Section
        icon={Cog}
        title="Refinement"
        count={counts.refinement}
        accentColor="text-kcd-cyan"
      >
        {refinement_items.length === 0 ? (
          <EmptyList label="items in refinement" />
        ) : (
          refinement_items.map((r) => (
            <div
              key={r.task_id}
              className="flex items-start gap-2 rounded bg-white/[0.03] border border-white/5 px-2.5 py-2 hover:bg-white/[0.06] transition-colors"
            >
              <Cog className="h-3.5 w-3.5 text-kcd-cyan mt-0.5 shrink-0 animate-[spin_4s_linear_infinite]" />
              <div className="flex-1 min-w-0">
                <p className="text-xs text-foreground/90 leading-snug truncate">
                  {r.title}
                </p>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  {sourceKindBadge(r.source_kind)}
                  {r.refinement_stage && (
                    <span className="text-[9px] text-kcd-cyan/70 uppercase tracking-wider">
                      {r.refinement_stage}
                    </span>
                  )}
                  {r.project_key && (
                    <span className="text-[9px] text-muted-foreground">
                      {r.project_key}
                    </span>
                  )}
                  <span className="text-[10px] text-muted-foreground">
                    {timeAgo(r.updated_at)}
                  </span>
                </div>
              </div>
            </div>
          ))
        )}
      </Section>

      {/* ---- DISPATCH QUEUE ---- */}
      <Section
        icon={Rocket}
        title="Dispatch Queue"
        count={counts.dispatch_eligible}
        accentColor="text-green-400"
      >
        {eligibleDispatch.length === 0 && skippedDispatch.length === 0 ? (
          <EmptyList label="items in dispatch queue" />
        ) : (
          <>
            {eligibleDispatch.map((d) => (
              <div
                key={d.task_id}
                className="flex items-start gap-2 rounded bg-white/[0.03] border border-white/5 px-2.5 py-2 hover:bg-white/[0.06] transition-colors"
              >
                <Rocket className="h-3.5 w-3.5 text-green-400 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-foreground/90 leading-snug truncate">
                    {d.title}
                  </p>
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    {agentBadge(d.target_agent)}
                    {sourceKindBadge(d.source_kind)}
                    {d.rank != null && (
                      <span className="text-[9px] text-muted-foreground font-mono">
                        #{d.rank}
                      </span>
                    )}
                    <span className="text-[10px] text-muted-foreground">
                      {timeAgo(d.updated_at)}
                    </span>
                  </div>
                  {d.routing_reason && (
                    <p className="text-[10px] text-muted-foreground/60 mt-0.5 truncate">
                      {d.routing_reason}
                    </p>
                  )}
                </div>
              </div>
            ))}
            {skippedDispatch.length > 0 && (
              <details className="group mt-1">
                <summary className="text-[10px] text-muted-foreground/50 cursor-pointer hover:text-muted-foreground transition-colors">
                  {skippedDispatch.length} skipped
                </summary>
                <div className="mt-1 space-y-1">
                  {skippedDispatch.map((d) => (
                    <div
                      key={d.task_id}
                      className="flex items-start gap-2 rounded bg-white/[0.02] border border-white/5 px-2.5 py-1.5 opacity-60"
                    >
                      <Rocket className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-[11px] text-foreground/60 truncate">
                          {d.title}
                        </p>
                        <div className="flex items-center gap-2 mt-0.5">
                          {agentBadge(d.target_agent)}
                          {d.skip_reason && (
                            <span className="text-[9px] text-red-400/60 truncate">
                              {d.skip_reason}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </>
        )}
      </Section>
    </div>
  );
}
