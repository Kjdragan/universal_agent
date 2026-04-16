"use client";
// Discord message API calls → /api/dashboard/gateway/ (injects session auth)
// CSI watchlist API calls   → /api/v1/csi/discord      (Next.js route, no auth needed)

import { useState, useEffect, useCallback, useRef } from "react";
import {
  MessageSquare, Plus, Loader2, RefreshCw, Edit2, Trash2,
  Settings2, X, Hash, Bot, Paperclip, AlertTriangle,
  ChevronRight, Server, FolderPlus, Check, Inbox,
  CalendarDays, MapPin, Radio, Clock, ChevronDown,
} from "lucide-react";

/* ─── Types ──────────────────────────────────────────────────────────── */

type SubChannel = { channel_id: string; channel_name: string; is_watched: boolean };
type DiscordServer = {
  server_id: string; server_name: string; domain: string;
  icon_url: string; channels: SubChannel[];
};
type WatchlistResponse = { categories: string[]; servers: DiscordServer[] };

type Signal = { rule: string; severity: "high" | "medium" | "low" | string };
type Message = {
  id: string; channel_id: string; channel_name?: string;
  author_name: string; content: string; timestamp: string;
  is_bot: boolean; has_attachments: boolean; processed_by_triage: boolean;
  signals: Signal[];
};

type DiscordEvent = {
  id: string;
  server_id: string;
  server_name?: string;
  name: string;
  description?: string;
  start_time: string;
  end_time?: string;
  location?: string;
  status: string; // "scheduled" | "active" | "completed" | "canceled"
  channel_name?: string;
  notified?: boolean;
};

const GATEWAY = "/api/dashboard/gateway"; // proxy that injects session auth

/* ─── Helpers ─────────────────────────────────────────────────────────── */

function initials(name: string) { return name.slice(0, 2).toUpperCase(); }

