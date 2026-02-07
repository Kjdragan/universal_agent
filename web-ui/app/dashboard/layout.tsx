"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/dashboard/chat", label: "Chat" },
  { href: "/dashboard/skills", label: "Skills" },
  { href: "/dashboard/cron-jobs", label: "Cron Jobs" },
  { href: "/dashboard/channels", label: "Channels" },
  { href: "/dashboard/settings", label: "Settings" },
];

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100">
      <div className="mx-auto flex max-w-[1400px] gap-4 p-4 md:p-6">
        <aside className="w-64 shrink-0 rounded-xl border border-slate-800/80 bg-slate-900/60 p-4 backdrop-blur">
          <div className="mb-4 border-b border-slate-800 pb-3">
            <p className="text-[11px] uppercase tracking-[0.22em] text-slate-400">Universal Agent</p>
            <p className="mt-1 text-sm font-semibold">Operations Dashboard</p>
          </div>
          <nav className="space-y-1">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={[
                    "block rounded-lg px-3 py-2 text-sm transition",
                    active
                      ? "bg-cyan-500/15 text-cyan-200 ring-1 ring-cyan-500/30"
                      : "text-slate-300 hover:bg-slate-800/70",
                  ].join(" ")}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </aside>
        <main className="min-h-[calc(100vh-3rem)] flex-1 rounded-xl border border-slate-800/80 bg-slate-900/50 p-4 backdrop-blur md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
