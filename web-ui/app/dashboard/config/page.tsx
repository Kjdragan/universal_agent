"use client";

import Link from "next/link";
import { OpsConfigSection, OpsProvider } from "@/components/OpsDropdowns";

export default function DashboardConfigPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Config</h1>
            <p className="text-sm text-slate-400">Ops configuration, schema, sync controls, and guarded destructive actions.</p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
          >
            Back to Home
          </Link>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          <OpsConfigSection variant="full" />
        </div>
      </div>
    </OpsProvider>
  );
}
