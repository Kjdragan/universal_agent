"use client";

import Image from "next/image";
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
    <div className="h-screen overflow-hidden bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100">
      <div className="mx-auto flex h-full max-w-full gap-4 p-4 md:p-6">
        <aside className="flex w-64 shrink-0 flex-col rounded-xl border border-slate-800/80 bg-slate-900/60 p-4 backdrop-blur">
          <div className="mb-4 border-b border-slate-800 pb-3">
            <div className="relative h-12 w-48 mb-2">
              <Image
                src="/simon_logo.png"
                alt="Simon"
                fill
                className="object-contain object-left"
              />
            </div>
            <p className="text-sm font-semibold pl-1">Operations Dashboard</p>
          </div>
          <nav className="flex-1 space-y-1 overflow-y-auto">
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
        <main className="flex h-full flex-1 flex-col overflow-hidden rounded-xl border border-slate-800/80 bg-slate-900/50 p-4 backdrop-blur md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
