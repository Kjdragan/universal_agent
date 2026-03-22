"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  LayoutDashboard,
  Building2,
  ShieldCheck,
  CheckCircle,
  ListTodo,
  MessageSquare,
  Send,
  CalendarDays,
  Bell,
  Radio,
  GraduationCap,
  Clock,
  HeartPulse,
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
      { href: "/dashboard/mission-control", label: "Mission Control", icon: Activity },
      { href: "/dashboard/corporation", label: "Corporation", icon: Building2, requiresHeadquarters: true },
      { href: "/dashboard/supervisors", label: "Supervisor Agents", icon: ShieldCheck, requiresHeadquarters: true },
      { href: "/dashboard/approvals", label: "Approvals", icon: CheckCircle },
      { href: "/dashboard/todolist", label: "To Do List", icon: ListTodo },
      { href: "/dashboard/sessions", label: "Sessions", icon: Clipboard },
      { href: "/dashboard/heartbeats", label: "Heartbeats", icon: HeartPulse },
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
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const sidebarTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
      <div className="flex h-screen items-center justify-center bg-background text-foreground">
        <div className="flex items-center gap-3 rounded-2xl border border-border/40 bg-card/30 px-6 py-5 text-sm text-muted-foreground backdrop-blur">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-primary" />
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
      <div className="flex h-screen items-center justify-center bg-background text-foreground p-4">
        <div className="w-full max-w-sm">
          <div className="mb-8 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20">
              <LayoutDashboard className="h-6 w-6 text-primary" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">Universal Agent</h1>
            <p className="mt-1 text-sm text-muted-foreground">Sign in to operations dashboard</p>
          </div>
          <div className="rounded-2xl border border-border/40 bg-card/20 p-6 backdrop-blur">
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Owner ID</label>
                <input
                  value={ownerId}
                  onChange={(event) => setOwnerId(event.target.value)}
                  className="w-full rounded-xl border border-border/40 bg-card/20 px-4 py-2.5 text-sm text-foreground outline-none transition placeholder:text-muted focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Password</label>
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  className="w-full rounded-xl border border-border/40 bg-card/20 px-4 py-2.5 text-sm text-foreground outline-none transition placeholder:text-muted focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                />
              </div>
              {authError && (
                <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-2.5 text-xs text-red-300">
                  {authError}
                </div>
              )}
              <button
                type="submit"
                className="w-full rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 active:bg-primary/80"
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

  const isStaging = process.env.NEXT_PUBLIC_UA_RUNTIME_STAGE === "staging";

  return (
    <div className="h-screen flex flex-col bg-background text-foreground">
      {/* Staging environment banner */}
      {isStaging && (
        <div className="flex shrink-0 items-center justify-center gap-2 bg-amber-400 px-4 py-1.5 text-xs font-semibold text-amber-950">
          <span>⚠️</span>
          <span>STAGING ENVIRONMENT — changes here are not production</span>
          <span>⚠️</span>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden relative">
        {/* Mobile hamburger — fixed button on left edge (visible only on mobile) */}
        <button
          onClick={() => setIsMobileSidebarOpen(!isMobileSidebarOpen)}
          className="fixed top-2 left-2 z-50 rounded-lg p-1.5 text-muted-foreground transition hover:bg-card/30 hover:text-foreground md:hidden bg-background/80 backdrop-blur border border-border/40"
        >
          {isMobileSidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        {/* Sidebar Overlay (Mobile) */}
        {isMobileSidebarOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm md:hidden"
            onClick={() => setIsMobileSidebarOpen(false)}
          />
        )}

        {/* Desktop hover trigger strip — invisible 16px zone on left edge */}
        <div
          className="hidden md:block fixed inset-y-0 left-0 w-4 z-50"
          onMouseEnter={() => {
            if (sidebarTimeoutRef.current) clearTimeout(sidebarTimeoutRef.current);
            setSidebarHovered(true);
          }}
        />

        {/* Sidebar — auto-hides on desktop, slides in on hover */}
        <aside
          className={[
            "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-border/40 bg-background shadow-2xl transition-transform duration-200",
            // Mobile: toggle via hamburger
            isMobileSidebarOpen ? "translate-x-0" : "-translate-x-full",
            // Desktop: hover-reveal overlay
            sidebarHovered ? "md:translate-x-0" : "md:-translate-x-full",
          ].join(" ")}
          onMouseEnter={() => {
            if (sidebarTimeoutRef.current) clearTimeout(sidebarTimeoutRef.current);
            setSidebarHovered(true);
          }}
          onMouseLeave={() => {
            sidebarTimeoutRef.current = setTimeout(() => setSidebarHovered(false), 400);
          }}
        >
          <nav className="flex-1 overflow-y-auto px-3 py-4">
            {NAV_GROUPS.map((group) => {
              const visibleItems = group.items.filter(
                (item) => !item.requiresHeadquarters || showCorporationNav,
              );
              if (visibleItems.length === 0) return null;
              return (
                <div key={group.title} className="mb-5">
                  <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
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
                            className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-muted-foreground transition hover:bg-card/20 hover:text-foreground"
                          >
                            <Icon className="h-4 w-4 shrink-0 opacity-60" />
                            {item.label}
                            <span className="ml-auto text-[10px] text-muted">&#x2197;</span>
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
                              ? "bg-primary/10 text-primary font-medium"
                              : "text-muted-foreground hover:bg-card/20 hover:text-foreground",
                          ].join(" ")}
                        >
                          <Icon
                            className={[
                              "h-4 w-4 shrink-0",
                              active ? "text-primary" : "opacity-50",
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

          <div className="border-t border-border/40 px-4 py-3">
            <p className="text-[11px] text-muted">{session.owner_id}</p>
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
                <div className="border-b border-border/40 bg-background/95 backdrop-blur-lg p-3">
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
