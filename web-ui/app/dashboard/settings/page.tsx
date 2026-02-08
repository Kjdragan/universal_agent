"use client";

import { OpsProvider, OpsConfigSection, SessionContinuityWidget } from "@/components/OpsDropdowns";
import { SessionGovernancePanel } from "@/components/dashboard/SessionGovernancePanel";

export default function DashboardSettingsPage() {
  return (
    <OpsProvider>
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-slate-400">Control plane config and schema.</p>
        </div>
        <SessionGovernancePanel />
        <div className="rounded-xl border border-slate-800 bg-slate-900/70">
          <SessionContinuityWidget />
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70">
          <OpsConfigSection />
        </div>
      </div>
    </OpsProvider>
  );
}
