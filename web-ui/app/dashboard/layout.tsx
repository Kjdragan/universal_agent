"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  LayoutDashboard,
  Building2,
  CheckCircle,
  ListTodo,
  MessageSquare,
  Send,
  CalendarDays,
  Bell,
  Radio,
  GraduationCap,
  Clock,
  Settings,
  Wrench,
  FolderOpen,
  Clipboard,
  Menu,
  X,
  LogOut,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";
import SystemCommandBar from "@/components/dashboard/SystemCommandBar";

/* ------------------------------------------------------------------ */
/* Navigation structure — grouped by daily-use priority                */
/* ------------------------------------------------------------------ */

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  external?: boolean;
  requiresHeadquarters?: boolean;
  badge?: string;
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

const NAV_GROUPS: NavGroup[] = [
  {
    title: "Operations",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/dashboard/corporation", label: "Corporation", icon: Building2, requiresHeadquarters: true },
      { href: "/dashboard/approvals", label: "Approvals", icon: CheckCircle },
      { href: "/dashboard/todolist", label: "To Do List", icon: ListTodo },
      { href: "/dashboard/sessions", label: "Sessions", icon: Clipboard },
    ],
  },
  {
    title: "Agent",
    items: [
      { href: "/dashboard/telegram", label: "Telegram", icon: Send },
      { href: "/dashboard/calendar", label: "Calendar", icon: CalendarDays },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { href: "/dashboard/events", label: "Events", icon: Bell },
      { href: "/dashboard/csi", label: "CSI Feed", icon: Radio },
      { href: "/dashboard/tutorials", label: "Tutorials", icon: GraduationCap },
    ],
  },
  {
    title: "System",
    items: [
      { href: "/dashboard/cron-jobs", label: "Cron Jobs", icon: Clock },
      { href: "/dashboard/config", label: "Configuration", icon: Settings },
      { href: "/dashboard/skills", label: "Skills", icon: Wrench },
      { href: "/files", label: "File Browser", icon: FolderOpen, external: true },
    ],
  },
];

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

type DashboardAuthSession = {
  authenticated: boolean;
  auth_required: boolean;
  owner_id: string;
  expires_at?: number | null;
};

type FactoryCapabilitiesResponse = {
  factory?: {
    factory_role?: string;
    gateway_mode?: string;
  };
};

