"use client";

import { OpsProvider, CalendarSection } from "@/components/OpsDropdowns";

export default function DashboardCalendarPage() {
  return (
    <OpsProvider>
      <div className="space-y-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Calendar</h1>
          <p className="text-sm text-slate-400">Cron + heartbeat scheduling view and controls.</p>
        </div>
        <CalendarSection />
      </div>
    </OpsProvider>
  );
}

