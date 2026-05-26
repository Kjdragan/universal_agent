"use client";

/**
 * GoalArtifactsPanel — surface a Task Hub card's /goal-flow artifacts inline.
 *
 * Renders the four standard self-briefing artifacts (BRIEF.md, ACCEPTANCE.md,
 * goal_condition.txt, COMPLETION.md) plus the operator's original prompt, so
 * the operator can trace user_prompt → BRIEF → ACCEPTANCE → goal condition →
 * COMPLETION without leaving the dashboard.
 *
 * Backed by GET /api/v1/dashboard/todolist/tasks/{task_id}/goal-artifacts
 * (see gateway_server.dashboard_todolist_get_goal_artifacts).
 */

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

type ArtifactFile = {
  path: string;
  size_bytes?: number;
  truncated?: boolean;
  content?: string | null;
  error?: string;
} | null;

type GoalArtifactsPayload = {
  task_id: string;
  use_goal_loop: boolean;
  target_agent: string;
  original_prompt: {
    title: string;
    description: string;
  };
  linked_mission_id: string | null;
  linked_mission_status: string | null;
  workspace_path: string | null;
  artifacts: {
    "BRIEF.md": ArtifactFile;
    "ACCEPTANCE.md": ArtifactFile;
    "goal_condition.txt": ArtifactFile;
    "COMPLETION.md": ArtifactFile;
  };
};

type Props = {
  taskId: string;
  /** Skip the fetch entirely if the operator hasn't opened the panel yet. */
  expanded: boolean;
};

type ToggleProps = {
  taskId: string;
  /** Compact label used in the toggle button (e.g. "View /goal flow"). */
  label?: string;
};

/**
 * GoalArtifactsToggle — self-contained inline expander for the artifacts panel.
 *
 * Use this in card render contexts where adding `useState` inline would be
 * awkward (e.g., inside a `.map()` callback). The toggle owns the expanded
 * state and renders the panel only when opened.
 */
export function GoalArtifactsToggle({ taskId, label = "View /goal flow artifacts" }: ToggleProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        className="font-mono text-[10px] text-kcd-cyan hover:text-kcd-cyan/80 transition-colors cursor-pointer"
      >
        {expanded ? "▼" : "▶"} {label}
      </button>
      <GoalArtifactsPanel taskId={taskId} expanded={expanded} />
    </div>
  );
}

const ARTIFACT_ORDER: Array<keyof GoalArtifactsPayload["artifacts"]> = [
  "BRIEF.md",
  "ACCEPTANCE.md",
  "goal_condition.txt",
  "COMPLETION.md",
];

const ARTIFACT_LABELS: Record<keyof GoalArtifactsPayload["artifacts"], string> = {
  "BRIEF.md": "BRIEF — Cody's interpretation",
  "ACCEPTANCE.md": "ACCEPTANCE — verifiable success criteria",
  "goal_condition.txt": "goal_condition — Haiku-evaluator-readable",
  "COMPLETION.md": "COMPLETION — self-attestation",
};

