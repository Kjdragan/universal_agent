"use client";

import { useCallback, useEffect, useState } from "react";
import { 
  History, 
  RefreshCw, 
  ExternalLink, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  AlertCircle,
  MessageSquare,
  ThumbsUp,
  ThumbsDown,
  Tag,
  FileText,
  Layers
} from "lucide-react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";

const API_BASE = "/api/dashboard/gateway";
const FEEDBACK_CHIPS = [
  "successful",
  "failed",
  "too_slow",
  "unexpected",
  "helpful",
  "continue_work",
  "needs_followup",
  "annoying",
  "out_of_scope",
];

type TaskEvidence = {
  evidence_id: string;
  source_kind: string;
  title?: string;
  url?: string;
  description?: string;
  occurred_at?: string;
};

type TaskArtifact = {
  artifact_id: string;
  artifact_type: string;
  title: string;
  summary?: string;
  status?: string;
  delivery_state?: string;
  href?: string;
  updated_at?: string;
};

type TaskRecap = {
  status?: string;
  idea?: string;
  implemented?: string;
  known_issues?: string;
  success_assessment?: string;
  recommended_next_action?: string;
  confidence?: number | null;
  generated_at?: string;
};

type TaskLinks = {
  session_href?: string;
  three_panel_href?: string;
  run_log_href?: string;
  transcript_href?: string;
  workspace_dir?: string;
};

type TaskHistoryItem = {
  task_id: string;
  source_kind: string;
  title: string;
  description: string;
  status: string;
  stage?: "queued" | "running" | "completed" | "needs_attention";
  result_summary?: string;
  session_id?: string;
  run_id?: string;
  workspace_dir?: string;
  agent_id?: string;
  session_role?: string;
  run_kind?: string;
  completed_at?: string;
  feedback_json?: string;
  evidence: TaskEvidence[];
  artifacts?: TaskArtifact[];
  recap?: TaskRecap;
  links?: TaskLinks;
};

type OpportunityItem = {
  id: string;
  source: string;
  title: string;
  summary: string;
  status: string;
  priority?: number;
  confidence_score?: number;
  updated_at?: string;
};

type HistoryCounts = {
  opportunities?: number;
  queued?: number;
  running?: number;
  completed?: number;
  needs_attention?: number;
  total_tasks?: number;
};

const STAGE_LABELS: Record<string, string> = {
  all: "All",
  opportunities: "Opportunities",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  needs_attention: "Needs Attention",
};

