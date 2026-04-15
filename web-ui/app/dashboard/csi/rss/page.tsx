"use client";

import { useState, useEffect } from "react";
import { Youtube, Trash2, Plus, Loader2, RefreshCw, Edit2, PlaySquare, FolderPlus, ListVideo } from "lucide-react";

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
  categories: string[];
};

export default function CsiWatchlistPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Inputs
  const [inputVal, setInputVal] = useState("");
  const [isAddingCategory, setIsAddingCategory] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // Preview
  const [previewChannel, setPreviewChannel] = useState<Channel | null>(null);

  // Renaming Categories
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [editCategoryVal, setEditCategoryVal] = useState("");

  // Visual Drag State (optional glow effect)
  const [dragOverCategory, setDragOverCategory] = useState<string | null>(null);

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
      setCategories(data.categories || []);
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to load watchlist");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWatchlist();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputVal.trim()) return;

    setSubmitting(true);
    setErrorMsg("");
    try {
      if (isAddingCategory) {
        const resp = await fetch("/api/v1/csi/watchlist/categories", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: inputVal.trim() }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || "Failed to create category");
        }
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
      }
      setInputVal("");
      await loadWatchlist();
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

  return (
    <div className="flex flex-col gap-6 w-full max-w-[1600px] mx-auto h-[calc(100vh-6rem)]">
      {/* Header & Controls */}
      <div className="flex flex-col gap-4">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
              <Youtube className="h-6 w-6 text-red-500" />
              CSI Watchlist Kanban
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Drag channels between categories. Click a channel to preview content natively.
            </p>
          </div>
          <button
            onClick={loadWatchlist}
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
                onClick={() => setIsAddingCategory(false)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${!isAddingCategory ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <Youtube className="w-4 h-4 inline-block mr-1.5 mb-0.5" />
                Channel
              </button>
              <button 
                type="button"
                onClick={() => setIsAddingCategory(true)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${isAddingCategory ? 'bg-indigo-500 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <FolderPlus className="w-4 h-4 inline-block mr-1.5 mb-0.5" />
                Category
              </button>
            </div>
            <input
              type="text"
              placeholder={isAddingCategory ? "Enter new category name..." : "Enter YouTube Channel URL or Handle..."}
              value={inputVal}
              onChange={(e) => setInputVal(e.target.value)}
              disabled={submitting}
              className="flex-1 rounded-lg border border-border/40 bg-background/50 px-4 py-2 text-sm text-foreground outline-none transition focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
            />
            <button
              type="submit"
              disabled={submitting || !inputVal.trim()}
              className={`flex items-center justify-center gap-2 rounded-lg px-5 py-2 text-sm font-medium text-white transition disabled:opacity-50 ${isAddingCategory ? 'bg-indigo-500 hover:bg-indigo-600' : 'bg-primary hover:bg-primary/90'}`}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              {isAddingCategory ? "Add Category" : "Add Channel"}
            </button>
          </form>
          {errorMsg && (
            <div className="mt-3 text-sm text-red-200 bg-red-950/40 px-3 py-2 rounded-lg border border-red-500/20 flex items-center">
              {errorMsg}
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
            <div className="columns-1 lg:columns-2 xl:columns-3 gap-6 space-y-6">
              {allDomains.map((domain) => {
                const categoryChannels = channels.filter(c => (c.domain || "uncategorized") === domain)
                  .sort((a,b) => a.channel_name.localeCompare(b.channel_name));
                
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
                    <div className="mb-3 flex items-center justify-between border-b border-border/30 pb-2 group/header">
                      {editingCategory === domain ? (
                        <input 
                           autoFocus
                           className="bg-background border border-border/50 rounded px-2 py-1 text-sm font-semibold w-[60%] outline-none focus:ring-1 focus:ring-primary/40"
                           value={editCategoryVal}
                           onChange={(e) => setEditCategoryVal(e.target.value)}
                           onBlur={() => handleRenameCategory(domain)}
                           onKeyDown={(e) => e.key === 'Enter' && handleRenameCategory(domain)}
                        />
                      ) : (
                        <div className="flex items-center gap-2 max-w-[70%]">
                         <h2 className="font-semibold text-foreground truncate">{displayTitle}</h2>
                         <button 
                           onClick={() => { setEditingCategory(domain); setEditCategoryVal(domain); }}
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
                          onClick={() => handleDeleteCategory(domain)}
                          className="opacity-0 group-hover/header:opacity-100 p-1 hover:bg-red-500/20 rounded text-muted-foreground hover:text-red-400 transition-all"
                          title="Delete Category completely"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                    
                    {/* Channel List */}
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
                            onClick={() => setPreviewChannel(ch)}
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
                  </div>
                );
              })}
            </div>
          </div>

          {/* Persistent Channel Preview Viewer Panel */}
          <div className="hidden md:flex w-[500px] lg:w-[600px] xl:w-[800px] shrink-0 border border-border/40 bg-card/20 rounded-xl flex-col shadow-inner overflow-hidden">
             {previewChannel ? (
                <>
                   <div className="bg-background/80 border-b border-border/40 p-4 shrink-0 flex items-center justify-between">
                      <div>
                         <h3 className="font-bold text-foreground text-sm truncate w-[300px]">{previewChannel.channel_name}</h3>
                         <p className="text-xs text-muted-foreground mt-0.5">Latest Uploads Viewer</p>
                      </div>
                      <a href={previewChannel.youtube_url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-xs bg-red-500/10 text-red-400 hover:bg-red-500/20 px-2 py-1 rounded transition-colors font-medium">
                         <Youtube className="w-3.5 h-3.5" /> URL
                      </a>
                   </div>
                   <div className="flex-1 w-full bg-black">
                       <iframe 
                          className="w-full h-full border-0"
                          src={`https://www.youtube.com/embed/videoseries?list=UU${previewChannel.channel_id.replace(/^UC/, '')}&playsinline=1&fs=0`}
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" 
                          title="Channel Uploads Playlist Viewer"
                       />
                   </div>
                </>
             ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground/60 space-y-4">
                    <PlaySquare className="w-12 h-12 text-border" />
                    <p className="text-sm">Click any channel in the Kanban board to preview their latest video uploads natively.</p>
                </div>
             )}
          </div>

        </div>
      )}
    </div>
  );
}
