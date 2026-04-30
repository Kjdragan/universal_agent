"use client";

import { useState, useEffect } from "react";
import { Youtube, Trash2, Plus, Loader2, RefreshCw, Edit2, PlaySquare, FolderPlus, ListVideo, Search, ChevronDown, ChevronRight, FileText, BookOpen } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Channel = {
  channel_id: string;
  channel_name: string;
  video_count: number;
  rss_feed_url: string;
  youtube_url: string;
  domain: string;
};

type RecentVideo = {
  video_id: string;
  title: string;
  channel_name: string;
  channel_id: string;
  published_at: string;
  ingested_at: string;
};

type WatchlistResponse = {
  channels: Channel[];
  categories: string[];
};

type DailyDigest = {
  id: string;
  event_id: string;
  source: string;
  event_type: string;
  title: string;
  summary: string;
  full_report_md: string;
  source_types: string[];
  created_at: string;
};

function compactDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const h = d.getHours(), m = d.getMinutes();
  const ampm = h >= 12 ? "p" : "a";
  return `${months[d.getMonth()]} ${d.getDate()} ${h % 12 || 12}:${m < 10 ? "0" + m : m}${ampm}`;
}

export default function CsiWatchlistPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Inputs
  const [inputVal, setInputVal] = useState("");
  const [inputMode, setInputMode] = useState<'search' | 'channel' | 'category'>('search');
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [successMsg, setSuccessMsg] = useState<React.ReactNode | null>(null);

  // Category expansion
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({});

  const toggleCategory = (cat: string) => {
    setExpandedCategories(prev => ({...prev, [cat]: !prev[cat]}));
  };

  // Preview
  const [previewChannel, setPreviewChannel] = useState<Channel | null>(null);
  const [previewVideoId, setPreviewVideoId] = useState<string | null>(null);

  // Recently ingested videos
  const [recentVideos, setRecentVideos] = useState<RecentVideo[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);

  // Daily Digests
  const [middleColumnMode, setMiddleColumnMode] = useState<'recent' | 'digests'>('recent');
  const [dailyDigests, setDailyDigests] = useState<DailyDigest[]>([]);
  const [digestsLoading, setDigestsLoading] = useState(false);
  const [selectedDigest, setSelectedDigest] = useState<DailyDigest | null>(null);

  // Renaming Categories
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [editCategoryVal, setEditCategoryVal] = useState("");

  // Visual Drag State (optional glow effect)
  const [dragOverCategory, setDragOverCategory] = useState<string | null>(null);

  // Recently ingested videos filter
  const [recentCategoryFilter, setRecentCategoryFilter] = useState<string | null>(null);

  const loadWatchlist = async (clearSuccess = true) => {
    setLoading(true);
    setErrorMsg("");
    if (clearSuccess) setSuccessMsg(null);
    try {
      const resp = await fetch("/api/v1/csi/watchlist");
      if (!resp.ok) {
        throw new Error(`Failed to fetch watchlist: ${resp.status}`);
      }
      const data = (await resp.json()) as WatchlistResponse;
      setChannels(data.channels || []);
      setCategories(data.categories || []);
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to load watchlist");
    } finally {
      setLoading(false);
    }
  };

  const loadRecentVideos = async (catFilter?: string | null) => {
    setRecentLoading(true);
    const filter = catFilter !== undefined ? catFilter : recentCategoryFilter;
    try {
      const url = filter 
        ? `/api/v1/csi/watchlist/recent-videos?limit=60&category=${encodeURIComponent(filter)}`
        : `/api/v1/csi/watchlist/recent-videos?limit=60`;
      const resp = await fetch(url);
      if (resp.ok) {
        const data = await resp.json();
        setRecentVideos(data.videos || []);
      }
    } catch { /* silent */ }
    finally { setRecentLoading(false); }
  };

  const loadDailyDigests = async () => {
    setDigestsLoading(true);
    try {
      const resp = await fetch("/api/dashboard/gateway/api/v1/dashboard/csi/digests?limit=100");
      if (resp.ok) {
        const data = await resp.json();
        const allDigests: DailyDigest[] = data.digests || [];
        // Filter to only youtube_daily_digest events
        const ytDigests = allDigests.filter(d => 
          (d.event_type || '').toLowerCase().includes('youtube_daily_digest')
        );
        setDailyDigests(ytDigests);
      }
    } catch { /* silent */ }
    finally { setDigestsLoading(false); }
  };

  useEffect(() => {
    loadWatchlist();
    loadRecentVideos();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (inputMode === 'search') return;
    if (!inputVal.trim()) return;

    setSubmitting(true);
    setErrorMsg("");
    setSuccessMsg(null);
    try {
      if (inputMode === 'category') {
        const resp = await fetch("/api/v1/csi/watchlist/categories", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: inputVal.trim() }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || "Failed to create category");
        }
        setSuccessMsg(`Category "${inputVal.trim()}" created successfully.`);
      } else {
        const resp = await fetch("/api/v1/csi/watchlist/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: inputVal.trim() }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || `Failed to add channel`);
        }
        const data = await resp.json();
        if (data.channel) {
          const ch = data.channel;
          setSuccessMsg(
            <div className="flex flex-col gap-1">
              <span className="font-semibold text-green-400">Successfully added channel!</span>
              <span className="text-sm">Name: <span className="text-white">{ch.channel_name}</span></span>
              <span className="text-sm">Category: <span className="text-white">{ch.domain}</span></span>
              <span className="text-sm text-muted-foreground">{data.message}</span>
            </div>
          );
        } else {
          setSuccessMsg("Channel added successfully.");
        }
      }
      setInputVal("");
      await loadWatchlist(false);
    } catch (err: any) {
      setErrorMsg(err.message || "Error processing request");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteChannel = async (channelId: string) => {
    if (!confirm("Remove this channel from the watchlist?")) return;
    try {
      const resp = await fetch(`/api/v1/csi/watchlist/${encodeURIComponent(channelId)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error("Failed to remove channel");
      
      setChannels((prev) => prev.filter((ch) => ch.channel_id !== channelId));
      if (previewChannel?.channel_id === channelId) setPreviewChannel(null);
    } catch (err: any) {
      alert("Error: " + err.message);
    }
  };

  const handleRenameCategory = async (oldName: string) => {
    if (!editCategoryVal.trim() || editCategoryVal === oldName) {
      setEditingCategory(null);
      return;
    }
    try {
      const resp = await fetch(`/api/v1/csi/watchlist/categories/${encodeURIComponent(oldName)}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ name: editCategoryVal.trim() })
      });
      if (!resp.ok) throw new Error("Failed to rename category");
      await loadWatchlist();
    } catch (err: any) {
      alert("Error renaming: " + err.message);
    } finally {
      setEditingCategory(null);
    }
  };

  const handleDeleteCategory = async (name: string) => {
    if (!confirm(`Warning: Delete "${name}"? This cascades and removes all channels inside it from the DB! \n\nAre you sure?`)) return;
    try {
      const resp = await fetch(`/api/v1/csi/watchlist/categories/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error("Failed to delete category");
      
      if (previewChannel?.domain === name) {
        setPreviewChannel(null);
      }
      await loadWatchlist();
    } catch (err: any) {
      alert("Error deleting category: " + err.message);
    }
  };

  // --- Kanban Drag and Drop Logic ---
  const handleDragStart = (e: React.DragEvent, channelId: string) => {
     e.dataTransfer.setData("channel_id", channelId);
  }

  const handleDragOver = (e: React.DragEvent, cat: string) => {
      e.preventDefault();
      if (dragOverCategory !== cat) {
          setDragOverCategory(cat);
      }
  }

  const handleDragLeave = (e: React.DragEvent, cat: string) => {
      if (dragOverCategory === cat) {
          setDragOverCategory(null);
      }
  }

  const handleDrop = async (e: React.DragEvent, newCategory: string) => {
      e.preventDefault();
      setDragOverCategory(null);
      
      const channelId = e.dataTransfer.getData("channel_id");
      if (!channelId) return;

      // Optimistic channel move
      setChannels(prev => prev.map(c => c.channel_id === channelId ? { ...c, domain: newCategory } : c));

      try {
          const resp = await fetch(`/api/v1/csi/watchlist/${encodeURIComponent(channelId)}`, {
              method: "PATCH",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({ domain: newCategory })
          });
          if (!resp.ok) throw new Error("Move failed");
      } catch (err: any) {
          alert("Error moving channel: " + err.message);
          await loadWatchlist(); // revert on fail
      }
  }

  // Generate complete list of unique domains (categories + channel domains)
  const allDomains = Array.from(new Set([
    ...categories,
    ...channels.map(c => c.domain || "uncategorized")
  ])).sort((a, b) => {
    if (a === "other_signal" || a === "uncategorized") return 1;
    if (b === "other_signal" || b === "uncategorized") return -1;
    return a.localeCompare(b);
  });

  const filteredChannels = channels.filter(c => {
    if (inputMode === 'search' && inputVal.trim() !== '') {
      return c.channel_name.toLowerCase().includes(inputVal.trim().toLowerCase());
    }
    return true;
  });

  return (
    <div className="flex flex-col gap-6 w-full max-w-[1600px] mx-auto h-[calc(100vh-6rem)]">
      {/* Header & Controls */}
      <div className="flex flex-col gap-4">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
              <Youtube className="h-6 w-6 text-red-500" />
              CSI - Youtube Watchlist
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Drag channels between categories. Click a channel to preview content natively.
            </p>
          </div>
          <button
            onClick={() => loadWatchlist()}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg bg-card/60 px-3 py-1.5 text-sm text-foreground border border-border/40 hover:bg-card hover:border-border transition disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        {/* Action Bar */}
        <div className="rounded-xl border border-border/40 bg-card/20 p-4 backdrop-blur shadow-sm">
          <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-3">
            <div className="flex bg-background/50 border border-border/40 rounded-lg p-1">
              <button 
                type="button"
                onClick={() => setInputMode('search')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${inputMode === 'search' ? 'bg-green-500 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <Search className="w-4 h-4 inline-block mr-1.5 mb-0.5" />
                Search
              </button>
              <button 
                type="button"
                onClick={() => setInputMode('channel')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${inputMode === 'channel' ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <Youtube className="w-4 h-4 inline-block mr-1.5 mb-0.5" />
                Channel
              </button>
              <button 
                type="button"
                onClick={() => setInputMode('category')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${inputMode === 'category' ? 'bg-indigo-500 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <FolderPlus className="w-4 h-4 inline-block mr-1.5 mb-0.5" />
                Category
              </button>
            </div>
            <input
              type="text"
              placeholder={inputMode === 'category' ? "Enter new category name..." : inputMode === 'channel' ? "Enter YouTube Channel URL or Handle..." : "Search channels by name..."}
              value={inputVal}
              onChange={(e) => setInputVal(e.target.value)}
              disabled={submitting}
              className="flex-1 rounded-lg border border-border/40 bg-background/50 px-4 py-2 text-sm text-foreground outline-none transition focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
            />
            <button
              type="submit"
              disabled={submitting || (inputMode !== 'search' && !inputVal.trim())}
              className={`flex items-center justify-center gap-2 rounded-lg px-5 py-2 text-sm font-medium text-white transition disabled:opacity-50 ${inputMode === 'category' ? 'bg-indigo-500 hover:bg-indigo-600' : inputMode === 'search' ? 'bg-green-500 hover:bg-green-600' : 'bg-primary hover:bg-primary/90'}`}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : inputMode === 'search' ? <Search className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
              {inputMode === 'category' ? "Add Category" : inputMode === 'search' ? "Search channels" : "Add Channel"}
            </button>
          </form>
          {errorMsg && (
            <div className="mt-3 text-sm text-red-200 bg-red-950/40 px-3 py-2 rounded-lg border border-red-500/20 flex items-center">
              {errorMsg}
            </div>
          )}
          {successMsg && !errorMsg && (
            <div className="mt-3 text-sm text-green-200 bg-green-950/40 px-3 py-2 rounded-lg border border-green-500/20 flex items-start">
              {successMsg}
            </div>
          )}
        </div>
      </div>

      {loading && channels.length === 0 ? (
         <div className="flex-1 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
         </div>
      ) : (
        <div className="flex gap-6 flex-1 min-h-0 overflow-hidden pb-4">
          
          {/* Kanban Columns */}
          <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
            <div className="columns-1 lg:columns-2 gap-5 space-y-5">
              {allDomains.map((domain) => {
                const categoryChannels = filteredChannels.filter(c => (c.domain || "uncategorized") === domain)
                  .sort((a,b) => a.channel_name.localeCompare(b.channel_name));
                
                const isSearching = inputMode === 'search' && inputVal.trim() !== '';
                if (isSearching && categoryChannels.length === 0) {
                    return null;
                }

                const isExpanded = isSearching ? true : !!expandedCategories[domain];

                // Formatting domain for display
                const displayTitle = domain
                  .split("_")
                  .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                  .join(" ");

                const isDragTarget = dragOverCategory === domain;

                return (
                  <div 
                    key={domain} 
                    onDragOver={(e) => handleDragOver(e, domain)}
                    onDragLeave={(e) => handleDragLeave(e, domain)}
                    onDrop={(e) => handleDrop(e, domain)}
                    className={`break-inside-avoid rounded-xl border p-4 transition-all duration-200 ${
                       isDragTarget 
                         ? 'border-primary bg-primary/5 ring-1 ring-primary/20' 
                         : 'border-border/40 bg-card/20'
                    }`}
                  >
                    {/* Category Header */}
                    <div 
                      className="mb-3 flex items-center justify-between border-b border-border/30 pb-2 group/header cursor-pointer"
                      onClick={() => {
                         if (!editingCategory) {
                             toggleCategory(domain);
                             setRecentCategoryFilter(domain);
                             loadRecentVideos(domain);
                         }
                      }}
                    >
                      {editingCategory === domain ? (
                        <input 
                           autoFocus
                           className="bg-background border border-border/50 rounded px-2 py-1 text-sm font-semibold w-[60%] outline-none focus:ring-1 focus:ring-primary/40"
                           value={editCategoryVal}
                           onChange={(e) => setEditCategoryVal(e.target.value)}
                           onBlur={() => handleRenameCategory(domain)}
                           onKeyDown={(e) => e.key === 'Enter' && handleRenameCategory(domain)}
                           onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <div className="flex items-center gap-2 max-w-[70%]">
                         {isExpanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                         <h2 className="font-semibold text-foreground truncate">{displayTitle}</h2>
                         <button 
                           onClick={(e) => { e.stopPropagation(); setEditingCategory(domain); setEditCategoryVal(domain); }}
                           className="opacity-0 group-hover/header:opacity-100 p-1 hover:bg-white/10 rounded text-muted-foreground hover:text-white transition-all"
                         >
                            <Edit2 className="w-3.5 h-3.5" />
                         </button>
                        </div>
                      )}

                      <div className="flex items-center gap-2">
                        <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                          {categoryChannels.length}
                        </span>
                        <button 
                          onClick={(e) => { e.stopPropagation(); handleDeleteCategory(domain); }}
                          className="opacity-0 group-hover/header:opacity-100 p-1 hover:bg-red-500/20 rounded text-muted-foreground hover:text-red-400 transition-all"
                          title="Delete Category completely"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                    
                    {/* Channel List */}
                    {isExpanded && (
                    <ul className="flex flex-col gap-2 min-h-[40px]">
                      {categoryChannels.map((ch) => (
                        <li 
                           key={ch.channel_id} 
                           draggable
                           onDragStart={(e) => handleDragStart(e, ch.channel_id)}
                           className={`group relative flex items-center justify-between gap-2 p-2 rounded-lg border transition-all cursor-grab active:cursor-grabbing ${
                             previewChannel?.channel_id === ch.channel_id 
                               ? 'border-primary/50 bg-primary/10 shadow-sm' 
                               : 'border-border/20 bg-background/30 hover:border-border/60 hover:bg-background/80'
                           }`}
                        >
                          <button 
                            onClick={() => { setPreviewChannel(ch); setPreviewVideoId(null); }}
                            className="flex-1 truncate font-medium text-sm text-slate-300 text-left hover:text-cyan-400 transition-colors flex items-center gap-2"
                          >
                            <span className="truncate">{ch.channel_name}</span>
                          </button>
                          
                          <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                             <a 
                               href={ch.youtube_url} 
                               target="_blank" 
                               rel="noopener noreferrer"
                               className="p-1.5 text-muted-foreground hover:text-cyan-400 rounded-md hover:bg-white/5"
                               title="Open on YouTube"
                             >
                               <ListVideo className="h-3.5 w-3.5" />
                             </a>
                             <button
                               onClick={() => handleDeleteChannel(ch.channel_id)}
                               className="p-1.5 text-muted-foreground hover:text-red-400 rounded-md hover:bg-red-400/10"
                               title="Remove Channel"
                             >
                                <Trash2 className="h-3.5 w-3.5" />
                             </button>
                          </div>
                        </li>
                      ))}
                      {categoryChannels.length === 0 && (
                          <div className="text-xs text-muted-foreground/50 text-center py-4 border-2 border-dashed border-border/20 rounded-lg">
                              Drag channels here
                          </div>
                      )}
                    </ul>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Middle Column: Recently Ingested Videos / Daily Digests */}
          <div className="hidden lg:flex w-[260px] xl:w-[300px] shrink-0 border border-border/40 bg-card/20 rounded-xl flex-col overflow-hidden">
            {/* Toggle Header */}
            <div className="bg-background/80 border-b border-border/40 px-2 py-2 shrink-0">
              <div className="flex bg-background/50 border border-border/40 rounded-lg p-0.5">
                <button
                  onClick={() => { setMiddleColumnMode('recent'); setSelectedDigest(null); }}
                  className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md text-[11px] font-semibold transition-colors ${
                    middleColumnMode === 'recent'
                      ? 'bg-red-500/20 text-red-400 shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <ListVideo className="w-3.5 h-3.5" />
                  Recent
                </button>
                <button
                  onClick={() => { setMiddleColumnMode('digests'); setPreviewVideoId(null); setPreviewChannel(null); loadDailyDigests(); }}
                  className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md text-[11px] font-semibold transition-colors ${
                    middleColumnMode === 'digests'
                      ? 'bg-amber-500/20 text-amber-400 shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <BookOpen className="w-3.5 h-3.5" />
                  Daily Digests
                </button>
              </div>
            </div>

            {/* Sub-header (contextual) */}
            {middleColumnMode === 'recent' && (
              <div className="bg-background/60 border-b border-border/30 px-4 py-2 shrink-0 flex items-center justify-between">
                <div className="flex flex-col gap-1">
                  {recentCategoryFilter && (
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-medium text-primary bg-primary/10 px-1.5 py-0.5 rounded border border-primary/20">
                        {recentCategoryFilter.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")}
                      </span>
                      <button 
                        onClick={(e) => { e.stopPropagation(); setRecentCategoryFilter(null); loadRecentVideos(null); }}
                        className="text-[10px] text-muted-foreground hover:text-red-400 underline underline-offset-2"
                      >
                        Clear
                      </button>
                    </div>
                  )}
                </div>
                <button onClick={() => loadRecentVideos()} disabled={recentLoading} className="text-xs text-muted-foreground hover:text-foreground transition">
                  <RefreshCw className={`w-3.5 h-3.5 ${recentLoading ? 'animate-spin' : ''}`} />
                </button>
              </div>
            )}
            {middleColumnMode === 'digests' && (
              <div className="bg-background/60 border-b border-border/30 px-4 py-2 shrink-0 flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground font-medium">
                  {dailyDigests.length} digest{dailyDigests.length !== 1 ? 's' : ''}
                </span>
                <button onClick={() => loadDailyDigests()} disabled={digestsLoading} className="text-xs text-muted-foreground hover:text-foreground transition">
                  <RefreshCw className={`w-3.5 h-3.5 ${digestsLoading ? 'animate-spin' : ''}`} />
                </button>
              </div>
            )}

            {/* Content */}
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              {middleColumnMode === 'recent' ? (
                <>
                  {recentVideos.length === 0 && !recentLoading && (
                    <div className="text-xs text-muted-foreground/50 text-center py-8">No recent videos</div>
                  )}
                  {recentVideos.map((v) => (
                    <button
                      key={`${v.video_id}-${v.ingested_at}`}
                      onClick={() => { setPreviewVideoId(v.video_id); setPreviewChannel(null); setSelectedDigest(null); }}
                      className={`w-full text-left px-3 py-2.5 border-b border-border/20 hover:bg-primary/5 transition-colors ${
                        previewVideoId === v.video_id ? 'bg-primary/10 border-l-2 border-l-primary' : ''
                      }`}
                    >
                      <p className="text-[13px] font-medium text-foreground/90 leading-snug line-clamp-2">{v.title || 'Untitled'}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-[11px] text-cyan-400/80 truncate max-w-[140px]">{v.channel_name}</span>
                        <span className="text-[10px] text-muted-foreground/60 ml-auto whitespace-nowrap">{compactDate(v.ingested_at)}</span>
                      </div>
                    </button>
                  ))}
                </>
              ) : (
                <>
                  {dailyDigests.length === 0 && !digestsLoading && (
                    <div className="flex flex-col items-center justify-center py-8 gap-2">
                      <BookOpen className="w-8 h-8 text-border" />
                      <span className="text-xs text-muted-foreground/50 text-center px-4">
                        No daily digests yet. They appear after the daily cron processes your YouTube playlists.
                      </span>
                    </div>
                  )}
                  {dailyDigests.map((digest) => {
                    const isActive = selectedDigest?.id === digest.id;
                    // Extract day and date from title like "Daily YouTube Digest: Monday, 2026-04-30 (8 videos)"
                    const dateMatch = digest.title.match(/(\w+day),\s*([\d-]+)/);
                    const dayLabel = dateMatch ? dateMatch[1] : '';
                    const dateLabel = dateMatch ? dateMatch[2] : compactDate(digest.created_at);
                    const videoCountMatch = digest.title.match(/(\d+)\s*videos?/);
                    const videoCount = videoCountMatch ? videoCountMatch[1] : '';

                    return (
                      <button
                        key={digest.id}
                        onClick={() => { setSelectedDigest(digest); setPreviewVideoId(null); setPreviewChannel(null); }}
                        className={`w-full text-left px-3 py-3 border-b border-border/20 hover:bg-amber-500/5 transition-colors ${
                          isActive ? 'bg-amber-500/10 border-l-2 border-l-amber-400' : ''
                        }`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <FileText className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                          <span className="text-[13px] font-semibold text-foreground/90">
                            {dayLabel || 'Digest'}
                          </span>
                          {videoCount && (
                            <span className="text-[10px] font-medium bg-amber-500/15 text-amber-400 px-1.5 py-0.5 rounded ml-auto">
                              {videoCount} videos
                            </span>
                          )}
                        </div>
                        <p className="text-[11px] text-muted-foreground leading-snug">
                          {dateLabel}
                        </p>
                        {digest.summary && (
                          <p className="text-[10px] text-muted-foreground/60 mt-1 line-clamp-2 leading-relaxed">
                            {digest.summary.slice(0, 120)}
                          </p>
                        )}
                      </button>
                    );
                  })}
                </>
              )}
            </div>
          </div>

          {/* Persistent Preview Viewer Panel */}
          <div className="hidden md:flex w-[440px] lg:w-[500px] xl:w-[600px] shrink-0 border border-border/40 bg-card/20 rounded-xl flex-col shadow-inner overflow-hidden">
             {selectedDigest ? (
                <>
                   <div className="bg-background/80 border-b border-border/40 p-4 shrink-0 flex items-center justify-between">
                      <div className="flex-1 min-w-0">
                         <div className="flex items-center gap-2 mb-1">
                           <span className="text-[10px] font-bold tracking-wide bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded">
                             DAILY DIGEST
                           </span>
                         </div>
                         <h3 className="font-bold text-foreground text-sm truncate">
                           {selectedDigest.title}
                         </h3>
                         <p className="text-xs text-muted-foreground mt-0.5">
                           {compactDate(selectedDigest.created_at)}
                         </p>
                      </div>
                   </div>
                   <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
                     <div className="prose prose-invert prose-sm max-w-none
                       prose-headings:text-foreground prose-headings:font-bold
                       prose-p:text-muted-foreground prose-p:leading-relaxed
                       prose-strong:text-foreground
                       prose-a:text-cyan-400 prose-a:no-underline hover:prose-a:underline
                       prose-code:text-amber-400 prose-code:bg-amber-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[12px]
                       prose-pre:bg-background/80 prose-pre:border prose-pre:border-border/40
                       prose-li:text-muted-foreground
                       prose-hr:border-border/40
                       prose-blockquote:border-l-amber-400 prose-blockquote:text-muted-foreground
                       prose-th:text-foreground/80 prose-td:text-muted-foreground
                     ">
                       <ReactMarkdown remarkPlugins={[remarkGfm]}>
                         {selectedDigest.full_report_md || selectedDigest.summary || 'No content available.'}
                       </ReactMarkdown>
                     </div>
                   </div>
                </>
             ) : previewVideoId ? (
                <>
                   <div className="bg-background/80 border-b border-border/40 p-4 shrink-0 flex items-center justify-between">
                      <div>
                         <h3 className="font-bold text-foreground text-sm truncate w-[280px]">
                           {recentVideos.find(v => v.video_id === previewVideoId)?.title || 'Video'}
                         </h3>
                         <p className="text-xs text-muted-foreground mt-0.5">
                           {recentVideos.find(v => v.video_id === previewVideoId)?.channel_name || ''}
                         </p>
                      </div>
                      <a href={`https://www.youtube.com/watch?v=${previewVideoId}`} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-xs bg-red-500/10 text-red-400 hover:bg-red-500/20 px-2 py-1 rounded transition-colors font-medium">
                         <Youtube className="w-3.5 h-3.5" /> Open
                      </a>
                   </div>
                   <div className="flex-1 w-full bg-black">
                       <iframe
                          className="w-full h-full border-0"
                          src={`https://www.youtube.com/embed/${previewVideoId}?autoplay=1&playsinline=1`}
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
                          title="Video Preview"
                       />
                   </div>
                </>
             ) : previewChannel ? (
                <>
                   <div className="bg-background/80 border-b border-border/40 p-4 shrink-0 flex items-center justify-between">
                      <div>
                         <h3 className="font-bold text-foreground text-sm truncate w-[280px]">{previewChannel.channel_name}</h3>
                         <p className="text-xs text-muted-foreground mt-0.5">Latest Uploads Viewer</p>
                      </div>
                      <a href={`https://www.youtube.com/playlist?list=UU${previewChannel.channel_id.replace(/^UC/, '')}`} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-xs bg-red-500/10 text-red-400 hover:bg-red-500/20 px-2 py-1 rounded transition-colors font-medium">
                         <Youtube className="w-3.5 h-3.5" /> Open
                      </a>
                   </div>
                   <div className="flex-1 w-full bg-black">
                       <iframe
                          className="w-full h-full border-0"
                          src={`https://www.youtube.com/embed/videoseries?list=UU${previewChannel.channel_id.replace(/^UC/, '')}&playsinline=1&fs=0`}
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
                          title="Channel Uploads Playlist Viewer"
                       />
                   </div>
                </>
             ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground/60 space-y-4">
                    <PlaySquare className="w-12 h-12 text-border" />
                    <p className="text-sm">{middleColumnMode === 'digests' ? 'Select a digest to read.' : 'Click a recent video or channel to preview.'}</p>
                </div>
             )}
          </div>

        </div>
      )}
    </div>
  );
}
