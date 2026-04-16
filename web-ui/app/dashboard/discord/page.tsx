"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { formatDateTimeTz } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";
const TIER_OPTIONS = ["A", "B", "C", "D", "MUTED"];

type DiscordOverview = {
  status: string;
  counts?: Record<string, number>;
  signal_breakdown_24h?: Array<Record<string, unknown>>;
  insight_breakdown_24h?: Array<Record<string, unknown>>;
};

type DiscordEvent = {
  id: string;
  server_id?: string;
  name: string;
  server_name?: string;
  channel_name?: string;
  entity_type?: string;
  status?: string;
  start_time?: string;
  end_time?: string;
  location?: string;
  description?: string;
  calendar_sync_status?: string;
  calendar_event_id?: string;
  calendar_sync_error?: string;
  discord_event_url?: string;
};

type DiscordChannel = {
  id: string;
  server_id: string;
  server_name?: string;
  name: string;
  category?: string;
  tier?: string;
  is_active?: number | boolean;
  messages_total?: number;
  unprocessed_total?: number;
  signals_total?: number;
  last_message_at?: string;
};

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function eventLink(event: DiscordEvent): string {
  if (event.discord_event_url) return event.discord_event_url;
  if (event.server_id && event.id && !event.id.startsWith("text_evt_")) return `https://discord.com/events/${event.server_id}/${event.id}`;
  return "";
}

