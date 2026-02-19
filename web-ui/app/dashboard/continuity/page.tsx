"use client";

import Link from "next/link";
import { OpsProvider, SessionContinuityWidget } from "@/components/OpsDropdowns";

export default function DashboardContinuityPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Continuity</h1>
            <p className="text-sm text-slate-400">Runtime and transport continuity metrics, success rates, and active alerts.</p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
          >
            Back to Home
          </Link>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          <SessionContinuityWidget variant="full" />
        </div>
      </div>
    </OpsProvider>
  );
}
