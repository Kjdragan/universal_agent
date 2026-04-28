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
  Tag
} from "lucide-react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";

const API_BASE = "/api/dashboard/gateway";
const FEEDBACK_CHIPS = [
  "successful",
  "failed",
  "too_slow",
  "unexpected",
  "helpful",
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

type TaskHistoryItem = {
  task_id: string;
  source_kind: string;
  title: string;
  description: string;
  status: string;
  result_summary?: string;
  session_id?: string;
  run_id?: string;
  completed_at?: string;
  feedback_json?: string;
  evidence: TaskEvidence[];
};

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
    } catch (err) {
      setError((err as Error).message || "Failed to load task history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
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
      attachMode: "tail",
      role: "viewer"
    });
  };

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <History className="w-6 h-6 text-cyan-400" />
            Proactive Task History
          </h1>
          <p className="text-muted-foreground mt-1">
            Audit system-driven agent tasks and provide feedback to improve the model.
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

      {!loading && tasks.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 border border-dashed border-border rounded-xl bg-card/10">
          <Clock className="w-12 h-12 text-muted-foreground/30 mb-4" />
          <p className="text-muted-foreground">No completed proactive tasks found.</p>
        </div>
      )}

      <div className="grid gap-4">
        {tasks.map((task) => {
          const isFeedbackOpen = feedbackOpenId === task.task_id;
          const feedback = task.feedback_json ? JSON.parse(task.feedback_json) : null;

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

                {task.result_summary && (
                  <div className="mt-4 p-3 bg-white/5 rounded-lg border border-white/5">
                    <div className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold mb-1 flex items-center gap-1.5">
                      <MessageSquare className="w-3 h-3" />
                      Result Summary
                    </div>
                    <p className="text-sm text-slate-300 italic">"{task.result_summary}"</p>
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
                        Rehydrate Workspace
                      </button>
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