export default function DiscordIntelPage() {
  const [overview, setOverview] = useState<DiscordOverview | null>(null);
  const [events, setEvents] = useState<DiscordEvent[]>([]);
  const [channels, setChannels] = useState<DiscordChannel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [updatingChannelId, setUpdatingChannelId] = useState("");
  const [deletingEventId, setDeletingEventId] = useState("");
  const [clearingAll, setClearingAll] = useState(false);
  const [hoveredEventId, setHoveredEventId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [overviewRes, eventsRes, channelsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/discord/overview`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/discord/events?limit=10`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/discord/channels?limit=1500`, { cache: "no-store" }),
      ]);
      if (!overviewRes.ok || !eventsRes.ok || !channelsRes.ok) {
        throw new Error(`Discord dashboard request failed (${overviewRes.status}/${eventsRes.status}/${channelsRes.status})`);
      }
      const [overviewJson, eventsJson, channelsJson] = await Promise.all([
        overviewRes.json(),
        eventsRes.json(),
        channelsRes.json(),
      ]);
      setOverview(overviewJson as DiscordOverview);
      setEvents(Array.isArray(eventsJson.events) ? eventsJson.events : []);
      setChannels(Array.isArray(channelsJson.channels) ? channelsJson.channels : []);
    } catch (err) {
      setError((err as Error).message || "Failed to load Discord intelligence.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groupedChannels = useMemo(() => {
    const groups = new Map<string, DiscordChannel[]>();
    for (const channel of channels) {
      const key = `${channel.server_name || "Unknown Server"} / ${channel.category || "Uncategorized"}`;
      const rows = groups.get(key) || [];
      rows.push(channel);
      groups.set(key, rows);
    }
    return [...groups.entries()].map(([label, rows]) => ({
      label,
      rows: rows.sort((a, b) => asNumber(b.messages_total) - asNumber(a.messages_total)),
      messages: rows.reduce((sum, row) => sum + asNumber(row.messages_total), 0),
      signals: rows.reduce((sum, row) => sum + asNumber(row.signals_total), 0),
      unprocessed: rows.reduce((sum, row) => sum + asNumber(row.unprocessed_total), 0),
    })).sort((a, b) => b.messages - a.messages);
  }, [channels]);

  const updateChannel = useCallback(async (channelId: string, patch: Record<string, unknown>) => {
    setUpdatingChannelId(channelId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/discord/channels/${encodeURIComponent(channelId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(`Update failed (${res.status})`);
      await load();
    } catch (err) {
      setError((err as Error).message || "Failed to update channel.");
    } finally {
      setUpdatingChannelId("");
    }
  }, [load]);

  const deleteEvent = useCallback(async (eventId: string) => {
    setDeletingEventId(eventId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/discord/events/${encodeURIComponent(eventId)}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      setEvents((prev) => prev.filter((e) => e.id !== eventId));
    } catch (err) {
      setError((err as Error).message || "Failed to delete event.");
    } finally {
      setDeletingEventId("");
    }
  }, []);

  const clearAllEvents = useCallback(async () => {
    if (!window.confirm("Delete all structured events? This cannot be undone.")) return;
    setClearingAll(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/discord/events`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Clear all failed (${res.status})`);
      setEvents([]);
    } catch (err) {
      setError((err as Error).message || "Failed to clear all events.");
    } finally {
      setClearingAll(false);
    }
  }, []);

  const counts = overview?.counts || {};

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Discord Intelligence</h1>
          <p className="text-sm text-muted-foreground">Structured events, signal volume, and channel tuning for the Discord firehose.</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-md border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card disabled:opacity-60"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          {error}
        </div>
      )}

      <section className="grid gap-3 md:grid-cols-4">
        {[
          ["Messages 24h", counts.messages_24h],
          ["Signals 24h", counts.signals_24h],
          ["Unprocessed", counts.unprocessed_messages],
          ["Upcoming events", counts.upcoming_structured_events],
        ].map(([label, value]) => (
          <div key={String(label)} className="border border-border bg-card/30 p-4">
            <p className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">{label}</p>
            <p className="mt-2 text-2xl font-semibold text-foreground">{asNumber(value)}</p>
          </div>
        ))}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-primary">Top 10 Structured Events</h2>
          {events.length > 0 && (
            <button
              type="button"
              id="discord-clear-all-events"
              onClick={clearAllEvents}
              disabled={clearingAll}
              className="rounded border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs text-red-300 hover:bg-red-500/20 hover:text-red-200 disabled:opacity-50 transition-colors"
            >
              {clearingAll ? "Clearing..." : "Clear All"}
            </button>
          )}
        </div>
        <div className="grid gap-3">
          {events.length === 0 && <div className="border border-border bg-card/20 p-4 text-sm text-muted-foreground">No upcoming structured events.</div>}
          {events.map((event) => {
            const link = eventLink(event);
            const isDeleting = deletingEventId === event.id;
            const isHovered = hoveredEventId === event.id;
            return (
              <article
                key={event.id}
                id={`discord-event-${event.id}`}
                className="relative border border-border bg-card/30 p-4 transition-colors"
                onMouseEnter={() => setHoveredEventId(event.id)}
                onMouseLeave={() => setHoveredEventId("")}
              >
                {/* Hover delete button */}
                {(isHovered || isDeleting) && (
                  <button
                    type="button"
                    onClick={() => deleteEvent(event.id)}
                    disabled={isDeleting}
                    aria-label={`Delete event ${event.name}`}
                    className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded border border-red-500/30 bg-red-500/10 text-red-300 hover:bg-red-500/25 hover:text-red-200 disabled:opacity-50 transition-colors"
                    title="Delete event"
                  >
                    {isDeleting ? (
                      <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                      </svg>
                    ) : (
                      <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    )}
                  </button>
                )}

                <div className="flex flex-wrap items-start justify-between gap-3 pr-8">
                  <div>
                    <h3 className="font-semibold text-foreground">{event.name}</h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {event.server_name || "Unknown server"} · {event.channel_name || event.location || event.entity_type || "event"} · {formatDateTimeTz(event.start_time)}
                    </p>
                  </div>
                  <span className="rounded border border-primary/20 bg-primary/10 px-2 py-1 text-[11px] uppercase tracking-[0.12em] text-primary">
                    {event.calendar_sync_status || "pending"}
                  </span>
                </div>
                {event.description && <p className="mt-3 line-clamp-3 text-sm text-muted-foreground">{event.description}</p>}
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  {link && (
                    <a className="text-primary hover:underline" href={link} target="_blank" rel="noreferrer">
                      Open Discord event
                    </a>
                  )}
                  {event.calendar_sync_error && <span className="text-red-300">Calendar error: {event.calendar_sync_error}</span>}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-primary">Channel Tuning</h2>
        <div className="space-y-2">
          {groupedChannels.map((group) => (
            <details key={group.label} className="border border-border bg-card/20">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-foreground">
                {group.label} · {group.rows.length} channels · {group.messages} msgs · {group.signals} signals · {group.unprocessed} unprocessed
              </summary>
              <div className="divide-y divide-border">
                {group.rows.map((channel) => (
                  <div key={channel.id} className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_auto_auto] md:items-center">
                    <div className="min-w-0">
                      <p className="truncate text-sm text-foreground">#{channel.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {asNumber(channel.messages_total)} messages · {asNumber(channel.signals_total)} signals · {asNumber(channel.unprocessed_total)} unprocessed · last {formatDateTimeTz(channel.last_message_at)}
                      </p>
                    </div>
                    <select
                      value={channel.tier || "C"}
                      onChange={(event) => updateChannel(channel.id, { tier: event.target.value })}
                      disabled={updatingChannelId === channel.id}
                      className="rounded border border-border bg-background px-2 py-1 text-xs"
                    >
                      {TIER_OPTIONS.map((tier) => <option key={tier} value={tier}>{tier}</option>)}
                    </select>
                    <button
                      type="button"
                      onClick={() => updateChannel(channel.id, { is_active: !Boolean(channel.is_active) })}
                      disabled={updatingChannelId === channel.id}
                      className="rounded border border-border bg-card/60 px-2 py-1 text-xs hover:bg-card disabled:opacity-60"
                    >
                      {Boolean(channel.is_active) ? "Mute" : "Unmute"}
                    </button>
                  </div>
                ))}
              </div>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
