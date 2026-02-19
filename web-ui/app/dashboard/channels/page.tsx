"use client";

import Link from "next/link";
import { OpsProvider, ChannelsSection } from "@/components/OpsDropdowns";

export default function DashboardChannelsPage() {
  return (
    <OpsProvider>
      <div className="space-y-4 h-full flex flex-col">
        <div className="flex items-center justify-between">
          <div>
          <h1 className="text-xl font-semibold tracking-tight">Channels</h1>
          <p className="text-sm text-slate-400">Connected pathways and health probes.</p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
          >
            Back to Home
          </Link>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 min-h-0 flex-1 overflow-hidden">
          <ChannelsSection variant="full" />
        </div>
      </div>
    </OpsProvider>
  );
}