function safeJson(raw: string | undefined | null): any {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
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

export default function ProactiveTaskHistoryPage() {
  const [tasks, setTasks] = useState<TaskHistoryItem[]>([]);
  const [opportunities, setOpportunities] = useState<OpportunityItem[]>([]);
  const [counts, setCounts] = useState<HistoryCounts>({});
  const [stage, setStage] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [feedbackOpenId, setFeedbackOpenId] = useState("");
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackTags, setFeedbackTags] = useState<string[]>([]);
  const [sentiment, setSentiment] = useState<"positive" | "negative" | "neutral" | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-task-history`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      const data = await res.json();
      setTasks(Array.isArray(data.tasks) ? data.tasks : []);
      setOpportunities(Array.isArray(data.opportunities) ? data.opportunities : []);
      setCounts(data.counts || {});
    } catch (err) {
      setError((err as Error).message || "Failed to load task history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const submitFeedback = useCallback(async (taskId: string) => {
    setBusyId(taskId);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/proactive-task-history/${encodeURIComponent(taskId)}/feedback`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sentiment: sentiment,
          feedback_tags: feedbackTags,
          feedback_text: feedbackText,
        }),
      });
      if (!res.ok) throw new Error(`Feedback failed (${res.status})`);
      setFeedbackOpenId("");
      setFeedbackText("");
      setFeedbackTags([]);
      setSentiment(null);
      await load();
    } catch (err) {
      setError((err as Error).message || "Feedback failed.");
    } finally {
      setBusyId("");
    }
  }, [feedbackTags, feedbackText, sentiment, load]);

  const toggleFeedbackTag = (tag: string) => {
    setFeedbackTags((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]);
  };

  const rehydrateSession = (item: TaskHistoryItem) => {
    if (!item.session_id) return;
    openOrFocusChatWindow({
      sessionId: item.session_id,
      runId: item.run_id,
      workspace: item.workspace_dir || item.links?.workspace_dir,
      attachMode: "tail",
      role: "viewer"
    });
  };

  const visibleTasks = tasks.filter((task) => stage === "all" || task.stage === stage);
  const showOpportunities = stage === "all" || stage === "opportunities";

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <History className="w-6 h-6 text-cyan-400" />
            Proactive Task History
          </h1>
          <p className="text-muted-foreground mt-1">
            Audit autonomous opportunities, executions, artifacts, and session history.
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

      <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
        {(["all", "opportunities", "queued", "running", "completed", "needs_attention"] as const).map((key) => {
          const count = key === "all"
            ? (counts.total_tasks || 0) + (counts.opportunities || 0)
            : key === "opportunities"
              ? counts.opportunities || 0
              : counts[key] || 0;
          return (
            <button
              key={key}
              onClick={() => setStage(key)}
              className={`rounded-lg border px-3 py-2 text-left transition-all ${
                stage === key ? "border-cyan-400/50 bg-cyan-500/10 text-cyan-200" : "border-white/10 bg-white/5 text-slate-400 hover:bg-white/10"
              }`}
            >
              <div className="text-[10px] uppercase tracking-widest">{STAGE_LABELS[key]}</div>
              <div className="text-lg font-bold">{count}</div>
            </button>
          );
        })}
      </div>

      {!loading && tasks.length === 0 && opportunities.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 border border-dashed border-border rounded-xl bg-card/10">
          <Clock className="w-12 h-12 text-muted-foreground/30 mb-4" />
          <p className="text-muted-foreground">No proactive opportunities or tasks found.</p>
        </div>
      )}

      {showOpportunities && opportunities.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-cyan-300">
            <Layers className="w-4 h-4" />
            Opportunities Detected
          </div>
          <div className="grid gap-3">
            {opportunities.map((item) => (
              <div key={item.id} className="rounded-xl border border-cyan-500/10 bg-cyan-500/[0.04] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 text-[10px] font-bold uppercase tracking-wider border border-cyan-500/20">
                        {item.source || "opportunity"}
                      </span>
                      <span className="text-[11px] text-muted-foreground font-mono">{item.id.slice(0, 12)}</span>
                    </div>
                    <h3 className="text-sm font-semibold text-white">{item.title}</h3>
                    <p className="mt-1 text-xs text-slate-400 line-clamp-2">{item.summary}</p>
                  </div>
                  <div className="text-right text-[11px] text-muted-foreground">
                    {item.confidence_score !== undefined && <div>confidence {Number(item.confidence_score).toFixed(2)}</div>}
                    <div>{compactDateTime(item.updated_at)}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="grid gap-4">
        {visibleTasks.map((task) => {
          const isFeedbackOpen = feedbackOpenId === task.task_id;
          const feedback = safeJson(task.feedback_json);
          const stageLabel = STAGE_LABELS[task.stage || task.status] || task.status;
          const recap = task.recap || {};
          const recapStatus = recap.status || "";
          const recapStatusLabel = recapStatus
            ? recapStatus.replace(/_/g, " ")
            : "not evaluated";
          const recapIsLlm = recapStatus === "llm_evaluated";

          return (
            <div
              key={task.task_id}
              className="group relative bg-[#0b1326]/40 border border-white/5 rounded-xl overflow-hidden hover:border-white/10 transition-all duration-300"
            >
              <div className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="space-y-1 flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 text-[10px] font-bold uppercase tracking-wider border border-cyan-500/20">
                        {task.source_kind}
                      </span>
                      <span className="px-2 py-0.5 rounded bg-white/5 text-slate-300 text-[10px] font-bold uppercase tracking-wider border border-white/10">
                        {stageLabel}
                      </span>
                      <span className="text-[11px] text-muted-foreground font-mono">
                        {task.task_id.slice(0, 8)}
                      </span>
                      {task.completed_at && (
                        <span className="text-[11px] text-muted-foreground flex items-center gap-1 ml-auto">
                          <Clock className="w-3 h-3" />
                          {compactDateTime(task.completed_at)}
                        </span>
                      )}
                    </div>
                    <h3 className="text-lg font-semibold text-white truncate group-hover:text-cyan-400 transition-colors">
                      {task.title}
                    </h3>
                    <p className="text-sm text-slate-400 line-clamp-2 leading-relaxed">
                      {task.description}
                    </p>
                  </div>
                  
	                  <div className="flex flex-col items-end gap-2">
                    <div className="flex items-center gap-2">
                      {task.status === "completed" ? (
                        <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 text-xs font-medium border border-emerald-500/20">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          Completed
                        </span>
                      ) : (
                        <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-500/10 text-red-400 text-xs font-medium border border-red-500/20">
                          <XCircle className="w-3.5 h-3.5" />
                          {task.status}
                        </span>
                      )}
                    </div>
	                  </div>
	                </div>

	                {(recap.idea || recap.implemented || recap.success_assessment || task.result_summary) && (
	                  <div className="mt-4 p-3 bg-white/5 rounded-lg border border-white/5">
	                    <div className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold mb-1 flex items-center gap-1.5">
	                      <MessageSquare className="w-3 h-3" />
	                      Evaluated Recap
                        <span className={`ml-2 rounded border px-1.5 py-0.5 tracking-normal ${
                          recapIsLlm
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                            : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                        }`}>
                          {recapStatusLabel}
                        </span>
                        {typeof recap.confidence === "number" && (
                          <span className="ml-auto text-[10px] text-slate-500 tracking-normal">
                            confidence {recap.confidence.toFixed(2)}
                          </span>
                        )}
	                    </div>
                      <div className="space-y-2 text-sm text-slate-300">
                        {recap.idea && <p><span className="text-slate-500">Idea:</span> {recap.idea}</p>}
                        {(recap.implemented || task.result_summary) && <p><span className="text-slate-500">Implemented:</span> {recap.implemented || task.result_summary}</p>}
                        {recap.known_issues && <p><span className="text-slate-500">Known issues:</span> {recap.known_issues}</p>}
                        {recap.success_assessment && <p><span className="text-slate-500">Assessment:</span> {recap.success_assessment}</p>}
                        {recap.recommended_next_action && <p><span className="text-slate-500">Next:</span> {recap.recommended_next_action}</p>}
                      </div>
	                  </div>
	                )}

	                {task.artifacts && task.artifacts.length > 0 && (
	                  <div className="mt-4 space-y-2">
	                    <div className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold">Artifacts</div>
	                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
	                      {task.artifacts.map((artifact) => (
	                        <a
	                          key={artifact.artifact_id}
	                          href={artifact.href || "#"}
	                          target={artifact.href ? "_blank" : undefined}
	                          rel="noopener noreferrer"
	                          className="flex items-center gap-3 p-2.5 rounded-lg bg-card/30 border border-border/40 hover:bg-card/60 hover:border-primary/30 transition-all group/ev"
	                        >
	                          <div className="w-8 h-8 rounded bg-background/60 flex items-center justify-center shrink-0 border border-border/20 group-hover/ev:border-primary/40">
	                            <FileText className="w-4 h-4 text-muted-foreground group-hover/ev:text-primary transition-colors" />
	                          </div>
	                          <div className="min-w-0 flex-1">
	                            <div className="text-xs font-medium text-slate-200 truncate">{artifact.title || artifact.artifact_type}</div>
	                            <div className="text-[10px] text-muted-foreground truncate">{artifact.delivery_state || artifact.status}</div>
	                          </div>
	                          {artifact.href && <ExternalLink className="w-3 h-3 text-muted-foreground group-hover/ev:text-primary" />}
	                        </a>
	                      ))}
	                    </div>
	                  </div>
	                )}

                {task.evidence && task.evidence.length > 0 && (
                  <div className="mt-4 space-y-2">
                    <div className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold">Evidence Items</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {task.evidence.map((ev) => (
                        <a
                          key={ev.evidence_id}
                          href={ev.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-3 p-2.5 rounded-lg bg-card/30 border border-border/40 hover:bg-card/60 hover:border-primary/30 transition-all group/ev"
                        >
                          <div className="w-8 h-8 rounded bg-background/60 flex items-center justify-center shrink-0 border border-border/20 group-hover/ev:border-primary/40">
                            <Tag className="w-4 h-4 text-muted-foreground group-hover/ev:text-primary transition-colors" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-medium text-slate-200 truncate">{ev.title || "Untitled Evidence"}</div>
                            <div className="text-[10px] text-muted-foreground truncate">{ev.url || ev.source_kind}</div>
                          </div>
                          <ExternalLink className="w-3 h-3 text-muted-foreground group-hover/ev:text-primary" />
                        </a>
                      ))}
                    </div>
                  </div>
                )}

                <div className="mt-6 pt-4 border-t border-white/5 flex flex-wrap items-center justify-between gap-4">
	                  <div className="flex items-center gap-3">
	                    {task.session_id && (
	                      <button
	                        onClick={() => rehydrateSession(task)}
                        className="flex items-center gap-2 px-3 py-1.5 bg-cyan-500/10 text-cyan-400 text-xs font-medium rounded-lg border border-cyan-500/20 hover:bg-cyan-500/20 transition-all"
                      >
	                        <ExternalLink className="w-3.5 h-3.5" />
	                        Three-Panel Session
	                      </button>
	                    )}
                      {task.links?.transcript_href && (
                        <a
                          href={task.links.transcript_href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-2 px-3 py-1.5 bg-white/5 text-slate-300 text-xs font-medium rounded-lg border border-white/10 hover:bg-white/10 transition-all"
                        >
                          <FileText className="w-3.5 h-3.5" />
                          Transcript
                        </a>
                      )}
                    <button
                      onClick={() => {
                        setFeedbackOpenId(isFeedbackOpen ? "" : task.task_id);
                        if (feedback) {
                          setFeedbackText(feedback.feedback_text || "");
                          setFeedbackTags(feedback.feedback_tags || []);
                          setSentiment(feedback.sentiment || null);
                        } else {
                          setFeedbackText("");
                          setFeedbackTags([]);
                          setSentiment(null);
                        }
                      }}
                      className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg border transition-all ${
                        isFeedbackOpen || feedback
                          ? "bg-amber-500/10 text-amber-400 border-amber-500/20 hover:bg-amber-500/20"
                          : "bg-white/5 text-slate-400 border-white/10 hover:bg-white/10 hover:text-slate-200"
                      }`}
                    >
                      <MessageSquare className="w-3.5 h-3.5" />
                      {feedback ? "Edit Feedback" : "Leave Feedback"}
                    </button>
                  </div>

                  {feedback && !isFeedbackOpen && (
                    <div className="flex items-center gap-2">
                      {feedback.sentiment === "positive" && <ThumbsUp className="w-4 h-4 text-emerald-400" />}
                      {feedback.sentiment === "negative" && <ThumbsDown className="w-4 h-4 text-red-400" />}
                      <div className="flex gap-1">
                        {feedback.feedback_tags?.map((tag: string) => (
                          <span key={tag} className="px-2 py-0.5 rounded-full bg-white/5 text-[9px] text-muted-foreground border border-white/5 capitalize">
                            {tag.replace("_", " ")}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {isFeedbackOpen && (
                  <div className="mt-4 p-4 rounded-xl bg-background/60 border border-white/10 animate-in slide-in-from-top-2 duration-300">
                    <div className="flex flex-col gap-4">
                      <div>
                        <label className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold mb-2 block">
                          Sentiment
                        </label>
                        <div className="flex gap-2">
                          <button
                            onClick={() => setSentiment("positive")}
                            className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-all ${
                              sentiment === "positive"
                                ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.2)]"
                                : "bg-white/5 border-white/10 text-slate-400 hover:bg-white/10"
                            }`}
                          >
                            <ThumbsUp className="w-4 h-4" />
                            Helpful
                          </button>
                          <button
                            onClick={() => setSentiment("negative")}
                            className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-all ${
                              sentiment === "negative"
                                ? "bg-red-500/20 border-red-500/40 text-red-400 shadow-[0_0_15px_rgba(239,68,68,0.2)]"
                                : "bg-white/5 border-white/10 text-slate-400 hover:bg-white/10"
                            }`}
                          >
                            <ThumbsDown className="w-4 h-4" />
                            Not Helpful
                          </button>
                        </div>
                      </div>

                      <div>
                        <label className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold mb-2 block">
                          Tags
                        </label>
                        <div className="flex flex-wrap gap-2">
                          {FEEDBACK_CHIPS.map((tag) => (
                            <button
                              key={tag}
                              onClick={() => toggleFeedbackTag(tag)}
                              className={`px-3 py-1.5 rounded-lg border text-xs transition-all capitalize ${
                                feedbackTags.includes(tag)
                                  ? "bg-cyan-500/20 border-cyan-500/40 text-cyan-400"
                                  : "bg-white/5 border-white/10 text-slate-400 hover:bg-white/10"
                              }`}
                            >
                              {tag.replace("_", " ")}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <label className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold mb-2 block">
                          Additional Comments
                        </label>
                        <textarea
                          value={feedbackText}
                          onChange={(e) => setFeedbackText(e.target.value)}
                          placeholder="What could be improved? (Optional)"
                          className="w-full bg-black/40 border border-white/10 rounded-lg px-4 py-3 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/50 min-h-[100px] transition-all"
                        />
                      </div>

                      <div className="flex justify-end gap-3 pt-2">
                        <button
                          onClick={() => setFeedbackOpenId("")}
                          className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-slate-200"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={() => submitFeedback(task.task_id)}
                          disabled={busyId === task.task_id}
                          className="px-6 py-2 bg-cyan-500 text-white text-xs font-bold rounded-lg hover:bg-cyan-400 transition-all shadow-[0_4px_20px_rgba(6,182,212,0.3)] disabled:opacity-50"
                        >
                          {busyId === task.task_id ? "Saving..." : "Save Feedback"}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
