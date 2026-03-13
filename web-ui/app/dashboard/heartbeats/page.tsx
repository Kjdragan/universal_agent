import React from "react";
import { OpsProvider, HeartbeatsSection } from "@/components/OpsDropdowns";

export default function HeartbeatsPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/40 px-4">
          <h1 className="text-sm font-semibold text-slate-200">System Heartbeat</h1>
        </header>

        <main className="flex-1 overflow-auto p-4 md:p-6 lg:p-8">
          <div className="mx-auto max-w-5xl space-y-6">
            <HeartbeatsSection variant="full" />
          </div>
        </main>
      </div>
    </OpsProvider>
  );
}
