"use client";

import { useCallback, useEffect, useState } from "react";
import { Settings, Shield, Server, FileCode, RefreshCw } from "lucide-react";
import { OpsConfigSection, OpsProvider, SessionContinuityWidget } from "@/components/OpsDropdowns";
import { SessionGovernancePanel } from "@/components/dashboard/SessionGovernancePanel";

const API_BASE = "/api/dashboard/gateway";

type FactoryCapabilities = {
  factory_id?: string;
  factory_role?: string;
  deployment_profile?: string;
  gateway_mode?: string;
  delegation_mode?: string;
  heartbeat_scope?: string;
  enable_telegram_poll?: boolean;
  enable_vp_coder?: boolean;
  redis_delegation_enabled?: boolean;
};

export default function DashboardConfigPage() {
  const [capabilities, setCapabilities] = useState<FactoryCapabilities | null>(null);
  const [activeSection, setActiveSection] = useState<"runtime" | "governance" | "ops">("runtime");

  const [liveChromeEnabled, setLiveChromeEnabled] = useState(false);
  const [liveChromeUrl, setLiveChromeUrl] = useState("");
  const [isLiveChromeLoading, setIsLiveChromeLoading] = useState(false);

  const loadCapabilities = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/factory/capabilities`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setCapabilities(data.factory || null);
      }
    } catch {}
  }, []);

  const loadLiveChromeStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/factory/live-chrome/status`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setLiveChromeEnabled(data.enabled);
        setLiveChromeUrl(data.cdp_url);
      }
    } catch (e) {
      console.error("Failed to load Live Chrome status", e);
    }
  }, []);

  useEffect(() => {
    void loadCapabilities();
    void loadLiveChromeStatus();
  }, [loadCapabilities, loadLiveChromeStatus]);

  const handleUpdateLiveChrome = async () => {
    setIsLiveChromeLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/factory/live-chrome/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: liveChromeEnabled, cdp_url: liveChromeUrl }),
      });
      if (res.ok) {
        void loadLiveChromeStatus();
      }
    } catch (e) {
      console.error("Failed to update Live Chrome info", e);
    } finally {
      setIsLiveChromeLoading(false);
    }
  };

  const sections = [
    { key: "runtime" as const, label: "Runtime Policy", icon: Server },
    { key: "governance" as const, label: "Session Governance", icon: Shield },
    { key: "ops" as const, label: "Ops Config", icon: FileCode },
  ];

  return (
    <OpsProvider>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Configuration</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">Runtime policy, session governance, and system configuration.</p>
        </div>

        {/* Section tabs */}
        <div className="flex gap-1 rounded-xl border border-border/40 bg-card/10 p-1">
          {sections.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.key}
                onClick={() => setActiveSection(s.key)}
                className={[
                  "flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition",
                  activeSection === s.key
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-card/20 hover:text-foreground",
                ].join(" ")}
              >
                <Icon className="h-3.5 w-3.5" />
                {s.label}
              </button>
            );
          })}
        </div>

        {/* Runtime Policy section */}
        {activeSection === "runtime" && (
          <div className="space-y-4">
            <section className="rounded-xl border border-border/40 bg-card/10 p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-foreground">Factory Runtime Policy</h2>
                <button
                  onClick={loadCapabilities}
                  className="flex items-center gap-1.5 rounded-lg border border-border/40 bg-card/15 px-2.5 py-1 text-[10px] text-muted-foreground transition hover:bg-card/30"
                >
                  <RefreshCw className="h-3 w-3" />
                  Refresh
                </button>
              </div>
              {capabilities ? (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {[
                    { label: "Factory ID", value: capabilities.factory_id || "--" },
                    { label: "Factory Role", value: capabilities.factory_role || "--" },
                    { label: "Deployment Profile", value: capabilities.deployment_profile || "--" },
                    { label: "Gateway Mode", value: capabilities.gateway_mode || "--" },
                    { label: "Delegation Mode", value: capabilities.delegation_mode || "--" },
                    { label: "Heartbeat Scope", value: capabilities.heartbeat_scope || "--" },
                  ].map((item) => (
                    <div key={item.label} className="rounded-lg border border-border/25 bg-card/10 px-3 py-2.5">
                      <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{item.label}</div>
                      <div className="mt-1 text-sm font-medium text-foreground">{item.value}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Loading runtime policy...</p>
              )}
            </section>

            <section className="rounded-xl border border-border/40 bg-card/10 p-5">
              <h2 className="mb-4 text-sm font-semibold text-foreground">Feature Status</h2>
              {capabilities ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  {[
                    { label: "Telegram Polling", enabled: capabilities.enable_telegram_poll },
                    { label: "VP Coder (CODIE)", enabled: capabilities.enable_vp_coder },
                    { label: "Redis Delegation", enabled: capabilities.redis_delegation_enabled },
                  ].map((item) => (
                    <div key={item.label} className="flex items-center gap-3 rounded-lg border border-border/25 bg-card/10 px-3 py-2.5">
                      <div className={`h-2 w-2 rounded-full ${item.enabled ? "bg-primary shadow-[0_0_6px] shadow-emerald-500/50" : "bg-muted"}`} />
                      <span className="text-sm text-foreground/80">{item.label}</span>
                      <span className={`ml-auto text-xs ${item.enabled ? "text-primary" : "text-muted-foreground"}`}>
                        {item.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Loading...</p>
              )}
            </section>

            <section className="rounded-xl border border-border/40 bg-card/10 p-5">
              <h2 className="mb-4 text-sm font-semibold text-foreground">Live Session Attachment (Tailscale)</h2>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <label className="text-sm font-medium leading-none">
                      Enable Live Chrome Bridge
                    </label>
                    <p className="text-[13px] text-muted-foreground">
                      Allows agents to connect to a local Chrome browser via Tailscale tunnel.
                    </p>
                  </div>
                  <button 
                    onClick={() => setLiveChromeEnabled(!liveChromeEnabled)}
                    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${liveChromeEnabled ? 'bg-primary' : 'bg-input'}`}
                  >
                    <span className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform ${liveChromeEnabled ? 'translate-x-4' : 'translate-x-0'}`}/>
                  </button>
                </div>
                
                <div className="space-y-2 pt-2 border-t border-border/20">
                  <label className="text-sm font-medium leading-none">
                    Tailscale CDP URL
                  </label>
                  <div className="flex gap-2">
                    <input 
                      type="text" 
                      value={liveChromeUrl} 
                      onChange={(e) => setLiveChromeUrl(e.target.value)}
                      placeholder="e.g. http://my-desktop:9222"
                      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                    />
                    <button 
                      onClick={handleUpdateLiveChrome} 
                      disabled={isLiveChromeLoading}
                      className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                      {isLiveChromeLoading ? "Saving..." : "Save"}
                    </button>
                  </div>
                  <p className="text-[13px] text-muted-foreground">
                    Must be in the format <code className="text-[11px] bg-muted/50 px-1.5 py-0.5 rounded border border-border/50">http://&lt;tailscale-ip-or-name&gt;:9222</code>. Leave blank to default to localhost.
                  </p>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* Session Governance section */}
        {activeSection === "governance" && (
          <SessionGovernancePanel />
        )}

        {/* Ops Config section */}
        {activeSection === "ops" && (
          <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-border/40 bg-card/10">
            <OpsConfigSection variant="full" />
          </div>
        )}
      </div>
    </OpsProvider>
  );
}
