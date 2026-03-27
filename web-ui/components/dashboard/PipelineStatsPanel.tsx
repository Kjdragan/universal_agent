import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { GitMerge, RefreshCw } from "lucide-react";

export type PipelineStatsData = {
  stats: {
    total_tasks: number;
    in_backlog: number;
    in_refinement: number;
    human_approval: number;
    dispatch_eligible: number;
  };
};

export function PipelineStatsPanel({ refreshKey }: { refreshKey: number }) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<PipelineStatsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/dashboard/gateway/api/v1/dashboard/pipeline-stats", { cache: "no-store" });
      if (!res.ok) throw new Error(`Failed to load: ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (err: any) {
      setError(err.message || "Failed to load pipeline stats");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <GitMerge className="h-4 w-4 text-secondary_container" />
            <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">Pipeline Stats</h2>
          </div>
        </div>
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="animate-pulse rounded bg-card/50 h-6 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !data || !data.stats) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4 flex flex-col items-center justify-center">
        <p className="text-sm text-red-400 mb-2">Error loading pipeline stats</p>
        <button onClick={load} className="text-xs text-primary hover:underline flex items-center gap-1">
          <RefreshCw className="h-3 w-3" /> Retry
        </button>
      </div>
    );
  }

  const { stats } = data;

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4 flex flex-col h-full">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitMerge className="h-4 w-4 text-secondary_container" />
          <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">Pipeline & Routing Stats</h2>
        </div>
        <span className="text-xs text-muted-foreground font-mono">{stats.total_tasks} total</span>
      </div>

      <div className="grid grid-cols-2 gap-2 mb-4">
        <div className="bg-white/5 border border-white/5 p-2 flex flex-col justify-between">
          <p className="text-[10px] uppercase text-muted-foreground tracking-widest truncate" title="backlog">Backlog</p>
          <p className="text-lg font-mono text-foreground mt-1">{stats.in_backlog}</p>
        </div>
        <div className="bg-white/5 border border-white/5 p-2 flex flex-col justify-between">
          <p className="text-[10px] uppercase text-muted-foreground tracking-widest truncate" title="refinement">Refinement</p>
          <p className="text-lg font-mono text-kcd-cyan mt-1">{stats.in_refinement}</p>
        </div>
        <div className="bg-white/5 border border-white/5 p-2 flex flex-col justify-between">
          <p className="text-[10px] uppercase text-muted-foreground tracking-widest truncate" title="human approval">Human Approval</p>
          <p className="text-lg font-mono text-kcd-amber mt-1">{stats.human_approval}</p>
        </div>
        <div className="bg-white/5 border border-white/5 p-2 flex flex-col justify-between">
          <p className="text-[10px] uppercase text-muted-foreground tracking-widest truncate" title="dispatch eligible">Dispatch Eligible</p>
          <p className="text-lg font-mono text-green-400 mt-1">{stats.dispatch_eligible}</p>
        </div>
      </div>
      <div className="flex-1" />
    </div>
  );
}
