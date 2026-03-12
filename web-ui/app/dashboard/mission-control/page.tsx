"use client";

import { Activity, BarChart3, Clock } from "lucide-react";

/**
 * Mission Control Dashboard - Milestone 1
 *
 * A centralized task monitoring interface with three main panels:
 * - Active Tasks: Currently running tasks and operations
 * - System Status: Health and status of system components
 * - Recent Events: Latest events and notifications
 */
export default function MissionControlPage() {
  return (
    <div className="flex h-full flex-col gap-6">
      {/* Page Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 ring-1 ring-blue-500/20">
          <Activity className="h-5 w-5 text-blue-400" />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Mission Control</h1>
          <p className="text-sm text-slate-500">Centralized task monitoring and system overview</p>
        </div>
      </div>

      {/* Main Content Grid */}
      <div className="grid flex-1 gap-4 lg:grid-cols-3">
        {/* Active Tasks Panel */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="mb-4 flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-medium text-slate-300">Active Tasks</h2>
          </div>
          <div className="flex flex-1 items-center justify-center py-8">
            <p className="text-sm text-slate-500">No active tasks</p>
          </div>
        </div>

        {/* System Status Panel */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="mb-4 flex items-center gap-2">
            <Activity className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-medium text-slate-300">System Status</h2>
          </div>
          <div className="flex flex-1 items-center justify-center py-8">
            <p className="text-sm text-slate-500">System status loading...</p>
          </div>
        </div>

        {/* Recent Events Panel */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="mb-4 flex items-center gap-2">
            <Clock className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-medium text-slate-300">Recent Events</h2>
          </div>
          <div className="flex flex-1 items-center justify-center py-8">
            <p className="text-sm text-slate-500">No recent events</p>
          </div>
        </div>
      </div>
    </div>
  );
}
