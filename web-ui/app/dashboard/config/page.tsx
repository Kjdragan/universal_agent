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

  const loadCapabilities = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/factory/capabilities`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setCapabilities(data.factory || null);
      }
    } catch {}
  }, []);

  useEffect(() => {
    void loadCapabilities();
  }, [loadCapabilities]);

  const sections = [
    { key: "runtime" as const, label: "Runtime Policy", icon: Server },
    { key: "governance" as const, label: "Session Governance", icon: Shield },
    { key: "ops" as const, label: "Ops Config", icon: FileCode },
  ];

  return (
    <OpsProvider>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">Configuration</h1>
          <p className="mt-0.5 text-sm text-slate-500">Runtime policy, session governance, and system configuration.</p>
        </div>

        {/* Section tabs */}
        <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
          {sections.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.key}
                onClick={() => setActiveSection(s.key)}
                className={[
                  "flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition",
                  activeSection === s.key
                    ? "bg-blue-500/10 text-blue-300"
                    : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200",
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
            <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-slate-200">Factory Runtime Policy</h2>
                <button
                  onClick={loadCapabilities}
                  className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-white/[0.03] px-2.5 py-1 text-[10px] text-slate-400 transition hover:bg-white/[0.06]"
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
                    <div key={item.label} className="rounded-lg border border-white/[0.04] bg-white/[0.02] px-3 py-2.5">
                      <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">{item.label}</div>
                      <div className="mt-1 text-sm font-medium text-slate-200">{item.value}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-slate-500">Loading runtime policy...</p>
              )}
            </section>

            <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
              <h2 className="mb-4 text-sm font-semibold text-slate-200">Feature Status</h2>
              {capabilities ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  {[
                    { label: "Telegram Polling", enabled: capabilities.enable_telegram_poll },
                    { label: "VP Coder (CODIE)", enabled: capabilities.enable_vp_coder },
                    { label: "Redis Delegation", enabled: capabilities.redis_delegation_enabled },
                  ].map((item) => (
                    <div key={item.label} className="flex items-center gap-3 rounded-lg border border-white/[0.04] bg-white/[0.02] px-3 py-2.5">
                      <div className={`h-2 w-2 rounded-full ${item.enabled ? "bg-emerald-500 shadow-[0_0_6px] shadow-emerald-500/50" : "bg-slate-600"}`} />
                      <span className="text-sm text-slate-300">{item.label}</span>
                      <span className={`ml-auto text-xs ${item.enabled ? "text-emerald-400" : "text-slate-500"}`}>
                        {item.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-slate-500">Loading...</p>
              )}
            </section>
          </div>
        )}

        {/* Session Governance section */}
        {activeSection === "governance" && (
          <SessionGovernancePanel />
        )}

        {/* Ops Config section */}
        {activeSection === "ops" && (
          <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.02]">
            <OpsConfigSection variant="full" />
          </div>
        )}
      </div>
    </OpsProvider>
  );
}
