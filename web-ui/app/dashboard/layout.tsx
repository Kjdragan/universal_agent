"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

const NAV_ITEMS: { href: string; label: string; external?: boolean }[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/dashboard/chat", label: "Chat Launch" },
  { href: "/dashboard/skills", label: "Skills" },
  { href: "/dashboard/calendar", label: "Calendar" },
  { href: "/dashboard/approvals", label: "Approvals" },
  { href: "/dashboard/cron-jobs", label: "Cron Jobs" },
  { href: "/dashboard/channels", label: "Channels" },
  { href: "/dashboard/settings", label: "Settings" },
  { href: "/files/", label: "File Browser", external: true },
];

type DashboardAuthSession = {
  authenticated: boolean;
  auth_required: boolean;
  owner_id: string;
  expires_at?: number | null;
};

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [session, setSession] = useState<DashboardAuthSession | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [ownerId, setOwnerId] = useState("owner_primary");

  const loadAuthSession = useCallback(async () => {
    setLoadingAuth(true);
    setAuthError(null);
    try {
      const response = await fetch("/api/dashboard/auth/session", { cache: "no-store" });
      const data = (await response.json()) as DashboardAuthSession;
      if (!response.ok && response.status !== 401) {
        throw new Error((data as { detail?: string }).detail || `Auth check failed (${response.status})`);
      }
      setSession(data);
      setOwnerId((prev) => prev || data.owner_id || "owner_primary");
    } catch (error) {
      setSession(null);
      setAuthError((error as Error).message);
    } finally {
      setLoadingAuth(false);
    }
  }, []);

  useEffect(() => {
    void loadAuthSession();
  }, [loadAuthSession]);

  const handleLogin = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      setAuthError(null);
      try {
        const response = await fetch("/api/dashboard/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password, owner_id: ownerId }),
        });
        if (!response.ok) {
          const data = (await response.json().catch(() => ({}))) as { detail?: string };
          throw new Error(data.detail || "Invalid credentials.");
        }
        setPassword("");
        await loadAuthSession();
      } catch (error) {
        setAuthError((error as Error).message);
      }
    },
    [password, ownerId, loadAuthSession],
  );

  const handleLogout = useCallback(async () => {
    await fetch("/api/dashboard/auth/logout", { method: "POST" });
    await loadAuthSession();
  }, [loadAuthSession]);

  if (loadingAuth) {
    return (
      <div className="flex h-screen items-center justify-center bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-200">
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 px-6 py-5 text-sm text-slate-300">
          Verifying dashboard session...
        </div>
      </div>
    );
  }

  if (!session?.authenticated) {
    return (
      <div className="flex h-screen items-center justify-center bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100 p-4">
        <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <h1 className="text-lg font-semibold">Dashboard Access</h1>
          <p className="mt-1 text-sm text-slate-400">Sign in to access operations controls and session data.</p>
          <form onSubmit={handleLogin} className="mt-4 space-y-3">
            <label className="block text-xs text-slate-400">
              Owner ID
              <input
                value={ownerId}
                onChange={(event) => setOwnerId(event.target.value)}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500"
              />
            </label>
            <label className="block text-xs text-slate-400">
              Password
              <input
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500"
              />
            </label>
            {authError && (
              <div className="rounded-md border border-rose-800/70 bg-rose-900/20 px-3 py-2 text-xs text-rose-200">
                {authError}
              </div>
            )}
            <div className="flex items-center gap-2">
              <button
                type="submit"
                className="rounded-md border border-cyan-700 bg-cyan-600/20 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-600/30"
              >
                Sign In
              </button>
              <button
                type="button"
                onClick={loadAuthSession}
                className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800/70"
              >
                Retry
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100">
      <div className="mx-auto flex h-full max-w-full gap-4 p-4 md:p-6">
        <aside className="flex w-64 shrink-0 flex-col rounded-xl border border-slate-800/80 bg-slate-900/60 p-4 backdrop-blur">
          <div className="mb-4 border-b border-slate-800 pb-3">
            <div className="relative h-12 w-48 mb-2">
              <Image
                src="/simon_logo_v2.png"
                alt="Simon"
                fill
                className="object-contain object-left"
              />
            </div>
            <p className="text-sm font-semibold pl-1">Operations Dashboard</p>
          </div>
          <nav className="flex-1 space-y-1 overflow-y-auto">
            {NAV_ITEMS.map((item) => {
              const active = !item.external && pathname === item.href;
              if (item.external) {
                return (
                  <a
                    key={item.href}
                    href={item.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between rounded-lg px-3 py-2 text-sm text-slate-300 transition hover:bg-slate-800/70"
                  >
                    {item.label}
                    <span className="text-[10px] text-slate-500">&#x2197;</span>
                  </a>
                );
              }
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
          <div className="mt-4 border-t border-slate-800 pt-3">
            <p className="text-[11px] text-slate-500">Owner: {session.owner_id}</p>
            {session.auth_required && (
              <button
                type="button"
                onClick={handleLogout}
                className="mt-2 w-full rounded-md border border-slate-700 bg-slate-900/60 px-2 py-1.5 text-xs text-slate-300 hover:bg-slate-800/70"
              >
                Sign Out
              </button>
            )}
          </div>
        </aside>
        <main className="flex h-full flex-1 flex-col overflow-hidden rounded-xl border border-slate-800/80 bg-slate-900/50 p-4 backdrop-blur md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
