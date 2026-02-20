"use client";

import { OpsProvider, SessionsSection } from "@/components/OpsDropdowns";

export default function DashboardSessionsPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          <SessionsSection variant="full" showBackToHome />
        </div>
      </div>
    </OpsProvider>
  );
}
