"use client";

import { OpsProvider, ChannelsSection } from "@/components/OpsDropdowns";

export default function DashboardChannelsPage() {
  return (
    <OpsProvider>
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Channels</h1>
          <p className="text-sm text-slate-400">Connected pathways and health probes.</p>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70">
          <ChannelsSection />
        </div>
      </div>
    </OpsProvider>
  );
}