/* ------------------------------------------------------------------ */
/* Layout                                                              */
/* ------------------------------------------------------------------ */

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [session, setSession] = useState<DashboardAuthSession | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [ownerId, setOwnerId] = useState("owner_primary");
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [showCorporationNav, setShowCorporationNav] = useState(false);
  const [commandBarVisible, setCommandBarVisible] = useState(false);
  const commandBarTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadAuthSession = useCallback(async () => {
    setLoadingAuth(true);
    setAuthError(null);
    let authenticated = false;
    try {
      const response = await fetch("/api/dashboard/auth/session", { cache: "no-store" });
      const data = (await response.json()) as DashboardAuthSession;
      if (!response.ok && response.status !== 401) {
        throw new Error((data as { detail?: string }).detail || `Auth check failed (${response.status})`);
      }
      setSession(data);
      setOwnerId((prev) => prev || data.owner_id || "owner_primary");
      authenticated = Boolean(data.authenticated);
    } catch (error) {
      setSession(null);
      setAuthError((error as Error).message);
      setShowCorporationNav(false);
    } finally {
      setLoadingAuth(false);
    }

    if (authenticated) {
      try {
        const ac = new AbortController();
        const timer = setTimeout(() => ac.abort(), 5000);
        const capsRes = await fetch("/api/dashboard/gateway/api/v1/factory/capabilities", {
          cache: "no-store",
          signal: ac.signal,
        });
        clearTimeout(timer);
        if (capsRes.ok) {
          const capsData = (await capsRes.json()) as FactoryCapabilitiesResponse;
          const role = String(capsData?.factory?.factory_role || "").trim().toUpperCase();
          const gatewayMode = String(capsData?.factory?.gateway_mode || "").trim().toLowerCase();
          setShowCorporationNav(role === "HEADQUARTERS" && gatewayMode === "full");
        } else {
          setShowCorporationNav(false);
        }
      } catch {
        setShowCorporationNav(false);
      }
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

  /* ---------------------------------------------------------------- */
  /* Loading state                                                     */
  /* ---------------------------------------------------------------- */

  if (loadingAuth) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a0f] text-slate-200">
        <div className="flex items-center gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.03] px-6 py-5 text-sm text-slate-400 backdrop-blur">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-blue-400" />
          Verifying session...
        </div>
      </div>
    );
  }

  /* ---------------------------------------------------------------- */
  /* Login screen                                                      */
  /* ---------------------------------------------------------------- */

  if (!session?.authenticated) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a0f] text-slate-100 p-4">
        <div className="w-full max-w-sm">
          <div className="mb-8 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-500/10 ring-1 ring-blue-500/20">
              <LayoutDashboard className="h-6 w-6 text-blue-400" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">Universal Agent</h1>
            <p className="mt-1 text-sm text-slate-500">Sign in to operations dashboard</p>
          </div>
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6 backdrop-blur">
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Owner ID</label>
                <input
                  value={ownerId}
                  onChange={(event) => setOwnerId(event.target.value)}
                  className="w-full rounded-xl border border-white/[0.06] bg-white/[0.03] px-4 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-600 focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Password</label>
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  className="w-full rounded-xl border border-white/[0.06] bg-white/[0.03] px-4 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-600 focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                />
              </div>
              {authError && (
                <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-2.5 text-xs text-red-300">
                  {authError}
                </div>
              )}
              <button
                type="submit"
                className="w-full rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-500 active:bg-blue-700"
              >
                Sign In
              </button>
            </form>
          </div>
        </div>
      </div>
    );
  }

  /* ---------------------------------------------------------------- */
  /* Authenticated layout                                              */
  /* ---------------------------------------------------------------- */

  return (
    <div className="h-screen flex flex-col bg-[#0a0a0f] text-slate-100">
      {/* Top bar — desktop + mobile */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.06] bg-white/[0.02] px-4 backdrop-blur">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setIsMobileSidebarOpen(!isMobileSidebarOpen)}
            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-white/[0.06] hover:text-slate-200 md:hidden"
          >
            {isMobileSidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <span className="text-sm font-semibold tracking-tight text-slate-300">Operations</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/"
            className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-white/[0.06] hover:text-white"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Chat</span>
          </Link>
          {session.auth_required && (
            <button
              type="button"
              onClick={handleLogout}
              className="flex items-center gap-1.5 rounded-lg p-1.5 text-slate-500 transition hover:bg-white/[0.06] hover:text-slate-300"
              title="Sign Out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar Overlay (Mobile) */}
        {isMobileSidebarOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm md:hidden"
            onClick={() => setIsMobileSidebarOpen(false)}
          />
        )}

        {/* Sidebar */}
        <aside
          className={[
            "fixed inset-y-12 left-0 z-50 flex w-64 flex-col border-r border-white/[0.06] bg-[#0a0a0f] transition-transform duration-200 md:relative md:inset-0 md:translate-x-0",
            isMobileSidebarOpen ? "translate-x-0" : "-translate-x-full",
          ].join(" ")}
        >
          <nav className="flex-1 overflow-y-auto px-3 py-4">
            {NAV_GROUPS.map((group) => {
              const visibleItems = group.items.filter(
                (item) => !item.requiresHeadquarters || showCorporationNav,
              );
              if (visibleItems.length === 0) return null;
              return (
                <div key={group.title} className="mb-5">
                  <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-600">
                    {group.title}
                  </p>
                  <div className="space-y-0.5">
                    {visibleItems.map((item) => {
                      const Icon = item.icon;
                      const active = !item.external && pathname === item.href;
                      if (item.external) {
                        return (
                          <a
                            key={item.href}
                            href={item.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-slate-400 transition hover:bg-white/[0.04] hover:text-slate-200"
                          >
                            <Icon className="h-4 w-4 shrink-0 opacity-60" />
                            {item.label}
                            <span className="ml-auto text-[10px] text-slate-600">&#x2197;</span>
                          </a>
                        );
                      }
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={[
                            "flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition",
                            active
                              ? "bg-blue-500/10 text-blue-300 font-medium"
                              : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200",
                          ].join(" ")}
                        >
                          <Icon
                            className={[
                              "h-4 w-4 shrink-0",
                              active ? "text-blue-400" : "opacity-50",
                            ].join(" ")}
                          />
                          {item.label}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </nav>

          <div className="border-t border-white/[0.06] px-4 py-3">
            <p className="text-[11px] text-slate-600">{session.owner_id}</p>
          </div>
        </aside>

        {/* Main Content */}
        <main className="relative flex flex-1 flex-col overflow-hidden">
          {/* SystemCommandBar hover trigger zone */}
          {showSystemCommandBar && (
            <>
              <div
                className="absolute top-0 left-0 right-0 z-30 h-1 cursor-pointer"
                onMouseEnter={() => {
                  if (commandBarTimeoutRef.current) clearTimeout(commandBarTimeoutRef.current);
                  setCommandBarVisible(true);
                }}
              />
              <div
                className={[
                  "absolute top-0 left-0 right-0 z-20 transition-all duration-200",
                  commandBarVisible
                    ? "translate-y-0 opacity-100"
                    : "-translate-y-full opacity-0 pointer-events-none",
                ].join(" ")}
                onMouseLeave={() => {
                  commandBarTimeoutRef.current = setTimeout(() => setCommandBarVisible(false), 400);
                }}
                onMouseEnter={() => {
                  if (commandBarTimeoutRef.current) clearTimeout(commandBarTimeoutRef.current);
                }}
              >
                <div className="border-b border-white/[0.06] bg-[#0a0a0f]/95 backdrop-blur-lg p-3">
                  <SystemCommandBar sourcePage={pathname || "/dashboard"} />
                </div>
              </div>
            </>
          )}

          <div className="flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
