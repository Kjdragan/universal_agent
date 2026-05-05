"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = "/api/dashboard/gateway";
const FILTER_PREFS_KEY = "ua.dashboard.proactiveSignals.filterPrefs.v1";
const SOURCE_FILTERS = ["all", "youtube", "discord"] as const;
const STATUS_FILTERS = ["pending", "tracking", "actioned", "rejected", "all"] as const;
const FEEDBACK_CHIPS = [
  "more_like_this",
  "less_like_this",
  "good_source",
  "bad_source",
  "wrong_topic",
  "too_noisy",
  "too_shallow",
  "novel",
];

type SignalAction = {
  id: string;
  label: string;
  description?: string;
};

type SignalEvidence = {
  title?: string;
  channel?: string;
  label?: string;
  url?: string;
  summary?: string;
  transcript_status?: string;
  occurred_at?: string;
};

type SignalCard = {
  card_id: string;
  source: string;
  card_type: string;
  title: string;
  summary: string;
  status: string;
  priority: number;
  confidence_score: number;
  novelty_score: number;
  evidence: SignalEvidence[];
  actions: SignalAction[];
  feedback?: { tag_counts?: Record<string, number>; history?: unknown[] };
  selected_action?: { task_id?: string; action?: SignalAction };
  created_at?: string;
  updated_at?: string;
};

function percent(value: number | undefined): string {
  const n = Number(value || 0);
  return `${Math.round(n * 100)}%`;
}

function formatTag(tag: string): string {
  return tag.replaceAll("_", " ");
}

const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function compactDateTime(iso: string | undefined | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const mon = SHORT_MONTHS[d.getMonth()];
  const day = d.getDate();
  const h = d.getHours();
  const m = d.getMinutes();
  const ampm = h >= 12 ? "p" : "a";
  const h12 = h % 12 || 12;
  const mm = m < 10 ? `0${m}` : `${m}`;
  return `${mon} ${day} ${h12}:${mm}${ampm}`;
}

