"use client";

import Link from "next/link";
import { OpsProvider } from "@/components/OpsDropdowns";
import { SessionGovernancePanel } from "@/components/dashboard/SessionGovernancePanel";

export default function DashboardSettingsPage() {
  return (
    <OpsProvider>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
            <p className="text-sm text-slate-400">Governance controls and operator entry points.</p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
          >
            Back to Home
          </Link>
        </div>
        <SessionGovernancePanel />
        <div className="grid gap-3 md:grid-cols-2">
          <Link
            href="/dashboard/config"
            className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-cyan-600/40 hover:bg-slate-900"
          >
            <div className="text-sm font-semibold">Config</div>
            <div className="text-xs text-slate-400 mt-1">Ops config, schema, remote sync toggle, and danger-zone controls.</div>
          </Link>
          <Link
            href="/dashboard/continuity"
            className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-cyan-600/40 hover:bg-slate-900"
          >
            <div className="text-sm font-semibold">Continuity</div>
            <div className="text-xs text-slate-400 mt-1">Runtime and transport continuity metrics with operational alerts.</div>
          </Link>
        </div>
      </div>
    </OpsProvider>
  );
}
