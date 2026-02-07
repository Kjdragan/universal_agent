"use client";

import { OpsProvider, SkillsSection } from "@/components/OpsDropdowns";

export default function DashboardSkillsPage() {
  return (
    <OpsProvider>
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Skills</h1>
          <p className="text-sm text-slate-400">Skill discovery and enablement status.</p>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70">
          <SkillsSection />
        </div>
      </div>
    </OpsProvider>
  );
}
