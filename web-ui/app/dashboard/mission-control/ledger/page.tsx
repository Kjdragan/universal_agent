"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BookOpen, Filter, Loader2, RefreshCw, XCircle } from "lucide-react";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "/api/dashboard/gateway";
const REFRESH_INTERVAL = 120_000; // 2 min

type LedgerCard = {
  card_id: string;
  subject_kind: string;
  subject_id: string;
  current_state: "retired" | "archived" | string;
  severity: string;
  title: string;
  narrative?: string;
  why_it_matters?: string;
  recommended_next_step?: string | null;
  recurrence_count: number;
  first_observed_at?: string | null;
  last_synthesized_at?: string | null;
  tags?: string[] | null;
  evidence_refs?: Array<{ kind?: string; id?: string; uri?: string; label?: string }> | null;
  synthesis_history?: Array<{ ts?: string; narrative?: string; model?: string }> | null;
  dispatch_history?: Array<{ ts?: string; action?: string; task_id?: string | null }> | null;
  operator_feedback?: {
    thumbs?: "up" | "down" | null;
    snoozed_until?: string | null;
    comments?: Array<{ ts?: string; text?: string }>;
  } | null;
};

type LedgerSummary = {
  retired_count: number;
  archived_count: number;
  recurring_count: number;
  most_recent_retired_iso: string | null;
};

type LedgerResponse = {
  status: string;
  generated_at?: string;
  summary?: LedgerSummary;
  cards?: LedgerCard[];
  filters?: {
    subject_kind: string | null;
    min_recurrence: number;
    state: string | null;
    since_iso: string | null;
    limit: number;
  };
  source?: string;
  error?: string;
};

const SUBJECT_KINDS = [
  "task",
  "run",
  "mission",
  "artifact",
  "failure_pattern",
  "infrastructure",
  "idea",
];

function relTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

function severityClasses(sev: string): string {
  const s = (sev || "").toLowerCase();
  if (s === "critical") return "border-red-500/30 bg-red-500/10 text-red-300";
  if (s === "warning") return "border-accent/30 bg-accent/10 text-accent";
  if (s === "watching") return "border-primary/30 bg-primary/10 text-primary";
  if (s === "success") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  return "border-border bg-card/30 text-muted-foreground";
}

function stateBadgeClasses(state: string): string {
  if (state === "retired") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
  if (state === "archived") return "border-zinc-500/30 bg-zinc-500/10 text-zinc-300";
  return "border-border bg-card/30 text-muted-foreground";
}

