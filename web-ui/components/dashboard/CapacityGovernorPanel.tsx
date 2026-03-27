import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { Activity, Server, Cpu, Clock, RefreshCw } from "lucide-react";

export type CapacityGovernorData = {
  status: string;
  max_concurrent_operations: number;
  active_operations: number;
  backoff: {
    active: boolean;
    until: string | null;
    reason: string | null;
    sleep_multiplier: number;
  };
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

  const utilizationPercent = data.max_concurrent_operations > 0 
    ? (data.active_operations / data.max_concurrent_operations) * 100 
    : 0;

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-medium text-foreground/80 tracking-wide uppercase">Capacity Governor</h2>
        </div>
        <span className={`px-2 py-0.5 text-xs rounded-none ${data.status === 'healthy' ? 'bg-primary/20 text-primary' : 'bg-red-400/20 text-red-400'}`}>
          {data.status}
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-muted-foreground uppercase tracking-widest text-[10px]">Concurrency limit</span>
            <span className="font-mono">{data.active_operations} / {data.max_concurrent_operations}</span>
          </div>
          <div className="h-1.5 w-full bg-white/5 overflow-hidden">
            <div 
              className={`h-full ${utilizationPercent > 80 ? 'bg-secondary_container' : 'bg-primary_container'} transition-all`}
              style={{ width: `${Math.min(utilizationPercent, 100)}%` }}
            />
          </div>
        </div>

        <div className={`p-3 border ${data.backoff.active ? 'border-secondary_container/50 bg-secondary_container/10' : 'border-white/5 bg-white/5'}`}>
          <div className="flex items-center gap-2 mb-2">
            <Activity className={`h-4 w-4 ${data.backoff.active ? 'text-secondary_container animate-pulse' : 'text-muted-foreground'}`} />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-foreground">Backoff State</h3>
            <span className={`ml-auto text-xs ${data.backoff.active ? 'text-secondary_container' : 'text-muted-foreground'}`}>
              {data.backoff.active ? 'ACTIVE' : 'INACTIVE'}
            </span>
          </div>
          
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <p className="text-muted-foreground uppercase text-[10px] tracking-wider mb-0.5">Multiplier</p>
              <p className="font-mono">{data.backoff.sleep_multiplier.toFixed(1)}x</p>
            </div>
            <div>
              <p className="text-muted-foreground uppercase text-[10px] tracking-wider mb-0.5">Until</p>
              <p className="font-mono">{data.backoff.until ? new Date(data.backoff.until).toLocaleTimeString() : '--'}</p>
            </div>
          </div>
          
          {data.backoff.active && data.backoff.reason && (
            <div className="mt-2 text-xs text-secondary_container">
              Reason: {data.backoff.reason}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
