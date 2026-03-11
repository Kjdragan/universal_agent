"use client";

import { OpsProvider } from "@/components/OpsDropdowns";
import { SecurityDashboardTab } from "@/components/dashboard/SecurityDashboardTab";

export default function DashboardSecurityPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          {/* Header to match other pages */}
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/40 p-3">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-200 flex items-center gap-2">
              <span className="text-emerald-500">🛡️</span> Security Operations
            </h2>
            <a
              href="/"
              className="text-xs uppercase tracking-wider text-cyan-500 hover:text-cyan-400 font-bold"
            >
              ◀ Back to Hub
            </a>
          </div>
          {/* Main Tab */}
          <div className="flex-1 overflow-auto relative">
            <SecurityDashboardTab />
          </div>
        </div>
      </div>
    </OpsProvider>
  );
}