export default function MissionControlLedgerPage() {
  const [cards, setCards] = useState<LedgerCard[]>([]);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // filters
  const [subjectKind, setSubjectKind] = useState<string>("");
  const [minRecurrence, setMinRecurrence] = useState<number>(0);
  const [stateFilter, setStateFilter] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    params.set("limit", "200");
    if (subjectKind) params.set("subject_kind", subjectKind);
    if (minRecurrence > 0) params.set("min_recurrence", String(minRecurrence));
    if (stateFilter) params.set("state", stateFilter);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/mission-control/ledger?${params.toString()}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`Failed to load ledger: ${res.status}`);
      const json: LedgerResponse = await res.json();
      if (json.status !== "ok") throw new Error(json.error || "ledger returned non-ok status");
      setCards(json.cards || []);
      setSummary(json.summary || null);
      setGeneratedAt(json.generated_at || null);
    } catch (err: any) {
      setError(err?.message || "Failed to load ledger");
    } finally {
      setLoading(false);
    }
  }, [subjectKind, minRecurrence, stateFilter]);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), REFRESH_INTERVAL);
    return () => clearInterval(t);
  }, [load]);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const counts = useMemo(() => {
    const byKind: Record<string, number> = {};
    for (const c of cards) {
      byKind[c.subject_kind] = (byKind[c.subject_kind] || 0) + 1;
    }
    return byKind;
  }, [cards]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto px-4 py-4 lg:px-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard/mission-control"
            className="inline-flex items-center gap-1 rounded border border-border bg-card/40 px-2 py-1 text-xs text-muted-foreground hover:bg-card/80"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Mission Control
          </Link>
          <h1 className="flex items-center gap-2 text-lg font-medium text-foreground">
            <BookOpen className="h-5 w-5 text-primary" />
            Knowledge Ledger
          </h1>
          <span className="text-xs text-muted-foreground">
            durable card history · {generatedAt ? `updated ${relTime(generatedAt)}` : "—"}
          </span>
        </div>
        <button
          onClick={() => void load()}
          className="inline-flex items-center gap-1 rounded border border-border bg-card/40 px-2 py-1 text-xs text-muted-foreground hover:bg-card/80"
          title="Refresh"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      </div>

      {/* Summary band */}
      {summary && (
        <div className="grid grid-cols-2 gap-3 rounded border border-white/10 bg-[#0b1326]/70 p-3 backdrop-blur-md md:grid-cols-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Retired</div>
            <div className="text-lg font-medium text-foreground">{summary.retired_count}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Archived</div>
            <div className="text-lg font-medium text-foreground">{summary.archived_count}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Recurring (≥2)</div>
            <div className="text-lg font-medium text-amber-200">{summary.recurring_count}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Most recent retire</div>
            <div className="text-sm text-foreground">{relTime(summary.most_recent_retired_iso)}</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 rounded border border-white/10 bg-[#0b1326]/40 p-3 text-xs">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <label className="flex items-center gap-1">
          <span className="text-muted-foreground">subject kind</span>
          <select
            value={subjectKind}
            onChange={(e) => setSubjectKind(e.target.value)}
            className="rounded border border-border bg-card/40 px-2 py-0.5 text-foreground"
          >
            <option value="">all</option>
            {SUBJECT_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="text-muted-foreground">recurrence ≥</span>
          <select
            value={String(minRecurrence)}
            onChange={(e) => setMinRecurrence(Number(e.target.value))}
            className="rounded border border-border bg-card/40 px-2 py-0.5 text-foreground"
          >
            <option value="0">any</option>
            <option value="2">2 (recurring)</option>
            <option value="3">3</option>
            <option value="5">5</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="text-muted-foreground">state</span>
          <select
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value)}
            className="rounded border border-border bg-card/40 px-2 py-0.5 text-foreground"
          >
            <option value="">retired + archived</option>
            <option value="retired">retired only</option>
            <option value="archived">archived only</option>
          </select>
        </label>
        <span className="ml-auto text-muted-foreground">
          {cards.length} card{cards.length === 1 ? "" : "s"}
          {Object.keys(counts).length > 0 && (
            <span className="ml-2">
              ({Object.entries(counts)
                .map(([k, n]) => `${k}:${n}`)
                .join(" · ")})
            </span>
          )}
        </span>
      </div>

      {/* Body */}
      {loading && cards.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading ledger…
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded border border-red-500/30 bg-red-500/5 p-6 text-sm text-red-200">
          <XCircle className="h-6 w-6" />
          {error}
          <button
            onClick={() => void load()}
            className="rounded border border-border bg-card/30 px-3 py-1 text-xs text-foreground/80 hover:bg-card/60"
          >
            Retry
          </button>
        </div>
      ) : cards.length === 0 ? (
        <div className="rounded border border-white/10 bg-[#0b1326]/40 p-6 text-center text-sm text-muted-foreground">
          No cards match the current filters. Adjust filters above or wait for the sweeper to retire more cards.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {cards.map((card) => {
            const isOpen = expanded.has(card.card_id);
            return (
              <div
                key={card.card_id}
                className="rounded border border-white/10 bg-[#0b1326]/60 p-3 backdrop-blur-md"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${severityClasses(
                          card.severity,
                        )}`}
                      >
                        {card.severity || "info"}
                      </span>
                      <span
                        className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${stateBadgeClasses(
                          card.current_state,
                        )}`}
                      >
                        {card.current_state}
                      </span>
                      <span className="rounded border border-border bg-card/30 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {card.subject_kind}
                      </span>
                      {card.recurrence_count > 1 && (
                        <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
                          ×{card.recurrence_count} occurrences
                        </span>
                      )}
                    </div>
                    <h3 className="mt-1 text-sm font-medium text-foreground">{card.title}</h3>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      first seen {relTime(card.first_observed_at)} · last synthesized{" "}
                      {relTime(card.last_synthesized_at)} · subject_id={" "}
                      <span className="font-mono">{card.subject_id}</span>
                    </div>
                  </div>
                  <button
                    onClick={() => toggle(card.card_id)}
                    className="rounded border border-border bg-card/30 px-2 py-0.5 text-xs text-muted-foreground hover:bg-card/60"
                  >
                    {isOpen ? "Hide" : "Detail"}
                  </button>
                </div>

                {isOpen && (
                  <div className="mt-3 grid gap-3 border-t border-white/10 pt-3 text-xs">
                    {card.narrative && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Narrative
                        </div>
                        <p className="whitespace-pre-wrap text-foreground/90">{card.narrative}</p>
                      </div>
                    )}
                    {card.why_it_matters && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Why it matters
                        </div>
                        <p className="whitespace-pre-wrap text-foreground/80">{card.why_it_matters}</p>
                      </div>
                    )}
                    {card.recommended_next_step && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Recommended next step
                        </div>
                        <p className="whitespace-pre-wrap text-foreground/80">
                          {card.recommended_next_step}
                        </p>
                      </div>
                    )}
                    {card.tags && card.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {card.tags.map((t) => (
                          <span
                            key={t}
                            className="rounded border border-border bg-card/30 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                    {card.synthesis_history && card.synthesis_history.length > 0 && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Synthesis history ({card.synthesis_history.length})
                        </div>
                        <ul className="space-y-1">
                          {card.synthesis_history.slice(-5).map((entry, idx) => (
                            <li key={idx} className="rounded border border-white/5 bg-card/20 p-2">
                              <div className="text-[10px] text-muted-foreground">
                                {entry.ts ? relTime(entry.ts) : "—"}
                                {entry.model ? ` · ${entry.model}` : ""}
                              </div>
                              <div className="mt-1 whitespace-pre-wrap text-foreground/80">
                                {entry.narrative}
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {card.operator_feedback?.comments && card.operator_feedback.comments.length > 0 && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Operator comments ({card.operator_feedback.comments.length})
                        </div>
                        <ul className="space-y-1">
                          {card.operator_feedback.comments.slice(-10).map((c, idx) => (
                            <li
                              key={idx}
                              className="rounded border border-white/5 bg-card/20 p-2 text-foreground/85"
                            >
                              <div className="text-[10px] text-muted-foreground">
                                {c.ts ? relTime(c.ts) : "—"}
                              </div>
                              <div className="mt-0.5 whitespace-pre-wrap">{c.text}</div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {card.dispatch_history && card.dispatch_history.length > 0 && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Dispatch history
                        </div>
                        <ul className="space-y-1">
                          {card.dispatch_history.slice(-5).map((d, idx) => (
                            <li key={idx} className="rounded border border-white/5 bg-card/20 p-2">
                              <div className="text-[10px] text-muted-foreground">
                                {d.ts ? relTime(d.ts) : "—"} · {d.action}
                                {d.task_id ? ` · task=${d.task_id}` : ""}
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {card.evidence_refs && card.evidence_refs.length > 0 && (
                      <div>
                        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          Evidence refs ({card.evidence_refs.length})
                        </div>
                        <ul className="space-y-1">
                          {card.evidence_refs.slice(0, 10).map((r, idx) => (
                            <li key={idx} className="rounded border border-white/5 bg-card/20 p-2">
                              <span className="font-mono text-[10px] text-muted-foreground">
                                {r.kind}:{r.id}
                              </span>{" "}
                              <span className="text-foreground/80">{r.label}</span>
                              {r.uri && (
                                <a
                                  href={r.uri}
                                  className="ml-2 text-primary hover:underline"
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  open
                                </a>
                              )}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