export default function ProactiveSignalsPage() {
  const [cards, setCards] = useState<SignalCard[]>([]);
  const [source, setSource] = useState<(typeof SOURCE_FILTERS)[number]>(() => {
    if (typeof window !== "undefined") {
      try {
        const cached = JSON.parse(window.localStorage.getItem(FILTER_PREFS_KEY) || "{}");
        const s = String(cached.source || "");
        if ((SOURCE_FILTERS as readonly string[]).includes(s)) return s as (typeof SOURCE_FILTERS)[number];
      } catch { /* ignore */ }
    }
    return "all";
  });
  const [status, setStatus] = useState<(typeof STATUS_FILTERS)[number]>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [feedbackOpenId, setFeedbackOpenId] = useState("");
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackTags, setFeedbackTags] = useState<string[]>([]);

  const saveFilterPrefs = useCallback((overrides?: { source?: string; status?: string }) => {
    const prefs = {
      source: overrides?.source ?? source,
      status: overrides?.status ?? status,
    };
    try {
      window.localStorage.setItem(FILTER_PREFS_KEY, JSON.stringify(prefs));
    } catch { /* ignore */ }
  }, [source, status]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ source, status, limit: "120", sync: "background" });
      const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-signals?${params.toString()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      const data = await res.json();
      setCards(Array.isArray(data.cards) ? data.cards : []);
    } catch (err) {
      setError((err as Error).message || "Failed to load proactive signals.");
    } finally {
      setLoading(false);
    }
  }, [source, status]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleFeedbackTag = useCallback((tag: string) => {
    setFeedbackTags((prev) => prev.includes(tag) ? prev.filter((item) => item !== tag) : [...prev, tag]);
  }, []);

  const submitFeedback = useCallback(async (cardId: string, nextStatus?: string) => {
    setBusyId(cardId);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-signals/${encodeURIComponent(cardId)}/feedback`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: nextStatus,
          feedback_tags: feedbackTags,
          feedback_text: feedbackText,
        }),
      });
      if (!res.ok) throw new Error(`Feedback failed (${res.status})`);
      setFeedbackOpenId("");
      setFeedbackText("");
      setFeedbackTags([]);
      await load();
    } catch (err) {
      setError((err as Error).message || "Feedback failed.");
    } finally {
      setBusyId("");
    }
  }, [feedbackTags, feedbackText, load]);

  const deleteCard = useCallback(async (cardId: string) => {
    setBusyId(cardId);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-signals/${encodeURIComponent(cardId)}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      setFeedbackOpenId((prev) => prev === cardId ? "" : prev);
      await load();
    } catch (err) {
      setError((err as Error).message || "Delete failed.");
    } finally {
      setBusyId("");
    }
  }, [load]);

  const deleteVisibleCards = useCallback(async () => {
    if (!cards.length) return;
    if (!confirm(`Are you sure you want to silently delete all ${cards.length} currently visible cards? This will not record any feedback signals.`)) return;
    
    setLoading(true);
    setError("");
    let hasError = false;
    
    try {
      // Delete in parallel to be fast, since they are independent operations.
      await Promise.all(
        cards.map(async (card) => {
          const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-signals/${encodeURIComponent(card.card_id)}`, {
            method: "DELETE",
          });
          if (!res.ok) {
             hasError = true;
          }
        })
      );
      
      if (hasError) throw new Error("Some items failed to delete.");
      setFeedbackOpenId("");
      await load();
    } catch (err) {
      setError((err as Error).message || "Bulk delete failed.");
    } finally {
      setLoading(false);
    }
  }, [cards, load]);

  const selectAction = useCallback(async (cardId: string, actionId: string) => {
    setBusyId(cardId);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-signals/${encodeURIComponent(cardId)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_id: actionId,
          feedback_tags: feedbackOpenId === cardId ? feedbackTags : [],
          feedback_text: feedbackOpenId === cardId ? feedbackText : "",
        }),
      });
      if (!res.ok) throw new Error(`Action failed (${res.status})`);
      setFeedbackOpenId("");
      setFeedbackText("");
      setFeedbackTags([]);
      await load();
    } catch (err) {
      setError((err as Error).message || "Action failed.");
    } finally {
      setBusyId("");
    }
  }, [feedbackOpenId, feedbackTags, feedbackText, load]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Proactive Signals</h1>
          <p className="text-sm text-muted-foreground">Cheap discovery cards from YouTube and Discord with action and feedback loops.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {cards.length > 0 && (
            <button
              type="button"
              onClick={deleteVisibleCards}
              disabled={loading || Boolean(busyId)}
              className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/20 disabled:opacity-50"
            >
              Delete All {cards.length}
            </button>
          )}
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="rounded-md border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card disabled:opacity-60"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        {SOURCE_FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => { setSource(item); saveFilterPrefs({ source: item }); }}
            className={`rounded-md border px-3 py-1.5 capitalize ${source === item ? "border-primary/40 bg-primary/15 text-primary" : "border-border bg-card/40 text-muted-foreground hover:text-foreground"}`}
          >
            {item}
          </button>
        ))}
        <span className="mx-1 text-muted-foreground">/</span>
        {STATUS_FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => { setStatus(item); saveFilterPrefs({ status: item }); }}
            className={`rounded-md border px-3 py-1.5 capitalize ${status === item ? "border-primary/40 bg-primary/15 text-primary" : "border-border bg-card/40 text-muted-foreground hover:text-foreground"}`}
          >
            {item}
          </button>
        ))}
      </div>

      {error && <div className="border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</div>}
      {!loading && cards.length === 0 && <div className="border border-border bg-card/20 p-6 text-center text-sm text-muted-foreground">No proactive signal cards found.</div>}

      <div className="grid gap-3">
        {cards.map((card) => {
          const feedbackOpen = feedbackOpenId === card.card_id;
          return (
            <article key={card.card_id} className="border border-border bg-card/30 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-foreground">{card.title}</h2>
                    <span className="rounded border border-border px-2 py-0.5 text-[11px] uppercase text-muted-foreground">{card.source}</span>
                    <span className="rounded border border-primary/25 bg-primary/10 px-2 py-0.5 text-[11px] uppercase text-primary">{card.card_type}</span>
                    <span className="rounded border border-border px-2 py-0.5 text-[11px] uppercase text-muted-foreground">{card.status}</span>
                  </div>
                  <p className="text-sm leading-relaxed text-foreground/80">{card.summary}</p>
                  <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                    <span>Confidence {percent(card.confidence_score)}</span>
                    <span>Novelty {percent(card.novelty_score)}</span>
                    <span>Priority {card.priority}</span>
                    {card.created_at && (
                      <span className="text-primary/60" title={card.created_at}>
                        {compactDateTime(card.created_at)}
                      </span>
                    )}
                  </div>
                  <div className="mt-3 space-y-1">
                    {card.evidence.slice(0, 4).map((item, index) => (
                      <div key={`${card.card_id}-evidence-${index}`} className="text-xs text-muted-foreground">
                        {item.url ? (
                          <a href={item.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{item.title || item.url}</a>
                        ) : (
                          <span>{item.title || item.label || "Evidence"}</span>
                        )}
                        {item.channel && <span> · {item.channel}</span>}
                        {item.transcript_status && <span> · transcript {item.transcript_status}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {card.actions.map((action) => (
                  <button
                    key={action.id}
                    type="button"
                    onClick={() => selectAction(card.card_id, action.id)}
                    disabled={Boolean(busyId)}
                    title={action.description || action.label}
                    className="rounded-md border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-primary hover:bg-primary/20 disabled:opacity-50"
                  >
                    {action.label}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    setFeedbackOpenId(feedbackOpen ? "" : card.card_id);
                    setFeedbackText("");
                    setFeedbackTags([]);
                  }}
                  className="rounded-md border border-border bg-card/60 px-3 py-1.5 text-xs hover:bg-card"
                >
                  Feedback
                </button>
                {card.status === "pending" && (
                  <button
                    type="button"
                    onClick={() => submitFeedback(card.card_id, "rejected")}
                    disabled={busyId === card.card_id}
                    className="rounded-md border border-red-400/30 bg-red-400/10 px-3 py-1.5 text-xs text-red-300 hover:bg-red-400/20 disabled:opacity-50"
                  >
                    Reject
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => deleteCard(card.card_id)}
                  disabled={busyId === card.card_id}
                  title="Silently delete this card without recording feedback"
                  className="rounded-md border border-border bg-card/60 px-3 py-1.5 text-xs hover:bg-red-500/10 hover:text-red-400 disabled:opacity-50"
                >
                  Delete
                </button>
              </div>

              {feedbackOpen && (
                <div className="mt-4 border border-border bg-background/70 p-3">
                  <div className="flex flex-wrap gap-2">
                    {FEEDBACK_CHIPS.map((chip) => (
                      <button
                        key={chip}
                        type="button"
                        onClick={() => toggleFeedbackTag(chip)}
                        className={`rounded-md border px-2 py-1 text-[11px] capitalize ${feedbackTags.includes(chip) ? "border-primary/40 bg-primary/15 text-primary" : "border-border bg-card/50 text-muted-foreground"}`}
                      >
                        {formatTag(chip)}
                      </button>
                    ))}
                  </div>
                  <textarea
                    value={feedbackText}
                    onChange={(event) => setFeedbackText(event.target.value)}
                    placeholder="Optional feedback for future suggestions"
                    className="mt-3 min-h-20 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      onClick={() => submitFeedback(card.card_id)}
                      disabled={busyId === card.card_id}
                      className="rounded-md border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-primary hover:bg-primary/20 disabled:opacity-50"
                    >
                      Save Feedback
                    </button>
                    <button
                      type="button"
                      onClick={() => submitFeedback(card.card_id, "rejected")}
                      disabled={busyId === card.card_id}
                      className="rounded-md border border-red-400/30 bg-red-400/10 px-3 py-1.5 text-xs text-red-300 hover:bg-red-400/20 disabled:opacity-50"
                    >
                      Reject With Feedback
                    </button>
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