export function GoalArtifactsPanel({ taskId, expanded }: Props) {
  const [data, setData] = useState<GoalArtifactsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [openFile, setOpenFile] = useState<string | null>(null);

  useEffect(() => {
    if (!expanded || !taskId) return;
    if (data) return; // Don't re-fetch
    setLoading(true);
    fetch(`${API_BASE}/api/v1/dashboard/todolist/tasks/${encodeURIComponent(taskId)}/goal-artifacts`, {
      cache: "no-store",
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((payload: GoalArtifactsPayload) => {
        setData(payload);
        setError(null);
      })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [taskId, expanded, data]);

  if (!expanded) return null;

  if (loading && !data) {
    return (
      <div className="mt-2 text-[11px] text-kcd-text-muted font-mono">Loading /goal artifacts...</div>
    );
  }
  if (error) {
    return (
      <div className="mt-2 text-[11px] text-red-400 font-mono">Failed to load artifacts: {error}</div>
    );
  }
  if (!data) return null;

  return (
    <div className="mt-2 border border-kcd-border rounded p-2 bg-black/30 text-[11px]">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-kcd-cyan font-mono uppercase tracking-wider text-[10px]">
            /goal flow
          </span>
          {data.linked_mission_id ? (
            <span className="font-mono text-[10px] text-kcd-text-muted">
              mission: <span className="text-kcd-text">{data.linked_mission_id.slice(0, 24)}...</span>
              {data.linked_mission_status && <span className="ml-1 text-kcd-cyan">({data.linked_mission_status})</span>}
            </span>
          ) : (
            <span className="font-mono text-[10px] text-kcd-text-muted">no mission dispatched yet</span>
          )}
        </div>
        {data.target_agent && (
          <span className="font-mono text-[10px] text-kcd-text-muted">→ {data.target_agent}</span>
        )}
      </div>

      {/* Progression: user prompt → 4 artifacts */}
      <div className="space-y-1">
        {/* Original prompt */}
        <button
          type="button"
          onClick={() => setOpenFile(openFile === "PROMPT" ? null : "PROMPT")}
          className="w-full text-left flex items-center justify-between px-1.5 py-1 rounded hover:bg-kcd-border/30 transition-colors"
        >
          <span className="font-mono text-[10px] text-kcd-text">
            <span className="text-kcd-text-muted mr-2">①</span>
            ORIGINAL PROMPT — operator's objective
          </span>
          <span className="font-mono text-[9px] text-kcd-text-muted">
            {data.original_prompt.description.length} chars {openFile === "PROMPT" ? "▼" : "▶"}
          </span>
        </button>
        {openFile === "PROMPT" && (
          <pre className="mt-1 p-2 bg-black/50 border border-kcd-border rounded font-mono text-[10px] whitespace-pre-wrap break-words text-kcd-text max-h-64 overflow-auto">
            {data.original_prompt.title && (
              <div className="text-kcd-cyan mb-1"># {data.original_prompt.title}</div>
            )}
            {data.original_prompt.description || "(no description)"}
          </pre>
        )}

        {/* The four artifacts in canonical order */}
        {ARTIFACT_ORDER.map((name, idx) => {
          const file = data.artifacts[name];
          const numeral = ["②", "③", "④", "⑤"][idx];
          const hasFile = file && file.content;
          return (
            <div key={name}>
              <button
                type="button"
                onClick={() => hasFile && setOpenFile(openFile === name ? null : name)}
                disabled={!hasFile}
                className={`w-full text-left flex items-center justify-between px-1.5 py-1 rounded transition-colors ${
                  hasFile ? "hover:bg-kcd-border/30 cursor-pointer" : "cursor-not-allowed opacity-50"
                }`}
              >
                <span className="font-mono text-[10px] text-kcd-text">
                  <span className="text-kcd-text-muted mr-2">{numeral}</span>
                  <span className={hasFile ? "text-kcd-text" : "text-kcd-text-muted"}>
                    {name}
                  </span>
                  <span className="ml-2 text-kcd-text-muted">— {ARTIFACT_LABELS[name]}</span>
                </span>
                <span className="font-mono text-[9px] text-kcd-text-muted">
                  {!file && "(not produced)"}
                  {file?.error && `error: ${file.error}`}
                  {file?.size_bytes != null && `${file.size_bytes} B`}
                  {file?.truncated && " · truncated"}
                  {hasFile && (openFile === name ? " ▼" : " ▶")}
                </span>
              </button>
              {openFile === name && hasFile && (
                <pre className="mt-1 p-2 bg-black/50 border border-kcd-border rounded font-mono text-[10px] whitespace-pre-wrap break-words text-kcd-text max-h-96 overflow-auto">
                  {file.content}
                </pre>
              )}
            </div>
          );
        })}
      </div>

      {/* Workspace pointer for operator deep-dive */}
      {data.workspace_path && (
        <div className="mt-2 pt-2 border-t border-kcd-border/50 font-mono text-[9px] text-kcd-text-muted">
          workspace: <span className="text-kcd-text">{data.workspace_path}</span>
        </div>
      )}
    </div>
  );
}

/**
 * GoalBadge — small "/goal active" pill rendered next to the task title.
 * Visible only when the task is /goal-enabled (operator-dispatched Cody or
 * eligible source_kind). Operators learn at a glance which tasks are running
 * under the autonomous evaluator loop.
 */
export function GoalBadge({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-kcd-cyan/15 border border-kcd-cyan/40 text-kcd-cyan font-mono text-[9px] uppercase tracking-wider">
      <span>🎯</span>
      <span>/goal</span>
    </span>
  );
}