function fmtTime(ts: string) {
  try {
    const d = new Date(ts);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffH = diffMs / 3_600_000;
    if (diffH < 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (diffH < 168) return d.toLocaleDateString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch { return ts; }
}

function fmtEventTime(ts: string) {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
  } catch { return ts; }
}

function fmtEventDate(ts: string) {
  try {
    const d = new Date(ts);
    return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  } catch { return ts; }
}

/** Returns a human-readable day label: "Today", "Tomorrow", "April 18", etc. */
function dayLabel(ts: string): string {
  try {
    const d = new Date(ts);
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const evtStart = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diffDays = Math.round((evtStart.getTime() - todayStart.getTime()) / 86_400_000);
    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Tomorrow";
    if (diffDays < 7) return d.toLocaleDateString([], { weekday: "long" });
    return d.toLocaleDateString([], { month: "long", day: "numeric" });
  } catch { return ts; }
}

function dayKey(ts: string): string {
  try {
    const d = new Date(ts);
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  } catch { return ts; }
}

function isLiveNow(evt: DiscordEvent): boolean {
  if (evt.status === "active") return true;
  try {
    const now = Date.now();
    const start = new Date(evt.start_time).getTime();
    const end = evt.end_time ? new Date(evt.end_time).getTime() : start + 3_600_000;
    return now >= start && now <= end;
  } catch { return false; }
}

const AVATAR_COLORS = [
  "bg-indigo-500", "bg-violet-500", "bg-blue-500", "bg-emerald-500",
  "bg-rose-500", "bg-amber-500", "bg-cyan-500", "bg-pink-500",
];
function avatarColor(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffffffff;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

const SEV_STYLES: Record<string, string> = {
  high: "bg-red-500/20 text-red-300 border-red-500/30",
  medium: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  low: "bg-sky-500/20 text-sky-300 border-sky-500/30",
};

type Tab = "messages" | "events";

/* ─── Events Panel ────────────────────────────────────────────────────── */

function EventsPanel() {
  const [events, setEvents] = useState<DiscordEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<DiscordEvent | null>(null);
  const [expandedDays, setExpandedDays] = useState<Set<string>>(new Set());
  const detailRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const r = await fetch(`${GATEWAY}/api/v1/dashboard/discord/events?limit=100`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setEvents(d.events ?? []);
    } catch (e: any) {
      setError(e.message ?? "Failed to load events");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Close detail panel on outside click
  useEffect(() => {
    if (!selectedEvent) return;
    const handler = (e: MouseEvent) => {
      if (detailRef.current && !detailRef.current.contains(e.target as Node)) setSelectedEvent(null);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [selectedEvent]);

  // Group events by day
  const grouped: Array<{ key: string; label: string; events: DiscordEvent[] }> = [];
  const seenKeys = new Map<string, number>();
  for (const evt of events) {
    const k = dayKey(evt.start_time);
    if (!seenKeys.has(k)) {
      seenKeys.set(k, grouped.length);
      grouped.push({ key: k, label: dayLabel(evt.start_time), events: [] });
    }
    grouped[seenKeys.get(k)!].events.push(evt);
  }

  // Auto-expand today and tomorrow
  useEffect(() => {
    if (grouped.length === 0 || expandedDays.size > 0) return;
    const toExpand = new Set<string>();
    grouped.slice(0, 2).forEach(g => toExpand.add(g.key));
    setExpandedDays(toExpand);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  const toggleDay = (key: string) => {
    setExpandedDays(prev => {
      const n = new Set(prev);
      n.has(key) ? n.delete(key) : n.add(key);
      return n;
    });
  };

  const liveCount = events.filter(isLiveNow).length;

  return (
    <div className="flex flex-1 min-h-0 relative overflow-hidden">
      {/* ── Event list column ── */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* Sub-header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-black/30 bg-[#2b2d31] shrink-0">
          <div className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4 text-indigo-400" />
            <span className="text-sm font-semibold text-white">Upcoming Events</span>
            {liveCount > 0 && (
              <span className="flex items-center gap-1 rounded-full bg-red-500/20 border border-red-500/40 px-2 py-0.5 text-[10px] font-semibold text-red-300">
                <Radio className="h-2.5 w-2.5 animate-pulse" />
                {liveCount} LIVE
              </span>
            )}
          </div>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-border/30 bg-background/30 px-2.5 py-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-background/60 transition disabled:opacity-40"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>

        {/* List body */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm text-red-400">{error}</p>
            </div>
          ) : events.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground/40 space-y-3 p-10">
              <CalendarDays className="h-12 w-12 text-border" />
              <div>
                <p className="text-sm">No upcoming events</p>
                <p className="text-xs mt-1">The daemon will populate events as servers schedule them</p>
              </div>
            </div>
          ) : (
            <div className="py-2">
              {grouped.map(group => {
                const isExpanded = expandedDays.has(group.key);
                const hasLive = group.events.some(isLiveNow);
                return (
                  <div key={group.key} className="mb-1">
                    {/* Day header */}
                    <button
                      onClick={() => toggleDay(group.key)}
                      className="w-full flex items-center gap-2 px-4 py-2 text-left hover:bg-white/[0.03] transition-colors group"
                    >
                      <ChevronDown
                        className={`h-3.5 w-3.5 text-muted-foreground/50 transition-transform shrink-0 ${isExpanded ? "" : "-rotate-90"}`}
                      />
                      <span className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground/60 group-hover:text-muted-foreground transition flex-1">
                        {group.label}
                      </span>
                      {hasLive && (
                        <span className="text-[9px] font-bold text-red-400 uppercase tracking-wider flex items-center gap-1">
                          <Radio className="h-2 w-2 animate-pulse" />LIVE
                        </span>
                      )}
                      <span className="text-[10px] text-muted-foreground/40">
                        {group.events.length} event{group.events.length !== 1 ? "s" : ""}
                      </span>
                    </button>

                    {/* Event rows */}
                    {isExpanded && group.events.map(evt => {
                      const live = isLiveNow(evt);
                      const canceled = evt.status === "canceled" || evt.status === "cancelled";
                      const isSelected = selectedEvent?.id === evt.id;
                      return (
                        <button
                          key={evt.id}
                          onClick={() => setSelectedEvent(isSelected ? null : evt)}
                          className={`w-full flex items-stretch gap-0 text-left px-4 py-0 hover:bg-white/[0.04] transition-colors
                            ${isSelected ? "bg-indigo-500/10" : ""}
                            ${canceled ? "opacity-50" : ""}`}
                        >
                          {/* Time column */}
                          <div className="w-16 shrink-0 flex flex-col items-end justify-center pr-3 py-3 border-r border-border/20">
                            <span className={`text-[11px] font-semibold tabular-nums ${live ? "text-red-300" : "text-muted-foreground/70"}`}>
                              {fmtEventTime(evt.start_time).split(" ")[0]}
                            </span>
                            <span className="text-[9px] text-muted-foreground/40 tabular-nums">
                              {fmtEventTime(evt.start_time).split(" ").slice(1).join(" ")}
                            </span>
                          </div>

                          {/* Live indicator dot */}
                          <div className="w-6 shrink-0 flex flex-col items-center py-3 px-1">
                            {live ? (
                              <span className="h-2.5 w-2.5 rounded-full bg-red-500 shadow-lg shadow-red-500/50 animate-pulse mt-0.5" />
                            ) : (
                              <span className="h-2.5 w-2.5 rounded-full border-2 border-indigo-500/40 mt-0.5" />
                            )}
                            <div className="flex-1 w-px bg-border/20 mt-1" />
                          </div>

                          {/* Content */}
                          <div className="flex-1 min-w-0 py-3 pl-2">
                            <div className="flex items-start gap-2 flex-wrap">
                              <span className={`text-sm font-semibold leading-tight ${canceled ? "line-through" : "text-[#dcddde]"}`}>
                                {evt.name}
                              </span>
                              {live && (
                                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-red-500/20 text-red-300 border border-red-500/30 shrink-0">
                                  <Radio className="h-2 w-2 animate-pulse" />LIVE NOW
                                </span>
                              )}
                              {canceled && (
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-zinc-500/20 text-zinc-400 border border-zinc-500/30 shrink-0">CANCELLED</span>
                              )}
                            </div>
                            <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                              {evt.server_name && (
                                <span className={`text-[10px] font-medium ${avatarColor(evt.server_name)} bg-opacity-0`} style={{ color: "inherit" }}>
                                  <span className="text-[#5865f2]">{evt.server_name}</span>
                                </span>
                              )}
                              {evt.location && (
                                <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
                                  <MapPin className="h-2.5 w-2.5" />{evt.location}
                                </span>
                              )}
                              {evt.end_time && (
                                <span className="flex items-center gap-1 text-[10px] text-muted-foreground/50">
                                  <Clock className="h-2.5 w-2.5" />
                                  ends {fmtEventTime(evt.end_time).split(" ")[0]}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Expand chevron */}
                          <div className="shrink-0 flex items-center justify-center w-8">
                            <ChevronRight className={`h-3.5 w-3.5 text-muted-foreground/30 transition-transform ${isSelected ? "rotate-90" : ""}`} />
                          </div>
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Detail flyout ── */}
      {selectedEvent && (
        <div
          ref={detailRef}
          className="absolute top-0 right-0 bottom-0 w-80 bg-[#1e1f22] border-l border-black/40 flex flex-col shadow-2xl z-10 animate-in slide-in-from-right duration-200"
        >
          {/* Detail header */}
          <div className="flex items-start justify-between px-4 py-4 border-b border-black/30 shrink-0">
            <div className="flex-1 min-w-0 pr-3">
              <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                {isLiveNow(selectedEvent) && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-red-500/20 text-red-300 border border-red-500/30">
                    <Radio className="h-2 w-2 animate-pulse" />LIVE
                  </span>
                )}
                {(selectedEvent.status === "canceled" || selectedEvent.status === "cancelled") && (
                  <span className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-zinc-500/20 text-zinc-400 border border-zinc-500/30">
                    CANCELLED
                  </span>
                )}
              </div>
              <p className="text-sm font-bold text-white leading-snug">{selectedEvent.name}</p>
              {selectedEvent.server_name && (
                <p className="text-[11px] text-[#5865f2] font-medium mt-0.5">{selectedEvent.server_name}</p>
              )}
            </div>
            <button onClick={() => setSelectedEvent(null)} className="p-1.5 rounded-lg hover:bg-white/10 text-[#949ba4] hover:text-white transition shrink-0">
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Detail body */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar">
            {/* Time block */}
            <div className="rounded-xl bg-white/[0.03] border border-border/20 p-3 space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/50">Schedule</p>
              <div className="flex items-start gap-2">
                <CalendarDays className="h-3.5 w-3.5 text-indigo-400 shrink-0 mt-0.5" />
                <div>
                  <p className="text-[12px] font-semibold text-[#dcddde]">{fmtEventDate(selectedEvent.start_time)}</p>
                  <p className="text-[11px] text-muted-foreground/60">
                    {fmtEventTime(selectedEvent.start_time)}
                    {selectedEvent.end_time && ` → ${fmtEventTime(selectedEvent.end_time)}`}
                  </p>
                </div>
              </div>
            </div>

            {/* Location */}
            {selectedEvent.location && (
              <div className="rounded-xl bg-white/[0.03] border border-border/20 p-3 space-y-2">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/50">Location</p>
                <div className="flex items-start gap-2">
                  <MapPin className="h-3.5 w-3.5 text-emerald-400 shrink-0 mt-0.5" />
                  <p className="text-[12px] text-[#dcddde] break-all">{selectedEvent.location}</p>
                </div>
              </div>
            )}

            {/* Channel */}
            {selectedEvent.channel_name && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-white/[0.03] border border-border/20">
                <Hash className="h-3.5 w-3.5 text-muted-foreground/50 shrink-0" />
                <span className="text-[12px] text-[#dcddde]">{selectedEvent.channel_name}</span>
              </div>
            )}

            {/* Description */}
            {selectedEvent.description && (
              <div className="space-y-1.5">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/50 px-1">Description</p>
                <div className="rounded-xl bg-white/[0.03] border border-border/20 p-3">
                  <p className="text-[12px] text-[#b9bbbe] leading-relaxed whitespace-pre-wrap">
                    {selectedEvent.description}
                  </p>
                </div>
              </div>
            )}

            {/* Google Calendar sync note */}
            <div className="rounded-xl bg-indigo-500/5 border border-indigo-500/20 p-3 flex items-start gap-2">
              <CalendarDays className="h-3.5 w-3.5 text-indigo-400 shrink-0 mt-0.5" />
              <p className="text-[11px] text-indigo-300/70">
                This event may already be synced to your Google Calendar via the Discord daemon.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Page ────────────────────────────────────────────────────────────── */

export default function CsiDiscordWatchlistPage() {
  /* Watchlist state */
  const [servers, setServers] = useState<DiscordServer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState("");

  /* Tab */
  const [activeTab, setActiveTab] = useState<Tab>("messages");

  /* Selected server + messages */
  const [activeServerId, setActiveServerId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [totalMessages, setTotalMessages] = useState(0);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [msgError, setMsgError] = useState("");

  /* Flyout for sub-channel config */
  const [flyoutOpen, setFlyoutOpen] = useState(false);
  const flyoutRef = useRef<HTMLDivElement>(null);

  /* Add server / category controls */
  const [inputVal, setInputVal] = useState("");
  const [isAddingCategory, setIsAddingCategory] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  /* Category edit */
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [editCategoryVal, setEditCategoryVal] = useState("");

  /* Clearing messages */
  const [clearing, setClearing] = useState(false);

  /* ── Load watchlist ───────────────────────────────────────────────── */
  const loadWatchlist = useCallback(async () => {
    setLoadingList(true);
    setListError("");
    try {
      const r = await fetch("/api/v1/csi/discord");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = (await r.json()) as WatchlistResponse;
      setServers(d.servers ?? []);
      setCategories(d.categories ?? []);
    } catch (e: any) {
      setListError(e.message ?? "Failed to load watchlist");
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => { void loadWatchlist(); }, [loadWatchlist]);

  /* ── Load messages for selected server ───────────────────────────── */
  const loadMessages = useCallback(async (serverId: string) => {
    setLoadingMsgs(true);
    setMsgError("");
    setMessages([]);
    try {
      const r = await fetch(
        `${GATEWAY}/api/v1/dashboard/discord/servers/${encodeURIComponent(serverId)}/messages?limit=150`,
        { cache: "no-store" }
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setMessages((d.messages ?? []).reverse()); // oldest first for chat feel
      setTotalMessages(d.total ?? 0);
    } catch (e: any) {
      setMsgError(e.message ?? "Failed to load messages");
    } finally {
      setLoadingMsgs(false);
    }
  }, []);

  const selectServer = useCallback((id: string) => {
    setActiveServerId(id);
    setFlyoutOpen(false);
    void loadMessages(id);
  }, [loadMessages]);

  const activeServer = servers.find(s => s.server_id === activeServerId) ?? null;

  /* ── Clear all messages for active server ─────────────────────────── */
  const clearServerMessages = useCallback(async () => {
    if (!activeServerId) return;
    if (!confirm(`Clear all scraped messages for "${activeServer?.server_name}"? This cannot be undone.`)) return;
    setClearing(true);
    try {
      await fetch(
        `${GATEWAY}/api/v1/dashboard/discord/servers/${encodeURIComponent(activeServerId)}/messages`,
        { method: "DELETE" }
      );
      setMessages([]);
      setTotalMessages(0);
    } finally {
      setClearing(false);
    }
  }, [activeServerId, activeServer]);

  /* ── Sub-channel toggle ───────────────────────────────────────────── */
  const toggleChannel = useCallback(async (channelId: string, current: boolean) => {
    if (!activeServerId) return;
    setServers(prev => prev.map(s =>
      s.server_id !== activeServerId ? s : {
        ...s,
        channels: s.channels.map(c => c.channel_id === channelId ? { ...c, is_watched: !current } : c)
      }
    ));
    try {
      const r = await fetch(`/api/v1/csi/discord/${encodeURIComponent(activeServerId)}/channels/${encodeURIComponent(channelId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_watched: !current }),
      });
      if (!r.ok) throw new Error();
    } catch {
      await loadWatchlist();
    }
  }, [activeServerId, loadWatchlist]);

  /* ── Add server / category ────────────────────────────────────────── */
  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputVal.trim()) return;
    setSubmitting(true);
    setFormError("");
    try {
      if (isAddingCategory) {
        const r = await fetch("/api/v1/csi/discord/categories", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: inputVal.trim() }),
        });
        if (!r.ok) throw new Error("Failed to create category");
      } else {
        const r = await fetch("/api/v1/csi/discord/add", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ server_id: inputVal.trim() }),
        });
        if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error((b as any).detail ?? "Failed to add server"); }
      }
      setInputVal("");
      await loadWatchlist();
    } catch (e: any) {
      setFormError(e.message ?? "Error");
    } finally {
      setSubmitting(false);
    }
  }, [inputVal, isAddingCategory, loadWatchlist]);

  /* ── Delete server ────────────────────────────────────────────────── */
  const deleteServer = useCallback(async (serverId: string) => {
    if (!confirm("Remove this server from the watchlist?")) return;
    await fetch(`/api/v1/csi/discord/${encodeURIComponent(serverId)}`, { method: "DELETE" });
    if (activeServerId === serverId) { setActiveServerId(null); setMessages([]); }
    await loadWatchlist();
  }, [activeServerId, loadWatchlist]);

  /* ── Rename category ──────────────────────────────────────────────── */
  const renameCategory = useCallback(async (oldName: string) => {
    if (!editCategoryVal.trim() || editCategoryVal === oldName) { setEditingCategory(null); return; }
    await fetch(`/api/v1/csi/discord/categories/${encodeURIComponent(oldName)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: editCategoryVal.trim() }),
    });
    setEditingCategory(null);
    await loadWatchlist();
  }, [editCategoryVal, loadWatchlist]);

  /* ── Delete category ──────────────────────────────────────────────── */
  const deleteCategory = useCallback(async (name: string) => {
    if (!confirm(`Delete category "${name}"? All servers in it will also be removed.`)) return;
    await fetch(`/api/v1/csi/discord/categories/${encodeURIComponent(name)}`, { method: "DELETE" });
    await loadWatchlist();
  }, [loadWatchlist]);

  /* ── Close flyout on outside click ───────────────────────────────── */
  useEffect(() => {
    if (!flyoutOpen) return;
    const handler = (e: MouseEvent) => {
      if (flyoutRef.current && !flyoutRef.current.contains(e.target as Node)) setFlyoutOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [flyoutOpen]);

  /* ── Derived ──────────────────────────────────────────────────────── */
  const allDomains = Array.from(new Set([
    ...categories,
    ...servers.map(s => s.domain || "uncategorized"),
  ])).sort((a, b) => a === "uncategorized" ? 1 : b === "uncategorized" ? -1 : a.localeCompare(b));

  /* ─────────────────────────────────────────────────────────────────── */
  /* Render                                                              */
  /* ─────────────────────────────────────────────────────────────────── */
  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] gap-4 max-w-[1700px] mx-auto">

      {/* ── Header ── */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
            <MessageSquare className="h-5 w-5 text-indigo-400" />
            Discord CSI Watchlist
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Track Discord servers · configure watched channels · browse scraped messages
          </p>
        </div>
        <button
          onClick={loadWatchlist}
          disabled={loadingList}
          className="flex items-center gap-1.5 rounded-lg border border-border/40 bg-card/30 px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-card/60 transition disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loadingList ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* ── Add bar ── */}
      <div className="shrink-0 rounded-xl border border-border/30 bg-card/20 p-3 backdrop-blur">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <div className="flex bg-background/40 border border-border/30 rounded-lg p-0.5 shrink-0">
            <button type="button" onClick={() => setIsAddingCategory(false)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${!isAddingCategory ? "bg-indigo-500 text-white shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
              <Server className="w-3.5 h-3.5 inline-block mr-1 mb-0.5" />Server
            </button>
            <button type="button" onClick={() => setIsAddingCategory(true)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${isAddingCategory ? "bg-indigo-500 text-white shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
              <FolderPlus className="w-3.5 h-3.5 inline-block mr-1 mb-0.5" />Group
            </button>
          </div>
          <input
            value={inputVal} onChange={e => setInputVal(e.target.value)} disabled={submitting}
            placeholder={isAddingCategory ? "New group name…" : "Discord Server ID…"}
            className="flex-1 rounded-lg border border-border/30 bg-background/40 px-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/60 outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
          />
          <button type="submit" disabled={submitting || !inputVal.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-600 disabled:opacity-50 transition">
            {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
            {isAddingCategory ? "Add Group" : "Add Server"}
          </button>
        </form>
        {formError && <p className="mt-1.5 text-xs text-red-400">{formError}</p>}
      </div>

      {/* ── Main 2-panel layout ── */}
      <div className="flex flex-1 gap-4 min-h-0 overflow-hidden">

        {/* ── LEFT: Server list ── */}
        <div className="w-72 shrink-0 flex flex-col gap-3 overflow-y-auto pr-1 custom-scrollbar">
          {loadingList && servers.length === 0 ? (
            <div className="flex-1 flex items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : listError ? (
            <p className="text-xs text-red-400 p-2">{listError}</p>
          ) : allDomains.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center p-8 text-muted-foreground/50 space-y-3">
              <Server className="h-10 w-10 text-border" />
              <p className="text-xs">No servers yet. Add a Discord Server ID above.</p>
            </div>
          ) : (
            allDomains.map(domain => {
              const domainServers = servers
                .filter(s => (s.domain || "uncategorized") === domain)
                .sort((a, b) => a.server_name.localeCompare(b.server_name));
              const displayTitle = domain.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
              return (
                <div key={domain} className="space-y-1">
                  {/* Category header */}
                  <div className="flex items-center gap-1.5 px-1 group/hdr">
                    {editingCategory === domain ? (
                      <input autoFocus
                        className="flex-1 bg-background border border-border/50 rounded px-1.5 py-0.5 text-[11px] font-semibold outline-none focus:ring-1 focus:ring-indigo-500/40"
                        value={editCategoryVal}
                        onChange={e => setEditCategoryVal(e.target.value)}
                        onBlur={() => void renameCategory(domain)}
                        onKeyDown={e => e.key === "Enter" && void renameCategory(domain)}
                      />
                    ) : (
                      <>
                        <ChevronRight className="h-3 w-3 text-muted-foreground/40" />
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 flex-1 truncate">
                          {displayTitle}
                        </span>
                        <span className="text-[10px] text-muted-foreground/40">{domainServers.length}</span>
                        <button onClick={() => { setEditingCategory(domain); setEditCategoryVal(domain); }}
                          className="opacity-0 group-hover/hdr:opacity-100 p-0.5 rounded hover:text-white text-muted-foreground transition-all">
                          <Edit2 className="h-3 w-3" />
                        </button>
                        <button onClick={() => void deleteCategory(domain)}
                          className="opacity-0 group-hover/hdr:opacity-100 p-0.5 rounded hover:text-red-400 text-muted-foreground transition-all">
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </>
                    )}
                  </div>

                  {/* Server cards */}
                  {domainServers.map(srv => {
                    const watchedCount = srv.channels.filter(c => c.is_watched).length;
                    const isActive = activeServerId === srv.server_id;
                    return (
                      <div key={srv.server_id}
                        onClick={() => { setActiveTab("messages"); selectServer(srv.server_id); }}
                        className={`group relative flex items-center gap-2.5 rounded-xl px-3 py-2.5 cursor-pointer border transition-all ${isActive
                          ? "border-indigo-500/50 bg-indigo-500/10 shadow-sm shadow-indigo-500/10"
                          : "border-border/20 bg-background/20 hover:border-border/50 hover:bg-background/50"
                        }`}
                      >
                        {srv.icon_url ? (
                          <img src={srv.icon_url} alt="" className="h-9 w-9 rounded-xl shrink-0 border border-white/10" />
                        ) : (
                          <div className={`h-9 w-9 rounded-xl shrink-0 flex items-center justify-center text-white text-xs font-bold ${isActive ? "bg-indigo-600" : "bg-indigo-500/30"}`}>
                            {initials(srv.server_name)}
                          </div>
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate text-foreground">{srv.server_name}</p>
                          <p className="text-[10px] text-muted-foreground">
                            {watchedCount} channel{watchedCount !== 1 ? "s" : ""} watched
                          </p>
                        </div>
                        <button
                          onClick={e => { e.stopPropagation(); void deleteServer(srv.server_id); }}
                          className="opacity-0 group-hover:opacity-100 p-1 rounded-lg hover:bg-red-500/20 hover:text-red-400 text-muted-foreground/50 transition-all shrink-0"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              );
            })
          )}
        </div>

        {/* ── RIGHT: Tabbed panel ── */}
        <div className="flex-1 flex flex-col min-h-0 rounded-xl border border-border/30 bg-[#1e1f22] overflow-hidden relative">

          {/* ── Tab bar ── */}
          <div className="flex items-center gap-0 border-b border-black/30 bg-[#2b2d31] shrink-0 px-4">
            <button
              onClick={() => setActiveTab("messages")}
              className={`flex items-center gap-1.5 px-4 py-3 text-xs font-semibold border-b-2 transition-colors ${
                activeTab === "messages"
                  ? "border-indigo-400 text-white"
                  : "border-transparent text-[#949ba4] hover:text-[#dbdee1]"
              }`}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              Messages
            </button>
            <button
              onClick={() => setActiveTab("events")}
              className={`flex items-center gap-1.5 px-4 py-3 text-xs font-semibold border-b-2 transition-colors ${
                activeTab === "events"
                  ? "border-indigo-400 text-white"
                  : "border-transparent text-[#949ba4] hover:text-[#dbdee1]"
              }`}
            >
              <CalendarDays className="h-3.5 w-3.5" />
              Events
            </button>
          </div>

          {/* ── Messages tab ── */}
          {activeTab === "messages" && (
            !activeServer ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-10 text-muted-foreground/40 space-y-4">
                <div className="h-16 w-16 rounded-2xl bg-indigo-500/10 flex items-center justify-center border border-indigo-500/20">
                  <Inbox className="h-8 w-8 text-indigo-400/50" />
                </div>
                <div>
                  <p className="text-sm font-medium text-muted-foreground/60">Select a server</p>
                  <p className="text-xs mt-1">Click any server on the left to browse its scraped messages</p>
                </div>
              </div>
            ) : (
              <>
                {/* Panel header (Discord-style) */}
                <div className="flex items-center gap-3 px-4 py-3 border-b border-black/30 bg-[#2b2d31] shrink-0">
                  {activeServer.icon_url ? (
                    <img src={activeServer.icon_url} alt="" className="h-8 w-8 rounded-xl border border-white/10 shrink-0" />
                  ) : (
                    <div className="h-8 w-8 rounded-xl bg-indigo-600 flex items-center justify-center text-white text-xs font-bold shrink-0">
                      {initials(activeServer.server_name)}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="font-semibold text-white text-sm truncate">{activeServer.server_name}</p>
                    <p className="text-[10px] text-[#949ba4]">
                      {totalMessages.toLocaleString()} message{totalMessages !== 1 ? "s" : ""} stored
                      {messages.length < totalMessages && ` · showing ${messages.length}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => void clearServerMessages()}
                      disabled={clearing || messages.length === 0}
                      title="Clear all stored messages for this server"
                      className="flex items-center gap-1.5 rounded-lg border border-border/30 bg-background/30 px-2.5 py-1.5 text-[11px] text-muted-foreground hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/10 transition disabled:opacity-40"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      {clearing ? "Clearing…" : "Clear Messages"}
                    </button>
                    <button
                      onClick={() => void loadMessages(activeServer.server_id)}
                      disabled={loadingMsgs}
                      className="flex items-center gap-1.5 rounded-lg border border-border/30 bg-background/30 px-2.5 py-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-background/60 transition disabled:opacity-40"
                    >
                      <RefreshCw className={`h-3.5 w-3.5 ${loadingMsgs ? "animate-spin" : ""}`} />
                    </button>
                    <button
                      onClick={() => setFlyoutOpen(v => !v)}
                      className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition ${flyoutOpen ? "border-indigo-500/50 bg-indigo-500/15 text-indigo-300" : "border-border/30 bg-background/30 text-muted-foreground hover:text-foreground hover:bg-background/60"}`}
                    >
                      <Settings2 className="h-3.5 w-3.5" />
                      Channels
                    </button>
                  </div>
                </div>

                {/* Message feed */}
                <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-3 space-y-0.5">
                  {loadingMsgs ? (
                    <div className="flex items-center justify-center h-full">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                  ) : msgError ? (
                    <div className="flex items-center justify-center h-full">
                      <p className="text-sm text-red-400">{msgError}</p>
                    </div>
                  ) : messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground/40 space-y-3">
                      <Hash className="h-10 w-10 text-border" />
                      <div>
                        <p className="text-sm">No messages scraped yet</p>
                        <p className="text-xs mt-1">Make sure the Discord daemon is running and channels are watched</p>
                      </div>
                    </div>
                  ) : (
                    <>
                      {messages.map((msg, i) => {
                        const prevMsg = i > 0 ? messages[i - 1] : null;
                        const isGrouped = prevMsg && prevMsg.author_name === msg.author_name &&
                          Math.abs(new Date(msg.timestamp).getTime() - new Date(prevMsg.timestamp).getTime()) < 300_000;
                        const color = avatarColor(msg.author_name);
                        return (
                          <div key={msg.id}
                            className={`group flex gap-3 rounded-lg px-2 py-0.5 hover:bg-white/[0.03] transition-colors ${!isGrouped ? "mt-4 pt-1" : ""}`}
                          >
                            <div className="w-9 shrink-0 flex items-start justify-center pt-0.5">
                              {!isGrouped ? (
                                <div className={`h-9 w-9 rounded-full ${color} flex items-center justify-center text-white text-xs font-bold select-none`}>
                                  {msg.is_bot ? <Bot className="h-4 w-4" /> : initials(msg.author_name)}
                                </div>
                              ) : (
                                <span className="text-[9px] text-[#4e5058] opacity-0 group-hover:opacity-100 transition-opacity w-9 text-right leading-loose pt-0.5">
                                  {fmtTime(msg.timestamp)}
                                </span>
                              )}
                            </div>
                            <div className="flex-1 min-w-0">
                              {!isGrouped && (
                                <div className="flex items-baseline gap-2 flex-wrap">
                                  <span className="text-sm font-semibold text-white">
                                    {msg.author_name}
                                    {msg.is_bot && (
                                      <span className="ml-1.5 rounded px-1 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-indigo-500/30 text-indigo-300">BOT</span>
                                    )}
                                  </span>
                                  {msg.channel_name && (
                                    <span className="text-[10px] text-[#5865f2] font-medium">
                                      #{msg.channel_name}
                                    </span>
                                  )}
                                  <span className="text-[10px] text-[#949ba4]">{fmtTime(msg.timestamp)}</span>
                                </div>
                              )}
                              <p className="text-sm text-[#dcddde] leading-relaxed break-words whitespace-pre-wrap">
                                {msg.content || <span className="italic text-[#4e5058]">[no content]</span>}
                              </p>
                              {msg.has_attachments && (
                                <span className="inline-flex items-center gap-1 mt-0.5 text-[10px] text-[#949ba4]">
                                  <Paperclip className="h-2.5 w-2.5" /> attachment
                                </span>
                              )}
                              {msg.signals.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                  {msg.signals.map((sig, si) => (
                                    <span key={si} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] font-medium ${SEV_STYLES[sig.severity] ?? SEV_STYLES.low}`}>
                                      <AlertTriangle className="h-2.5 w-2.5" />
                                      {sig.rule.replace(/_/g, " ")}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                      {totalMessages > messages.length && (
                        <p className="text-center text-[11px] text-muted-foreground/50 pt-4 pb-2">
                          Showing {messages.length} of {totalMessages.toLocaleString()} messages
                        </p>
                      )}
                    </>
                  )}
                </div>
              </>
            )
          )}

          {/* ── Events tab ── */}
          {activeTab === "events" && <EventsPanel />}

          {/* ── Sub-channel config flyout (Messages tab only) ── */}
          {flyoutOpen && activeServer && activeTab === "messages" && (
            <div
              ref={flyoutRef}
              className="absolute top-0 right-0 bottom-0 w-72 bg-[#2b2d31] border-l border-black/30 flex flex-col shadow-2xl z-10 animate-in slide-in-from-right duration-200"
            >
              <div className="flex items-center justify-between px-4 py-3 border-b border-black/20 shrink-0">
                <p className="text-sm font-semibold text-white">Configure Channels</p>
                <button onClick={() => setFlyoutOpen(false)} className="p-1 rounded hover:bg-white/10 text-[#949ba4] hover:text-white transition">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="px-4 py-2 text-[10px] text-[#949ba4] border-b border-black/20 shrink-0">
                Toggle which channels are scraped. Changes take effect immediately.
              </p>
              <div className="flex-1 overflow-y-auto p-2 space-y-0.5 custom-scrollbar">
                {activeServer.channels.length === 0 ? (
                  <p className="text-xs text-[#949ba4] text-center py-8">No channels found</p>
                ) : (
                  activeServer.channels.map(ch => (
                    <button key={ch.channel_id}
                      onClick={() => void toggleChannel(ch.channel_id, ch.is_watched)}
                      className={`w-full flex items-center justify-between p-2 rounded-lg text-left transition-colors ${ch.is_watched ? "bg-[#404249] text-white" : "text-[#949ba4] hover:bg-[#35373c] hover:text-[#dbdee1]"}`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        <Hash className="h-3.5 w-3.5 shrink-0 text-[#80848e]" />
                        <span className="truncate text-[13px] font-medium">{ch.channel_name}</span>
                      </div>
                      <div className={`h-4 w-4 rounded border flex items-center justify-center shrink-0 ${ch.is_watched ? "bg-indigo-500 border-indigo-500" : "border-[#4e5058]"}`}>
                        {ch.is_watched && <Check className="h-3 w-3 text-white" />}
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
