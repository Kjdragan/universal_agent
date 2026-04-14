"use client";

import { useState, useEffect } from "react";
import { Youtube, Trash2, Plus, Loader2, RefreshCw } from "lucide-react";

type Channel = {
  channel_id: string;
  channel_name: string;
  video_count: number;
  rss_feed_url: string;
  youtube_url: string;
  domain: string;
};

type WatchlistResponse = {
  channels: Channel[];
};

export default function CsiWatchlistPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [newUrl, setNewUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const loadWatchlist = async () => {
    setLoading(true);
    setErrorMsg("");
    try {
      const resp = await fetch("/api/v1/csi/watchlist");
      if (!resp.ok) {
        throw new Error(`Failed to fetch watchlist: ${resp.status}`);
      }
      const data = (await resp.json()) as WatchlistResponse;
      setChannels(data.channels || []);
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to load watchlist");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWatchlist();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUrl.trim()) return;

    setSubmitting(true);
    setErrorMsg("");
    try {
      const resp = await fetch("/api/v1/csi/watchlist/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: newUrl.trim() }),
      });

      const body = await resp.json().catch(() => ({}));

      if (!resp.ok) {
        throw new Error(body.detail || `Failed to add channel (${resp.status})`);
      }
      setNewUrl("");
      await loadWatchlist();
    } catch (err: any) {
      setErrorMsg(err.message || "Error adding channel");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (channelId: string) => {
    if (!confirm("Are you sure you want to remove this channel?")) return;
    try {
      const resp = await fetch(`/api/v1/csi/watchlist/${encodeURIComponent(channelId)}`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to remove channel");
      }
      // Optimistic update
      setChannels((prev) => prev.filter((ch) => ch.channel_id !== channelId));
    } catch (err: any) {
      alert("Error: " + err.message);
    }
  };

  // Group by domains
  const groupedChannels = channels.reduce((acc, ch) => {
    const d = ch.domain || "uncategorized";
    if (!acc[d]) acc[d] = [];
    acc[d].push(ch);
    return acc;
  }, {} as Record<string, Channel[]>);

  // Sort domains alphabetically
  const sortedDomains = Object.keys(groupedChannels).sort((a, b) => {
    if (a === "other_signal" || a === "uncategorized") return 1;
    if (b === "other_signal" || b === "uncategorized") return -1;
    return a.localeCompare(b);
  });

  return (
    <div className="flex flex-col gap-6 w-full max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <Youtube className="h-6 w-6 text-red-500" />
            CSI YouTube Watchlist
          </h1>
          <p className="text-sm text-muted-foreground">
            Manage the list of YouTube channels monitored by the CSI Ingester.
          </p>
        </div>
        
        <button
          onClick={loadWatchlist}
          disabled={loading}
          className="flex w-fit items-center gap-2 rounded-lg bg-card/50 px-3 py-1.5 text-sm text-foreground border border-border/40 hover:bg-card transition disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Add Form */}
      <div className="rounded-xl border border-border/40 bg-card/20 p-4 backdrop-blur">
        <form onSubmit={handleAdd} className="flex flex-col sm:flex-row gap-3">
          <input
            type="text"
            placeholder="Enter YouTube Channel URL, Video URL, or @Handle..."
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            disabled={submitting}
            className="flex-1 rounded-lg border border-border/40 bg-background/50 px-4 py-2 text-sm text-foreground outline-none transition focus:border-primary/50 focus:ring-1 focus:ring-primary/20 placeholder:text-muted"
          />
          <button
            type="submit"
            disabled={submitting || !newUrl.trim()}
            className="flex items-center justify-center gap-2 rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Add Channel
          </button>
        </form>
        {errorMsg && (
          <div className="mt-3 text-sm text-red-400 bg-red-400/10 px-3 py-2 rounded-lg border border-red-400/20">
            {errorMsg}
          </div>
        )}
      </div>

      {/* Loading state */}
      {loading && channels.length === 0 ? (
        <div className="flex h-40 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        /* Columns / Masonry Grid for Categories */
        <div className="columns-1 md:columns-2 lg:columns-3 xl:columns-4 gap-6 space-y-6">
          {sortedDomains.map((domain) => {
            // Sort channels inside category alphabetically
            const categoryChannels = [...groupedChannels[domain]].sort((a, b) =>
              (a.channel_name || "").localeCompare(b.channel_name || "")
            );
            
            // Format domain nicely 
            const formattedDomain = domain
              .split("_")
              .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
              .join(" ");

            return (
              <div key={domain} className="break-inside-avoid rounded-xl border border-border/40 bg-card/20 p-4">
                <div className="mb-3 flex items-center justify-between border-b border-border/40 pb-2">
                  <h2 className="font-semibold text-foreground">{formattedDomain}</h2>
                  <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    {categoryChannels.length}
                  </span>
                </div>
                
                <ul className="flex flex-col gap-2">
                  {categoryChannels.map((ch) => (
                    <li key={ch.channel_id} className="group relative flex items-start justify-between gap-2 text-sm">
                      <a 
                        href={ch.youtube_url} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        className="flex-1 truncate font-medium text-slate-300 transition-colors hover:text-cyan-400"
                        title={ch.channel_name}
                      >
                        {ch.channel_name}
                      </a>
                      
                      <button
                        onClick={() => handleDelete(ch.channel_id)}
                        className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-muted-foreground hover:text-red-400 rounded-md hover:bg-red-400/10"
                        title="Remove Channel"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}

      {!loading && channels.length === 0 && !errorMsg && (
        <div className="flex h-40 items-center justify-center text-muted-foreground">
          No channels found in the watchlist. Add one above!
        </div>
      )}
    </div>
  );
}
