"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import SystemCommandBar from "@/components/dashboard/SystemCommandBar";

const NAV_ITEMS: { href: string; label: string; external?: boolean; primary?: boolean }[] = [
  { href: "/", label: "← Back to Main App", primary: true },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/dashboard/chat", label: "Chat Launch" },
  { href: "/dashboard/sessions", label: "Sessions" },
  { href: "/dashboard/skills", label: "Skills" },
  { href: "/dashboard/calendar", label: "Calendar" },
  { href: "/dashboard/tutorials", label: "Tutorials" },
  { href: "/dashboard/events", label: "Events" },
  { href: "/dashboard/csi", label: "CSI Feed" },
  { href: "/dashboard/approvals", label: "Approvals" },
  { href: "/dashboard/cron-jobs", label: "Cron Jobs" },
  { href: "/dashboard/channels", label: "Channels" },
  { href: "/dashboard/config", label: "Config" },
  { href: "/dashboard/continuity", label: "Continuity" },
  { href: "/dashboard/todolist", label: "To Do List" },
  { href: "/dashboard/settings", label: "Settings" },
  { href: "/files", label: "File Browser", external: true },
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
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

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

  useEffect(() => {
    setIsMobileSidebarOpen(false);
  }, [pathname]);

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
  const showSystemCommandBar = Boolean(
    pathname &&
    !pathname.startsWith("/dashboard/chat") &&
    !pathname.startsWith("/dashboard/csi")
  );

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
    <div className="h-screen flex flex-col bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100">
      {/* Mobile Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-800 bg-slate-900/80 px-4 backdrop-blur md:hidden">
        <div className="text-sm font-semibold tracking-wide text-slate-200">Operations</div>
        <button
          onClick={() => setIsMobileSidebarOpen(!isMobileSidebarOpen)}
          className="rounded-md border border-slate-700 bg-slate-800/60 p-2 text-slate-300"
        >
          {isMobileSidebarOpen ? "✕" : "☰"}
        </button>
      </header>

      <div className="flex h-full flex-1 overflow-hidden p-0 md:p-6 lg:gap-4">
        {/* Sidebar Overlay (Mobile) */}
        {isMobileSidebarOpen && (
          <div
            className="fixed inset-0 z-40 bg-slate-950/60 backdrop-blur-sm md:hidden"
            onClick={() => setIsMobileSidebarOpen(false)}
          />
        )}

        <aside
          className={[
            "fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r border-slate-800 bg-slate-900/95 p-3 transition-transform duration-300 md:relative md:inset-0 md:flex md:w-64 md:translate-x-0 md:rounded-xl md:border md:bg-slate-900/60 md:backdrop-blur",
            isMobileSidebarOpen ? "translate-x-0" : "-translate-x-full"
          ].join(" ")}
        >
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
                      : item.primary
                        ? "bg-cyan-600/10 text-cyan-400 font-bold hover:bg-cyan-600/20 border border-cyan-700/30"
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

        <main className="flex h-full flex-1 flex-col overflow-y-auto overflow-x-hidden scrollbar-thin bg-slate-900/50 p-4 pr-2 backdrop-blur md:rounded-xl md:border md:border-slate-800/80 md:p-6 md:pr-4">
          {showSystemCommandBar && <SystemCommandBar sourcePage={pathname || "/dashboard"} />}
          {children}
        </main>
      </div>
    </div>
  );
}
