import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { GitMerge, RefreshCw, GitCommit, PlayCircle, PlusCircle, CheckCircle } from "lucide-react";

export type PipelineStatsData = {
  status: string;
  total_events: number;
  by_type: Record<string, number>;
  recent_events: Array<{
    task_id: string;
    event_type: string;
    timestamp: string;
    details?: string;
  }>;
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

  if (error || !data) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4 flex flex-col items-center justify-center">
        <p className="text-sm text-red-400 mb-2">Error loading pipeline stats</p>
        <button onClick={load} className="text-xs text-primary hover:underline flex items-center gap-1">
          <RefreshCw className="h-3 w-3" /> Retry
        </button>
      </div>
    );
  }

  const parseEventTypeIcon = (type: string) => {
    if (type.includes('create')) return <PlusCircle className="h-3 w-3 text-primary_container" />;
    if (type.includes('start') || type.includes('run')) return <PlayCircle className="h-3 w-3 text-secondary_container" />;
    if (type.includes('complete') || type.includes('done')) return <CheckCircle className="h-3 w-3 text-green-400" />;
    return <GitCommit className="h-3 w-3 text-muted-foreground" />;
  };

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4 flex flex-col h-full">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitMerge className="h-4 w-4 text-secondary_container" />
          <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">Pipeline & Routing Stats</h2>
        </div>
        <span className="text-xs text-muted-foreground font-mono">{data.total_events} events</span>
      </div>

      <div className="grid grid-cols-2 gap-2 mb-4">
        {Object.entries(data.by_type).slice(0, 4).map(([type, count]) => (
          <div key={type} className="bg-white/5 border border-white/5 p-2">
            <p className="text-[10px] uppercase text-muted-foreground tracking-widest truncate" title={type}>{type.replace('task_', '')}</p>
            <p className="text-lg font-mono text-foreground">{count}</p>
          </div>
        ))}
      </div>

      <div className="flex-1">
        <h3 className="text-[10px] uppercase tracking-widest text-muted-foreground mb-2">Recent Events</h3>
        {data.recent_events.length === 0 ? (
          <p className="text-xs text-muted-foreground italic">No recent events</p>
        ) : (
          <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
            {data.recent_events.map((event, idx) => (
              <div key={idx} className="flex items-start gap-2 text-xs p-1.5 hover:bg-white/5 items-center">
                {parseEventTypeIcon(event.event_type)}
                <div className="min-w-0 flex-1 flex items-center justify-between">
                  <span className="text-foreground/80 truncate pr-2 font-mono text-[10px]">{event.task_id.split('-')[0]}...</span>
                  <span className="text-muted-foreground text-[10px] uppercase">{event.event_type.replace('task_', '')}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
