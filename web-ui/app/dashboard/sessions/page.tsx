"use client";

import Link from "next/link";
import { OpsProvider, SessionsSection } from "@/components/OpsDropdowns";

export default function DashboardSessionsPage() {
  return (
    <OpsProvider>
      <div className="flex h-full flex-col space-y-2">
        <div className="flex items-center justify-end">
          <Link
            href="/"
            className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
          >
            Back to Home
          </Link>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70">
          <SessionsSection variant="full" />
        </div>
      </div>
    </OpsProvider>
  );
}
