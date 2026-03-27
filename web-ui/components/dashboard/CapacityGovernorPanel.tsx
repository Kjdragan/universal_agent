import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { Activity, Server, Clock, RefreshCw } from "lucide-react";

export type CapacityGovernorData = {
  max_concurrent: number;
  active_slots: number;
  available_slots: number;
  in_backoff: boolean;
  backoff_remaining_seconds: number;
  consecutive_429s: number;
  total_429s: number;
  total_requests: number;
  total_shed: number;
  last_429_at: string | null;
};

export function CapacityGovernorPanel({ refreshKey }: { refreshKey: number }) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<CapacityGovernorData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/dashboard/gateway/api/v1/dashboard/capacity", { cache: "no-store" });
      if (!res.ok) throw new Error(`Failed to load: ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (err: any) {
      setError(err.message || "Failed to load capacity overview");
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
            <Server className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">Capacity Governor</h2>
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
        <p className="text-sm text-red-400 mb-2">Error loading capacity</p>
        <button onClick={load} className="text-xs text-primary hover:underline flex items-center gap-1">
          <RefreshCw className="h-3 w-3" /> Retry
        </button>
      </div>
    );
  }

  const utilizationPercent = data.max_concurrent > 0 
    ? (data.active_slots / data.max_concurrent) * 100 
    : 0;
  
  const statusLabel = data.in_backoff ? 'Throttled' : 'Healthy';

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">Capacity Governor</h2>
        </div>
        <span className={`px-2 py-0.5 text-xs rounded-none uppercase tracking-wider font-mono ${data.in_backoff ? 'bg-red-400/20 text-red-400' : 'bg-primary/20 text-primary'}`}>
          {statusLabel}
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-muted-foreground uppercase tracking-widest text-[10px]">Concurrency limit</span>
            <span className="font-mono">{data.active_slots} / {data.max_concurrent}</span>
          </div>
          <div className="h-1.5 w-full bg-white/5 overflow-hidden">
            <div 
              className={`h-full ${utilizationPercent > 80 ? 'bg-secondary_container' : 'bg-primary_container'} transition-all`}
              style={{ width: `${Math.min(utilizationPercent, 100)}%` }}
            />
          </div>
        </div>

        <div className={`p-3 border ${data.in_backoff ? 'border-secondary_container/50 bg-secondary_container/10' : 'border-white/5 bg-white/5'}`}>
          <div className="flex items-center gap-2 mb-2">
            <Activity className={`h-4 w-4 ${data.in_backoff ? 'text-secondary_container animate-pulse' : 'text-muted-foreground'}`} />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-foreground">Backoff State</h3>
            <span className={`ml-auto font-mono text-xs ${data.in_backoff ? 'text-secondary_container' : 'text-muted-foreground'}`}>
              {data.in_backoff ? 'ACTIVE' : 'INACTIVE'}
            </span>
          </div>
          
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <p className="text-muted-foreground uppercase text-[10px] tracking-wider mb-0.5">Consecutive 429s</p>
              <p className="font-mono">{data.consecutive_429s}</p>
            </div>
            <div>
              <p className="text-muted-foreground uppercase text-[10px] tracking-wider mb-0.5">Remaining time</p>
              <p className="font-mono">{data.in_backoff ? `${data.backoff_remaining_seconds}s` : '--'}</p>
            </div>
          </div>
        </div>
        
        <div className="grid grid-cols-3 gap-2 text-xs border border-white/5 bg-white/5 p-2">
            <div>
              <p className="text-muted-foreground uppercase text-[9px] tracking-wider mb-0.5">Total Req</p>
              <p className="font-mono text-[10px]">{data.total_requests}</p>
            </div>
            <div>
              <p className="text-muted-foreground uppercase text-[9px] tracking-wider mb-0.5">Total 429s</p>
              <p className="font-mono text-[10px]">{data.total_429s}</p>
            </div>
            <div>
              <p className="text-muted-foreground uppercase text-[9px] tracking-wider mb-0.5">Shed Count</p>
              <p className="font-mono text-[10px]">{data.total_shed}</p>
            </div>
        </div>
      </div>
    </div>
  );
}
