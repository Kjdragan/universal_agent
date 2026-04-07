"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  LayoutDashboard,
  Building2,
  ShieldCheck,
  CheckCircle,
  ListTodo,
  Kanban,
  Orbit,
  Clipboard,
  HeartPulse,
  MessageSquare,
  Mail,
  Send,
  CalendarDays,
  Bell,
  Radio,
  GraduationCap,
  Clock,
  Settings,
  Wrench,
  FolderOpen,
  Menu,
  X,
  type LucideIcon,
} from "lucide-react";

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
      { href: "/dashboard/chat", label: "Chat", icon: MessageSquare },
      { href: "/dashboard/mission-control", label: "Mission Control", icon: Activity },
      { href: "/dashboard/corporation", label: "Corporation", icon: Building2, requiresHeadquarters: true },
      { href: "/dashboard/supervisors", label: "Supervisor Agents", icon: ShieldCheck, requiresHeadquarters: true },
      { href: "/dashboard/approvals", label: "Approvals", icon: CheckCircle },
      { href: "/dashboard/todolist", label: "To Do List", icon: ListTodo },
      { href: "/dashboard/kanban", label: "Kanban Board", icon: Kanban },
      { href: "/dashboard/sessions", label: "Sessions", icon: Clipboard },
      { href: "/dashboard/heartbeats", label: "Heartbeats", icon: HeartPulse },
      { href: "/dashboard/agent-flow", label: "Agent Flow", icon: Orbit },
    ],
  },
  {
    title: "Agent",
    items: [
      { href: "/dashboard/mail", label: "Mail", icon: Mail },
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

export function GlobalSidebar() {
  const pathname = usePathname();
  const [session, setSession] = useState<DashboardAuthSession | null>(null);
  const [showCorporationNav, setShowCorporationNav] = useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const sidebarTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadAuthSession = useCallback(async () => {
    let authenticated = false;
    try {
      const response = await fetch("/api/dashboard/auth/session", { cache: "no-store" });
      const data = (await response.json()) as DashboardAuthSession;
      if (response.ok || response.status === 401) {
        setSession(data);
        authenticated = Boolean(data.authenticated);
      }
    } catch (error) {
      setSession(null);
      setShowCorporationNav(false);
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

  return (
    <>
      <button
        type="button"
        onClick={() => setIsMobileSidebarOpen(!isMobileSidebarOpen)}
        className="fixed top-2 left-2 z-50 rounded-lg p-1.5 text-muted-foreground transition hover:bg-card/30 hover:text-foreground md:hidden bg-background/80 backdrop-blur border border-border/40"
      >
        {isMobileSidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
      </button>
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

      <aside
        className={[
          "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-white/10 bg-[#0b1326]/90 backdrop-blur-2xl shadow-2xl transition-all duration-300 pointer-events-auto",
          isMobileSidebarOpen ? "translate-x-0 w-64" : "-translate-x-full w-64",
          "md:translate-x-0",
          sidebarHovered ? "md:w-64" : "md:w-20",
        ].join(" ")}
        onMouseEnter={() => {
          if (sidebarTimeoutRef.current) clearTimeout(sidebarTimeoutRef.current);
          setSidebarHovered(true);
        }}
        onMouseLeave={() => {
          sidebarTimeoutRef.current = setTimeout(() => setSidebarHovered(false), 200);
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
                <p
                  className={[
                    "mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground transition-all duration-300 whitespace-nowrap overflow-hidden w-full",
                    sidebarHovered || isMobileSidebarOpen ? "opacity-100" : "md:opacity-0 md:w-0",
                  ].join(" ")}
                >
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
                          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-muted-foreground transition overflow-hidden hover:bg-card/20 hover:text-foreground"
                        >
                          <Icon className="h-5 w-5 shrink-0 opacity-60" />
                          <span
                            className={[
                              "whitespace-nowrap transition-all duration-300 flex items-center w-full",
                              sidebarHovered || isMobileSidebarOpen ? "opacity-100" : "md:opacity-0 md:w-0 overflow-hidden",
                            ].join(" ")}
                          >
                            {item.label}
                            <span className="ml-auto text-[10px] text-muted">&#x2197;</span>
                          </span>
                        </a>
                      );
                    }
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={[
                          "flex items-center w-full gap-2.5 rounded-lg px-3 py-2 text-[13px] transition overflow-hidden",
                          active
                            ? "bg-cyan-500/10 text-cyan-400 font-medium"
                            : "text-slate-400 hover:bg-white/5 hover:text-slate-200",
                        ].join(" ")}
                      >
                        <Icon
                          className={[
                            "h-5 w-5 shrink-0 transition-colors duration-200",
                            active ? "text-cyan-400 drop-shadow-[0_0_8px_rgba(34,211,238,0.5)]" : "",
                          ].join(" ")}
                        />
                        <span
                          className={[
                            "whitespace-nowrap transition-all duration-300",
                            sidebarHovered || isMobileSidebarOpen ? "opacity-100" : "md:opacity-0 md:w-0 overflow-hidden",
                          ].join(" ")}
                        >
                          {item.label}
                        </span>
                      </Link>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        <div className="border-t border-white/10 px-4 py-3 min-h-[48px] overflow-hidden whitespace-nowrap">
          <p className={[
            "text-[11px] text-muted-foreground font-mono transition-opacity duration-300",
             sidebarHovered || isMobileSidebarOpen ? "opacity-100" : "md:opacity-0"
          ].join(" ")}>
            {session?.owner_id || "..."}
          </p>
        </div>
      </aside>
    </>
  );
}
