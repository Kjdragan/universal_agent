"use client";

import Link from "next/link";
import { OpsProvider, SkillsSection } from "@/components/OpsDropdowns";

export default function DashboardSkillsPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col space-y-4">
        <div className="shrink-0 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Skills</h1>
            <p className="text-sm text-muted-foreground">Skill discovery and enablement status.</p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-primary/30 bg-primary/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-primary/90 hover:bg-primary/25"
          >
            Back to Home
          </Link>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-background/70">
          <SkillsSection variant="full" />
        </div>
      </div>
    </OpsProvider>
  );
}
