"use client";

import { useState, useEffect } from "react";
import { Trash2, Plus, Loader2, RefreshCw, Edit2, MessageSquare, FolderPlus, Server, Check } from "lucide-react";

type DiscordSubChannel = {
  channel_id: string;
  channel_name: string;
  is_watched: boolean;
};

type DiscordServer = {
  server_id: string;
  server_name: string;
  domain: string;
  icon_url: string;
  channels: DiscordSubChannel[];
};

type WatchlistResponse = {
  categories: string[];
  servers: DiscordServer[];
};

export default function CsiDiscordWatchlistPage() {
  const [servers, setServers] = useState<DiscordServer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Inputs
  const [inputVal, setInputVal] = useState("");
  const [isAddingCategory, setIsAddingCategory] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // Preview / Inspector
  const [activeServerId, setActiveServerId] = useState<string | null>(null);

  // Renaming Categories
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [editCategoryVal, setEditCategoryVal] = useState("");

  // Visual Drag State
  const [dragOverCategory, setDragOverCategory] = useState<string | null>(null);

  const loadWatchlist = async () => {
    setLoading(true);
    setErrorMsg("");
    try {
      const resp = await fetch("/api/v1/csi/discord");
      if (!resp.ok) {
        throw new Error(`Failed to fetch watchlist: ${resp.status}`);
      }
      const data = (await resp.json()) as WatchlistResponse;
      setServers(data.servers || []);
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

  const activeServer = servers.find(s => s.server_id === activeServerId) || null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputVal.trim()) return;

    setSubmitting(true);
    setErrorMsg("");
    try {
      if (isAddingCategory) {
        const resp = await fetch("/api/v1/csi/discord/categories", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: inputVal.trim() }),
        });
        if (!resp.ok) throw new Error("Failed to create category");
      } else {
        const resp = await fetch("/api/v1/csi/discord/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ server_id: inputVal.trim() }),
        });
        if (!resp.ok) {
           const b = await resp.json().catch(()=>({}));
           throw new Error(b.detail || "Failed to add server");
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

  const handleDeleteServer = async (serverId: string) => {
    if (!confirm("Remove this Discord Server from the watchlist completely?")) return;
    try {
      const resp = await fetch(`/api/v1/csi/discord/${encodeURIComponent(serverId)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error("Failed to remove server");
      
      setServers((prev) => prev.filter((s) => s.server_id !== serverId));
      if (activeServerId === serverId) setActiveServerId(null);
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
      const resp = await fetch(`/api/v1/csi/discord/categories/${encodeURIComponent(oldName)}`, {
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
    if (!confirm(`Warning: Delete "${name}"? This cascades and drops all tracked servers inside it! \n\nAre you sure?`)) return;
    try {
      const resp = await fetch(`/api/v1/csi/discord/categories/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error("Failed to delete category");
      
      if (activeServer?.domain === name) {
        setActiveServerId(null);
      }
      await loadWatchlist();
    } catch (err: any) {
      alert("Error deleting category: " + err.message);
    }
  };

  const handleToggleChannelStatus = async (channelId: string, currentState: boolean) => {
    if (!activeServerId) return;
    try {
      // Opt UI
      setServers(prev => prev.map(s => {
          if (s.server_id !== activeServerId) return s;
          return {
              ...s,
              channels: s.channels.map(ch => ch.channel_id === channelId ? { ...ch, is_watched: !currentState } : ch)
          };
      }));

      const resp = await fetch(`/api/v1/csi/discord/${encodeURIComponent(activeServerId)}/channels/${encodeURIComponent(channelId)}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ is_watched: !currentState })
      });
      if (!resp.ok) throw new Error("Toggle fail");
    } catch (err) {
        await loadWatchlist(); // revert
    }
  }

  // Kanban Drag Logic
  const handleDragStart = (e: React.DragEvent, serverId: string) => {
     e.dataTransfer.setData("server_id", serverId);
  }

  const handleDragOver = (e: React.DragEvent, cat: string) => {
      e.preventDefault();
      if (dragOverCategory !== cat) setDragOverCategory(cat);
  }

  const handleDragLeave = (e: React.DragEvent, cat: string) => {
      if (dragOverCategory === cat) setDragOverCategory(null);
  }

  const handleDrop = async (e: React.DragEvent, newCategory: string) => {
      e.preventDefault();
      setDragOverCategory(null);
      const serverId = e.dataTransfer.getData("server_id");
      if (!serverId) return;

      // Optimistic move
      setServers(prev => prev.map(s => s.server_id === serverId ? { ...s, domain: newCategory } : s));

      try {
          const resp = await fetch(`/api/v1/csi/discord/${encodeURIComponent(serverId)}`, {
              method: "PATCH",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({ domain: newCategory })
          });
          if (!resp.ok) throw new Error("Move failed");
      } catch (err: any) {
          alert("Error moving server: " + err.message);
          await loadWatchlist();
      }
  }

  const allDomains = Array.from(new Set([
    ...categories,
    ...servers.map(s => s.domain || "uncategorized")
  ])).sort((a, b) => {
    if (a === "uncategorized") return 1;
    if (b === "uncategorized") return -1;
    return a.localeCompare(b);
  });

  return (
    <div className="flex flex-col gap-6 w-full max-w-[1600px] mx-auto h-[calc(100vh-6rem)] relative overflow-hidden">
      {/* Header */}
      <div className="flex flex-col gap-4">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
              <MessageSquare className="h-6 w-6 text-indigo-500" />
              Discord CSI Watchlist
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Group your tracked Discord Servers and select specific channels for the intelligence crawler to listen to.
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

        {/* Input Bar */}
        <div className="rounded-xl border border-border/40 bg-card/20 p-4 backdrop-blur shadow-sm">
          <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-3">
            <div className="flex bg-background/50 border border-border/40 rounded-lg p-1">
              <button 
                type="button"
                onClick={() => setIsAddingCategory(false)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${!isAddingCategory ? 'bg-indigo-500 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <Server className="w-4 h-4 inline-block mr-1.5 mb-0.5" />
                Server
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
              placeholder={isAddingCategory ? "Enter custom group name..." : "Enter Discord Server ID..."}
              value={inputVal}
              onChange={(e) => setInputVal(e.target.value)}
              disabled={submitting}
              className="flex-1 rounded-lg border border-border/40 bg-background/50 px-4 py-2 text-sm text-foreground outline-none transition focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
            />
            <button
              type="submit"
              disabled={submitting || !inputVal.trim()}
              className={`flex items-center justify-center gap-2 rounded-lg px-5 py-2 text-sm font-medium text-white transition disabled:opacity-50 bg-indigo-500 hover:bg-indigo-600`}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              {isAddingCategory ? "Add Kanban Group" : "Fetch Server"}
            </button>
          </form>
          {errorMsg && <div className="mt-2 text-sm text-red-400">{errorMsg}</div>}
        </div>
      </div>

      {loading && servers.length === 0 ? (
         <div className="flex-1 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
         </div>
      ) : (
        <div className="flex gap-6 flex-1 min-h-0 overflow-hidden pb-4">
          
          {/* Left Kanban */}
          <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
            <div className="columns-1 lg:columns-2 gap-6 space-y-6">
              {allDomains.map((domain) => {
                const categoryServers = servers.filter(s => (s.domain || "uncategorized") === domain)
                  .sort((a,b) => a.server_name.localeCompare(b.server_name));
                const displayTitle = domain.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
                const isDragTarget = dragOverCategory === domain;

                return (
                  <div 
                    key={domain} 
                    onDragOver={(e) => handleDragOver(e, domain)}
                    onDragLeave={(e) => handleDragLeave(e, domain)}
                    onDrop={(e) => handleDrop(e, domain)}
                    className={`break-inside-avoid rounded-xl border p-4 transition-all duration-200 ${
                       isDragTarget ? 'border-indigo-500 bg-indigo-500/5 ring-1 ring-indigo-500/20' : 'border-border/40 bg-card/20'
                    }`}
                  >
                    <div className="mb-3 flex items-center justify-between border-b border-border/30 pb-2 group/header">
                      {editingCategory === domain ? (
                        <input 
                           autoFocus
                           className="bg-background border border-border/50 rounded px-2 py-1 text-sm font-semibold w-[60%] outline-none focus:ring-1 focus:ring-indigo-500/40"
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
                        <span className="rounded-md bg-indigo-500/10 px-2 py-0.5 text-xs font-medium text-indigo-400">
                          {categoryServers.length}
                        </span>
                        <button 
                          onClick={() => handleDeleteCategory(domain)}
                          className="opacity-0 group-hover/header:opacity-100 p-1 hover:bg-red-500/20 rounded text-muted-foreground hover:text-red-400 transition-all"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                    
                    <ul className="flex flex-col gap-2 min-h-[40px]">
                      {categoryServers.map((srv) => {
                        const trackedCount = srv.channels.filter(c => c.is_watched).length;
                        return (
                        <li 
                           key={srv.server_id} 
                           draggable
                           onDragStart={(e) => handleDragStart(e, srv.server_id)}
                           className={`group relative flex flex-col gap-2 p-3 rounded-lg border transition-all cursor-grab active:cursor-grabbing ${
                             activeServerId === srv.server_id 
                               ? 'border-indigo-500/50 bg-indigo-500/10 shadow-sm' 
                               : 'border-border/20 bg-background/30 hover:border-border/60 hover:bg-background/80'
                           }`}
                           onClick={() => setActiveServerId(srv.server_id)}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3 overflow-hidden">
                                {srv.icon_url ? (
                                    <img src={srv.icon_url} alt="icon" className="w-8 h-8 rounded-full bg-black/50 border border-white/5" />
                                ) : (
                                    <div className="w-8 h-8 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center font-bold text-xs uppercase">
                                       {srv.server_name.charAt(0)}
                                    </div>
                                )}
                                <div className="flex flex-col overflow-hidden">
                                   <span className="truncate font-semibold text-sm text-slate-200">{srv.server_name}</span>
                                   <span className="text-xs text-muted-foreground">{trackedCount} sub-channels active</span>
                                </div>
                            </div>
                            
                            <button
                              onClick={(e) => { e.stopPropagation(); handleDeleteServer(srv.server_id); }}
                              className="opacity-0 group-hover:opacity-100 p-1.5 text-muted-foreground hover:text-red-400 rounded-md hover:bg-red-400/10"
                            >
                               <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        </li>
                      )})}
                    </ul>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Right Configurator Panel */}
          <div className="hidden md:flex w-[400px] shrink-0 border border-border/40 bg-[#2b2d31] rounded-xl flex-col shadow-inner overflow-hidden">
             {activeServer ? (
                <>
                   {/* Discord Server Header Mock */}
                   <div className="p-4 shadow-sm border-b border-black/20 flex items-center gap-3 shrink-0 bg-[#2b2d31]">
                       {activeServer.icon_url ? (
                          <img src={activeServer.icon_url} alt="icon" className="w-12 h-12 rounded-xl shadow-sm" />
                       ) : (
                          <div className="w-12 h-12 rounded-xl bg-[#5865F2] flex items-center justify-center text-white font-bold text-lg">
                             {activeServer.server_name.charAt(0).toUpperCase()}
                          </div>
                       )}
                       <div className="flex flex-col">
                           <h3 className="font-bold text-white text-base truncate w-[260px]">{activeServer.server_name}</h3>
                           <p className="text-xs text-[#b5bac1] font-medium tracking-wide uppercase mt-0.5">Scraper Configuration</p>
                       </div>
                   </div>
                   
                   {/* Sub-Channel List Mock */}
                   <div className="flex-1 overflow-y-auto p-3 space-y-1 custom-scrollbar">
                        {activeServer.channels.length === 0 ? (
                           <div className="text-center py-8 text-[#949ba4] text-sm">No text channels found or token missing.</div>
                        ) : (
                           activeServer.channels.map(ch => (
                               <button
                                  key={ch.channel_id}
                                  onClick={() => handleToggleChannelStatus(ch.channel_id, ch.is_watched)}
                                  className={`w-full flex items-center justify-between p-2 rounded text-left transition-colors ${ch.is_watched ? 'bg-[#404249] text-white' : 'text-[#949ba4] hover:bg-[#35373c] hover:text-[#dbdee1]'}`}
                               >
                                  <div className="flex items-center gap-2 truncate">
                                      <span className="text-xl font-light text-[#80848e] select-none leading-none">#</span>
                                      <span className="truncate text-[15px] font-medium">{ch.channel_name}</span>
                                  </div>
                                  <div className={`w-5 h-5 rounded border flex items-center justify-center shrink-0 ${ch.is_watched ? 'bg-[#5865F2] border-[#5865F2]' : 'border-[#4e5058]'}`}>
                                      {ch.is_watched && <Check className="w-3.5 h-3.5 text-white" />}
                                  </div>
                               </button>
                           ))
                        )}
                   </div>
                </>
             ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground/60 space-y-4">
                    <Server className="w-12 h-12 text-border" />
                    <p className="text-sm">Select a server to view and configure its underlying tracking sub-channels.</p>
                </div>
             )}
          </div>

        </div>
      )}
    </div>
  );
}
