"use client";

import { OpsProvider, SkillsSection } from "@/components/OpsDropdowns";

export default function DashboardSkillsPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col space-y-4">
        <div className="shrink-0">
          <h1 className="text-xl font-semibold tracking-tight">Skills</h1>
          <p className="text-sm text-slate-400">Skill discovery and enablement status.</p>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          <SkillsSection />
        </div>
      </div>
    </OpsProvider>
  );
}
