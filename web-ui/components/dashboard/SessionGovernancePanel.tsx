"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8002";
const OPS_TOKEN = process.env.NEXT_PUBLIC_UA_OPS_TOKEN;

type SessionSummary = { session_id: string; status: string };

type SessionPolicy = {
  autonomy_mode?: string;
  identity_mode?: string;
  tool_profile?: string;
  memory?: {
    mode?: "off" | "session_only" | "selective" | "full";
    session_memory_enabled?: boolean;
    tags?: string[];
    long_term_tag_allowlist?: string[];
  };
  approvals?: { enabled?: boolean; timeout_hours?: number };
  limits?: { max_runtime_seconds?: number; max_tool_calls?: number };
};

type PendingGate = {
  approval_id?: string;
  status?: string;
  categories?: string[];
  reasons?: string[];
};

function headers(): Record<string, string> {
  return OPS_TOKEN ? { "X-UA-OPS-TOKEN": OPS_TOKEN } : {};
}

export function SessionGovernancePanel() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [policy, setPolicy] = useState<SessionPolicy | null>(null);
  const [pending, setPending] = useState<PendingGate | null>(null);
  const [status, setStatus] = useState<string>("");

  const loadSessions = useCallback(async () => {
    const res = await fetch(`${API_BASE}/api/v1/sessions`);
    const data = await res.json();
    const items = data.sessions || [];
    setSessions(items);
    if (!selectedSession && items.length > 0) {
      setSelectedSession(items[0].session_id);
    }
  }, [selectedSession]);

  const loadPolicy = useCallback(async () => {
    if (!selectedSession) return;
    const [policyRes, pendingRes] = await Promise.all([
      fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(selectedSession)}/policy`),
      fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(selectedSession)}/pending`),
    ]);
    const policyData = await policyRes.json();
    const pendingData = await pendingRes.json();
    setPolicy(policyData.policy || null);
    setPending(pendingData.pending || null);
  }, [selectedSession]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadSessions();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadSessions]);

  useEffect(() => {
    if (!selectedSession) return;
    const timer = window.setTimeout(() => {
      void loadPolicy();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [selectedSession, loadPolicy]);

  const savePatch = useCallback(
    async (patch: Record<string, unknown>) => {
      if (!selectedSession) return;
      setStatus("Saving...");
      const res = await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(selectedSession)}/policy`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch }),
      });
      if (!res.ok) {
        setStatus(`Save failed (${res.status})`);
        return;
      }
      const data = await res.json();
      setPolicy(data.policy || null);
      setStatus("Saved");
    },
    [selectedSession],
  );

  const resumePending = useCallback(async () => {
    if (!selectedSession || !pending) return;
    setStatus("Approving gate...");
    const approvalId = pending.approval_id;
    if (approvalId) {
      await fetch(`${API_BASE}/api/v1/ops/approvals/${encodeURIComponent(approvalId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({ status: "approved", notes: "Approved from dashboard governance panel" }),
      });
    }
    await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(selectedSession)}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approval_id: approvalId }),
    });
    setStatus("Gate approved. Send 'resume' in Chat to continue.");
    await loadPolicy();
  }, [selectedSession, pending, loadPolicy]);

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-300">Session Governance</h2>
        <button
          type="button"
          onClick={loadSessions}
          className="rounded-md border border-slate-700 bg-slate-800/60 px-2 py-1 text-xs hover:bg-slate-800"
        >
          Refresh Sessions
        </button>
      </div>

      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-400">
          Session
          <select
            className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
            value={selectedSession}
            onChange={(e) => setSelectedSession(e.target.value)}
          >
            {sessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {session.session_id}
              </option>
            ))}
          </select>
        </label>
      </div>

      {policy && (
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">
            Autonomy Mode
            <select
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
              value={policy.autonomy_mode || "yolo"}
              onChange={(e) => savePatch({ autonomy_mode: e.target.value })}
            >
              <option value="yolo">yolo</option>
              <option value="guarded">guarded</option>
              <option value="locked">locked</option>
            </select>
          </label>

          <label className="text-xs text-slate-400">
            Identity Mode
            <select
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
              value={policy.identity_mode || "persona"}
              onChange={(e) => savePatch({ identity_mode: e.target.value })}
            >
              <option value="persona">persona</option>
              <option value="operator_proxy">operator_proxy</option>
            </select>
          </label>

          <label className="text-xs text-slate-400">
            Tool Profile
            <select
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
              value={policy.tool_profile || "full"}
              onChange={(e) => savePatch({ tool_profile: e.target.value })}
            >
              <option value="full">full</option>
              <option value="safe">safe</option>
            </select>
          </label>

          <label className="text-xs text-slate-400">
            Memory Mode
            <select
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
              value={policy.memory?.mode || "session_only"}
              onChange={(e) => savePatch({ memory: { ...(policy.memory || {}), mode: e.target.value } })}
            >
              <option value="off">off</option>
              <option value="session_only">session_only</option>
              <option value="selective">selective</option>
              <option value="full">full</option>
            </select>
          </label>

          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={policy.memory?.session_memory_enabled !== false}
              onChange={(e) =>
                savePatch({
                  memory: {
                    ...(policy.memory || {}),
                    session_memory_enabled: e.target.checked,
                  },
                })
              }
            />
            Session Memory Enabled
          </label>

          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(policy.approvals?.enabled)}
              onChange={(e) => savePatch({ approvals: { ...(policy.approvals || {}), enabled: e.target.checked } })}
            />
            Approvals Enabled
          </label>

          <label className="text-xs text-slate-400 md:col-span-2">
            Memory Tags (comma-separated)
            <div className="mt-1">
              <input
                key={`memory-tags-${selectedSession}-${(policy.memory?.tags || []).join(",")}`}
                className="w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
                defaultValue={(policy.memory?.tags || []).join(", ")}
                onBlur={(e) =>
                  savePatch({
                    memory: {
                      ...(policy.memory || {}),
                      tags: e.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                    },
                  })
                }
                placeholder="dev_test,retain"
              />
            </div>
          </label>

          <label className="text-xs text-slate-400 md:col-span-2">
            Long-term Allowlist (selective mode)
            <div className="mt-1">
              <input
                key={`memory-allow-${selectedSession}-${(policy.memory?.long_term_tag_allowlist || []).join(",")}`}
                className="w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 py-2 text-sm"
                defaultValue={(policy.memory?.long_term_tag_allowlist || []).join(", ")}
                onBlur={(e) =>
                  savePatch({
                    memory: {
                      ...(policy.memory || {}),
                      long_term_tag_allowlist: e.target.value
                        .split(",")
                        .map((item) => item.trim())
                        .filter(Boolean),
                    },
                  })
                }
                placeholder="retain"
              />
            </div>
          </label>
        </div>
      )}

      {pending && (
        <div className="mt-4 rounded-lg border border-amber-700/70 bg-amber-900/20 p-3 text-sm text-amber-100">
          <p className="font-semibold">Pending Gate: {pending.status || "pending"}</p>
          <p className="mt-1 text-xs text-amber-200">Approval ID: {pending.approval_id || "n/a"}</p>
          {pending.categories && pending.categories.length > 0 && (
            <p className="mt-1 text-xs text-amber-200">Categories: {pending.categories.join(", ")}</p>
          )}
          <button
            type="button"
            onClick={resumePending}
            className="mt-2 rounded-md border border-amber-600 bg-amber-500/20 px-2 py-1 text-xs hover:bg-amber-500/30"
          >
            Approve Gate
          </button>
        </div>
      )}

      {status && <p className="mt-3 text-xs text-slate-400">{status}</p>}
    </section>
  );
}
