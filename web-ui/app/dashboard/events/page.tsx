"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { formatTimeTz } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";

type SessionSummary = {
  session_id: string;
  status?: string;
};

type SystemEventItem = {
  event_id?: string;
  type?: string;
  created_at?: string;
  payload?: Record<string, unknown>;
};

export default function DashboardEventsPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [events, setEvents] = useState<SystemEventItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [error, setError] = useState("");

  const loadSessions = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/ops/sessions`);
      if (!response.ok) return;
      const data = await response.json();
      const rows = Array.isArray(data.sessions) ? (data.sessions as SessionSummary[]) : [];
      setSessions(rows);
      setSessionId((prev) => prev || rows[0]?.session_id || "");
    } catch {
      // no-op; page still usable with prior state
    }
  }, []);

  const loadEvents = useCallback(async (targetSessionId: string) => {
    if (!targetSessionId) {
      setEvents([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/v1/system/events?session_id=${encodeURIComponent(targetSessionId)}`);
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(detail || `Events request failed (${response.status})`);
      }
      const data = await response.json();
      const rows = Array.isArray(data.events) ? (data.events as SystemEventItem[]) : [];
      setEvents(rows);
      setSelectedEventId((prev) => prev || rows[0]?.event_id || "");
    } catch (err: any) {
      setError(err?.message || "Failed to load events");
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    void loadEvents(sessionId);
    const timer = window.setInterval(() => {
      void loadEvents(sessionId);
    }, 8000);
    return () => window.clearInterval(timer);
  }, [sessionId, loadEvents]);

  const filteredEvents = useMemo(() => {
    const sorted = [...events].sort((a, b) => {
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return tb - ta;
    });
    const q = eventTypeFilter.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((ev) => (ev.type || "").toLowerCase().includes(q));
  }, [events, eventTypeFilter]);

  const selectedEvent = useMemo(
    () => filteredEvents.find((ev) => ev.event_id === selectedEventId) ?? null,
    [filteredEvents, selectedEventId],
  );

  return (
    <div className="flex h-full flex-col space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Events</h1>
          <p className="text-sm text-slate-400">Session-level system events with payload inspection.</p>
        </div>
        <Link
          href="/"
          className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
        >
          Back to Home
        </Link>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px] min-w-[260px]"
          >
            <option value="">Select session</option>
            {sessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>{session.session_id}</option>
            ))}
          </select>
          <input
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
            placeholder="Filter event type"
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px]"
          />
          <button
            type="button"
            onClick={() => void loadEvents(sessionId)}
            className="rounded border border-border/60 bg-card/40 px-2 py-1 text-[12px] hover:bg-card/60"
          >
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
        {error && <div className="mb-2 text-sm text-rose-300">{error}</div>}
        <div className="grid min-h-0 gap-3 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-1 max-h-[62vh] overflow-y-auto scrollbar-thin">
            {filteredEvents.length === 0 && (
              <div className="text-sm text-slate-400 rounded border border-slate-800 bg-slate-950/40 px-3 py-4">
                No events found for this session.
              </div>
            )}
            {filteredEvents.map((ev) => {
              const id = ev.event_id || `${ev.type || "event"}-${ev.created_at || "unknown"}`;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSelectedEventId(ev.event_id || "")}
                  className={`w-full rounded border px-2 py-2 text-left ${selectedEventId === ev.event_id ? "border-cyan-500/60 bg-cyan-500/10" : "border-slate-800 bg-slate-950/40 hover:bg-slate-900/60"}`}
                >
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>{ev.type || "system_event"}</span>
                    <span>{formatTimeTz(ev.created_at, { placeholder: "--:--:--" })}</span>
                  </div>
                  <div className="mt-1 text-[11px] font-mono text-slate-300 truncate">
                    {Object.keys(ev.payload || {}).join(", ") || "(no payload fields)"}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="rounded border border-slate-800 bg-slate-950/50 p-2 min-h-[220px]">
            <div className="mb-2 text-[11px] uppercase tracking-wider text-slate-400">Raw Payload</div>
            {selectedEvent ? (
              <pre className="max-h-[56vh] overflow-auto whitespace-pre-wrap font-mono text-[11px] text-slate-200">
                {JSON.stringify(selectedEvent.payload || {}, null, 2)}
              </pre>
            ) : (
              <div className="text-sm text-slate-500">Select an event to inspect payload.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
