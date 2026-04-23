"use client";

import { useDeferredValue, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Link from "next/link";
import {
  Bot,
  BookOpen,
  FileText,
  Layers3,
  PanelRightClose,
  PanelRightOpen,
  RefreshCcw,
  Search,
  Sparkles,
} from "lucide-react";
import { formatDateTimeTz } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";

type FileLink = {
  path?: string;
  rel_path?: string;
  api_url?: string;
  storage_href?: string;
};

type TopRow = {
  post_id?: string;
  tier?: number;
  action_type?: string;
  task_id?: string;
  post_url?: string;
  text_excerpt?: string;
  wiki_pages?: string[];
};

type Packet = {
  packet_name: string;
  date_slug: string;
  generated_at?: string;
  handle?: string;
  status?: string;
  new_post_count?: number;
  action_count?: number;
  queued_task_count?: number;
  linked_source_count?: number;
  linked_source_fetched_count?: number;
  tier_counts?: Record<string, number>;
  action_type_counts?: Record<string, number>;
  top_rows?: TopRow[];
  packet_storage_href?: string;
  report_markdown: FileLink;
  digest: FileLink;
  candidate_ledger: FileLink;
  linked_sources: FileLink;
  implementation_opportunities: FileLink;
  lane_ledger: FileLink;
};

type KnowledgePage = {
  path?: string;
  title?: string;
  summary?: string;
  tags?: string[];
  updated_at?: string;
  api_url?: string;
  storage_href?: string;
};

type Primitive = {
  kind?: string;
  title?: string;
  rationale?: string;
  content_markdown?: string;
};

type BundleVariant = {
  key?: string;
  label?: string;
  intent?: string;
  applicability?: string[];
  confidence?: string;
  primitives?: Primitive[];
};

type CanonicalSource = {
  title?: string;
  url?: string;
  source_type?: string;
  domain?: string;
  why_canonical?: string;
};

type Bundle = {
  bundle_id?: string;
  title?: string;
  summary?: string;
  why_now?: string;
  likely_ua_value?: string;
  likely_agent_system_value?: string;
  for_kevin_markdown?: string;
  for_ua_markdown?: string;
  recommended_variant?: string;
  canonical_sources?: CanonicalSource[];
  discovery_posts?: string[];
  variants?: BundleVariant[];
  artifact_markdown?: FileLink;
  artifact_json?: FileLink;
};

type Rolling = {
  title?: string;
  window_days?: number;
  generated_at?: string;
  bundle_count?: number;
  narrative_markdown?: string;
  report?: FileLink;
  bundles?: Bundle[];
};

type Payload = {
  status: string;
  state?: {
    handle?: string;
    last_seen_post_id?: string;
    last_success_at?: string;
    seen_post_count?: number;
  };
  latest_packet?: Packet | null;
  packets?: Packet[];
  rolling?: Rolling;
  vault?: {
    index?: FileLink;
    overview?: FileLink;
    knowledge_pages?: KnowledgePage[];
  };
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function MetricCard({
  label,
  value,
  accent,
  hint,
}: {
  label: string;
  value: string;
  accent: string;
  hint: string;
}) {
  return (
    <div className="rounded-3xl border border-border/40 bg-card/20 p-4 backdrop-blur-xl">
      <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className={`mt-2 text-3xl font-semibold tracking-tight ${accent}`}>{value}</div>
      <div className="mt-2 text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}

export default function DashboardClaudeCodeIntelPage() {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [selectedPacketName, setSelectedPacketName] = useState<string>("");
  const [selectedBundleId, setSelectedBundleId] = useState<string>("");
  const [selectedVariantKey, setSelectedVariantKey] = useState<string>("");
  const [reportMarkdown, setReportMarkdown] = useState<string>("");
  const [rollingMarkdown, setRollingMarkdown] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);
  const [rollingLoading, setRollingLoading] = useState(false);
  const [error, setError] = useState("");
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [showKnowledgeDrawer, setShowKnowledgeDrawer] = useState(false);
  const deferredKnowledgeQuery = useDeferredValue(knowledgeQuery);

  const packets = useMemo(() => payload?.packets ?? [], [payload?.packets]);
  const rolling = payload?.rolling || null;
  const bundles = useMemo(() => rolling?.bundles ?? [], [rolling?.bundles]);
  const knowledgePages = useMemo(() => payload?.vault?.knowledge_pages ?? [], [payload?.vault?.knowledge_pages]);

  const selectedPacket = useMemo(() => {
    if (!packets.length) return null;
    return packets.find((item) => item.packet_name === selectedPacketName) || packets[0];
  }, [packets, selectedPacketName]);

  const selectedBundle = useMemo(() => {
    if (!bundles.length) return null;
    return bundles.find((item) => item.bundle_id === selectedBundleId) || bundles[0];
  }, [bundles, selectedBundleId]);

  const selectedVariant = useMemo(() => {
    const variants = selectedBundle?.variants || [];
    if (!variants.length) return null;
    return (
      variants.find((item) => item.key === selectedVariantKey)
      || variants.find((item) => item.key === selectedBundle?.recommended_variant)
      || variants[0]
    );
  }, [selectedBundle, selectedVariantKey]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const response = await fetch(`${API_BASE}/api/v1/dashboard/claude-code-intel?limit=50`, { cache: "no-store" });
        const data = (await response.json()) as Payload;
        if (!response.ok) throw new Error(asText((data as { detail?: string }).detail) || `HTTP ${response.status}`);
        if (cancelled) return;
        setPayload(data);
        const latestName = asText(data.latest_packet?.packet_name) || asText(data.packets?.[0]?.packet_name);
        setSelectedPacketName((current) => current || latestName);
        const firstBundleId = asText(data.rolling?.bundles?.[0]?.bundle_id);
        setSelectedBundleId((current) => current || firstBundleId);
        const firstVariant = asText(data.rolling?.bundles?.[0]?.recommended_variant) || asText(data.rolling?.bundles?.[0]?.variants?.[0]?.key);
        setSelectedVariantKey((current) => current || firstVariant);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadMarkdown(targetUrl: string, assign: (value: string) => void, setBusy: (value: boolean) => void, label: string) {
      if (!targetUrl) {
        assign("");
        return;
      }
      setBusy(true);
      try {
        const response = await fetch(targetUrl, { cache: "no-store" });
        const text = await response.text();
        if (!response.ok) throw new Error(`Failed to load ${label} (${response.status})`);
        if (!cancelled) assign(text);
      } catch (err) {
        if (!cancelled) assign(`Failed to load ${label}: ${err instanceof Error ? err.message : String(err)}`);
      } finally {
        if (!cancelled) setBusy(false);
      }
    }
    void loadMarkdown(asText(selectedPacket?.report_markdown?.api_url), setReportMarkdown, setReportLoading, "report");
    void loadMarkdown(asText(rolling?.report?.api_url), setRollingMarkdown, setRollingLoading, "rolling brief");
    return () => {
      cancelled = true;
    };
  }, [selectedPacket?.report_markdown?.api_url, rolling?.report?.api_url]);

  const filteredKnowledgePages = useMemo(() => {
    const query = deferredKnowledgeQuery.trim().toLowerCase();
    if (!query) return knowledgePages;
    return knowledgePages.filter((page) => {
      const haystack = [
        asText(page.title),
        asText(page.summary),
        ...(page.tags || []).map(asText),
        asText(page.path),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [knowledgePages, deferredKnowledgeQuery]);

  const latestPacket = payload?.latest_packet || null;
  const latestGeneratedAt = asText(latestPacket?.generated_at);
  const lastSuccessAt = asText(payload?.state?.last_success_at);

  return (
    <div className="h-full overflow-auto bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-[1800px] flex-col gap-6 px-4 py-5 md:px-6">
        <section className="rounded-[32px] border border-cyan-400/15 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.18),transparent_40%),linear-gradient(135deg,rgba(15,23,42,0.92),rgba(8,15,30,0.98))] p-6 shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-4xl">
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-300/80">
                <Bot className="h-4 w-4" />
                Claude Code Intelligence
              </div>
              <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-100 md:text-4xl">
                Rolling builder intelligence for ClaudeDevs
              </h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300/80">
                This review console keeps the latest packet report, the rolling 14-day builder brief, and the reusable
                capability bundles in one place so both you and the agents can turn new Claude Code developments into
                actual system-building leverage.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {rolling?.report?.api_url ? (
                <a
                  href={asText(rolling.report.api_url)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-300/10 px-4 py-2 text-sm font-medium text-amber-50 transition hover:bg-amber-300/15"
                >
                  <Sparkles className="h-4 w-4" />
                  Rolling 14-Day Brief
                </a>
              ) : null}
              {asText(payload?.vault?.index?.api_url) ? (
                <a
                  href={asText(payload?.vault?.index?.api_url)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-full border border-cyan-400/25 bg-cyan-400/10 px-4 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-400/15"
                >
                  <BookOpen className="h-4 w-4" />
                  Vault Index
                </a>
              ) : null}
              <button
                type="button"
                onClick={() => setShowKnowledgeDrawer((current) => !current)}
                className="inline-flex items-center gap-2 rounded-full border border-white/12 bg-white/5 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-white/10"
              >
                {showKnowledgeDrawer ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
                Knowledge Search
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="inline-flex items-center gap-2 rounded-full border border-white/12 bg-white/5 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-white/10"
              >
                <RefreshCcw className="h-4 w-4" />
                Refresh
              </button>
            </div>
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Latest Packet"
              value={latestGeneratedAt ? formatDateTimeTz(latestGeneratedAt, { placeholder: "--" }) : "--"}
              accent="text-cyan-300"
              hint="Most recent ClaudeDevs packet"
            />
            <MetricCard
              label="Rolling Brief"
              value={asText(rolling?.generated_at) ? formatDateTimeTz(asText(rolling?.generated_at), { placeholder: "--" }) : "--"}
              accent="text-amber-200"
              hint="Current rolling 14-day synthesis"
            />
            <MetricCard
              label="Actions / Tasks"
              value={`${asNumber(latestPacket?.action_count)} / ${asNumber(latestPacket?.queued_task_count)}`}
              accent="text-amber-300"
              hint="Latest packet action count and queued task count"
            />
            <MetricCard
              label="Bundles / Vault"
              value={`${bundles.length} / ${knowledgePages.length}`}
              accent="text-emerald-300"
              hint={`Checkpoint: ${lastSuccessAt ? formatDateTimeTz(lastSuccessAt, { placeholder: "--" }) : "--"}`}
            />
          </div>
        </section>

        {error ? (
          <div className="rounded-2xl border border-red-400/20 bg-red-500/10 p-4 text-sm text-red-200">{error}</div>
        ) : null}

        <section className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
          <div className="rounded-[28px] border border-border/40 bg-card/20 p-4 backdrop-blur-xl">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Packet History</div>
                <h2 className="mt-1 text-lg font-semibold text-slate-100">Recent intelligence runs</h2>
              </div>
              <div className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-300">
                {packets.length}
              </div>
            </div>

            <div className="mt-4 space-y-3">
              {loading ? <div className="text-sm text-muted-foreground">Loading packets…</div> : null}
              {!loading && packets.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-border/40 p-4 text-sm text-muted-foreground">
                  No Claude Code intel packets found yet.
                </div>
              ) : null}
              {packets.map((packet) => {
                const active = packet.packet_name === selectedPacket?.packet_name;
                return (
                  <button
                    key={packet.packet_name}
                    type="button"
                    onClick={() => setSelectedPacketName(packet.packet_name)}
                    className={[
                      "w-full rounded-2xl border p-4 text-left transition",
                      active
                        ? "border-cyan-400/35 bg-cyan-400/10 shadow-[0_0_0_1px_rgba(34,211,238,0.12)]"
                        : "border-white/8 bg-white/5 hover:bg-white/[0.08]",
                    ].join(" ")}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-slate-100">
                          {formatDateTimeTz(packet.generated_at, { placeholder: packet.packet_name })}
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {packet.date_slug} · {packet.packet_name}
                        </div>
                      </div>
                      <div className="rounded-full border border-white/10 bg-black/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-cyan-200">
                        {packet.status}
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs text-slate-300">
                      <div>
                        <div className="text-muted-foreground">Posts</div>
                        <div className="mt-1 font-semibold">{asNumber(packet.new_post_count)}</div>
                      </div>
                      <div>
                        <div className="text-muted-foreground">Actions</div>
                        <div className="mt-1 font-semibold">{asNumber(packet.action_count)}</div>
                      </div>
                      <div>
                        <div className="text-muted-foreground">Fetched</div>
                        <div className="mt-1 font-semibold">
                          {asNumber(packet.linked_source_fetched_count)}/{asNumber(packet.linked_source_count)}
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)]">
              <div className="rounded-[28px] border border-border/40 bg-card/20 p-4 backdrop-blur-xl">
                <div className="flex flex-col gap-3 border-b border-white/8 pb-4 md:flex-row md:items-start md:justify-between">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Latest Report</div>
                    <h2 className="mt-1 text-lg font-semibold text-slate-100">
                      {selectedPacket ? `${selectedPacket.date_slug} / ${selectedPacket.packet_name}` : "No packet selected"}
                    </h2>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>Last seen post: {asText(payload?.state?.last_seen_post_id) || "--"}</span>
                      <span>·</span>
                      <span>Seen ids: {asNumber(payload?.state?.seen_post_count)}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {selectedPacket?.digest?.api_url ? (
                      <a href={selectedPacket.digest.api_url} target="_blank" rel="noreferrer" className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5">
                        Digest
                      </a>
                    ) : null}
                    {selectedPacket?.candidate_ledger?.api_url ? (
                      <a href={selectedPacket.candidate_ledger.api_url} target="_blank" rel="noreferrer" className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5">
                        Ledger
                      </a>
                    ) : null}
                    {selectedPacket?.linked_sources?.api_url ? (
                      <a href={selectedPacket.linked_sources.api_url} target="_blank" rel="noreferrer" className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5">
                        Linked Sources
                      </a>
                    ) : null}
                  </div>
                </div>

                <div className="mt-5 min-h-[560px] rounded-[24px] border border-white/8 bg-[#0c1426]/80 p-5">
                  {reportLoading ? (
                    <div className="text-sm text-muted-foreground">Loading markdown report…</div>
                  ) : (
                    <article className="markdown-preview max-w-none">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{reportMarkdown || "_No report available._"}</ReactMarkdown>
                    </article>
                  )}
                </div>
              </div>

              <div className="rounded-[28px] border border-border/40 bg-card/20 p-4 backdrop-blur-xl">
                <div className="flex items-start justify-between gap-3 border-b border-white/8 pb-4">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Rolling 14-Day Narrative</div>
                    <h2 className="mt-1 text-lg font-semibold text-slate-100">{asText(rolling?.title) || "Current builder brief"}</h2>
                    <div className="mt-2 text-xs text-muted-foreground">
                      Regenerated automatically from the latest 14-day ClaudeDevs capability window.
                    </div>
                  </div>
                  {rolling?.report?.api_url ? (
                    <a href={rolling.report.api_url} target="_blank" rel="noreferrer" className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5">
                      Open narrative
                    </a>
                  ) : null}
                </div>
                <div className="mt-5 min-h-[560px] rounded-[24px] border border-white/8 bg-[#0c1426]/80 p-5">
                  {rollingLoading ? (
                    <div className="text-sm text-muted-foreground">Loading rolling builder brief…</div>
                  ) : (
                    <article className="markdown-preview max-w-none">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{rollingMarkdown || "_No rolling narrative available yet._"}</ReactMarkdown>
                    </article>
                  )}
                </div>
              </div>
            </div>

            <div className="rounded-[28px] border border-border/40 bg-card/20 p-4 backdrop-blur-xl">
              <div className="flex items-center justify-between border-b border-white/8 pb-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Capability Bundles</div>
                  <h2 className="mt-1 text-lg font-semibold text-slate-100">Reusable building blocks from the rolling window</h2>
                </div>
                <div className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-300">
                  {bundles.length}
                </div>
              </div>

              <div className="mt-5 grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
                <div className="space-y-3">
                  {bundles.map((bundle) => {
                    const active = asText(bundle.bundle_id) === asText(selectedBundle?.bundle_id);
                    return (
                      <button
                        key={asText(bundle.bundle_id)}
                        type="button"
                        onClick={() => {
                          setSelectedBundleId(asText(bundle.bundle_id));
                          setSelectedVariantKey(asText(bundle.recommended_variant) || asText(bundle.variants?.[0]?.key));
                        }}
                        className={[
                          "w-full rounded-2xl border p-4 text-left transition",
                          active
                            ? "border-cyan-400/35 bg-cyan-400/10"
                            : "border-white/8 bg-white/[0.04] hover:bg-white/[0.06]",
                        ].join(" ")}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="text-sm font-medium text-slate-100">{bundle.title || bundle.bundle_id}</div>
                          <Layers3 className="mt-0.5 h-4 w-4 text-cyan-300" />
                        </div>
                        <div className="mt-2 text-xs leading-5 text-slate-300">{bundle.summary || "No summary available."}</div>
                        <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
                          {bundle.likely_ua_value ? (
                            <span className="rounded-full border border-white/10 px-2 py-0.5">{bundle.likely_ua_value}</span>
                          ) : null}
                          {bundle.likely_agent_system_value ? (
                            <span className="rounded-full border border-white/10 px-2 py-0.5">{bundle.likely_agent_system_value}</span>
                          ) : null}
                        </div>
                      </button>
                    );
                  })}
                  {!bundles.length ? (
                    <div className="rounded-2xl border border-dashed border-white/8 p-4 text-sm text-muted-foreground">
                      No capability bundles have been synthesized yet.
                    </div>
                  ) : null}
                </div>

                <div className="min-h-[620px] rounded-[24px] border border-white/8 bg-[#0c1426]/80 p-5">
                  {selectedBundle ? (
                    <div className="space-y-5">
                      <div className="flex flex-col gap-3 border-b border-white/8 pb-4 md:flex-row md:items-start md:justify-between">
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Bundle Detail</div>
                          <h3 className="mt-1 text-xl font-semibold text-slate-100">{selectedBundle.title || selectedBundle.bundle_id}</h3>
                          <p className="mt-2 max-w-3xl text-sm text-slate-300">{selectedBundle.why_now || selectedBundle.summary || ""}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {selectedBundle.artifact_markdown?.api_url ? (
                            <a href={selectedBundle.artifact_markdown.api_url} target="_blank" rel="noreferrer" className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5">
                              Open bundle
                            </a>
                          ) : null}
                          {selectedBundle.artifact_json?.api_url ? (
                            <a href={selectedBundle.artifact_json.api_url} target="_blank" rel="noreferrer" className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5">
                              JSON
                            </a>
                          ) : null}
                        </div>
                      </div>

                      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                        <article className="markdown-preview max-w-none rounded-2xl border border-white/8 bg-black/20 p-4">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedBundle.for_kevin_markdown || "_No Kevin explanation available._"}</ReactMarkdown>
                        </article>
                        <article className="markdown-preview max-w-none rounded-2xl border border-white/8 bg-black/20 p-4">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedBundle.for_ua_markdown || "_No UA package available._"}</ReactMarkdown>
                        </article>
                      </div>

                      <div className="rounded-2xl border border-white/8 bg-black/20 p-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Canonical Sources</div>
                        <div className="mt-3 space-y-2">
                          {(selectedBundle.canonical_sources || []).map((source) => (
                            <a
                              key={`${source.url}-${source.title}`}
                              href={asText(source.url)}
                              target="_blank"
                              rel="noreferrer"
                              className="block rounded-2xl border border-white/8 bg-white/[0.03] p-3 transition hover:bg-white/[0.05]"
                            >
                              <div className="text-sm font-medium text-slate-100">{source.title || source.url}</div>
                              <div className="mt-1 text-xs text-muted-foreground">
                                {(source.domain || "unknown domain")} · {(source.source_type || "unknown source type")}
                              </div>
                              <div className="mt-2 text-xs text-slate-300">{source.why_canonical || ""}</div>
                            </a>
                          ))}
                        </div>
                      </div>

                      <div className="rounded-2xl border border-white/8 bg-black/20 p-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Variants</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {(selectedBundle.variants || []).map((variant) => {
                            const active = asText(variant.key) === asText(selectedVariant?.key);
                            return (
                              <button
                                key={asText(variant.key)}
                                type="button"
                                onClick={() => setSelectedVariantKey(asText(variant.key))}
                                className={[
                                  "rounded-full border px-3 py-1.5 text-xs transition",
                                  active
                                    ? "border-cyan-400/35 bg-cyan-400/10 text-cyan-100"
                                    : "border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/[0.06]",
                                ].join(" ")}
                              >
                                {variant.label || variant.key}
                              </button>
                            );
                          })}
                        </div>

                        {selectedVariant ? (
                          <div className="mt-4 space-y-4">
                            <div className="rounded-2xl border border-white/8 bg-white/[0.03] p-4">
                              <div className="text-sm font-medium text-slate-100">{selectedVariant.label || selectedVariant.key}</div>
                              <div className="mt-1 text-xs text-muted-foreground">{selectedVariant.intent || ""}</div>
                              <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-slate-300">
                                {(selectedVariant.applicability || []).map((item) => (
                                  <span key={`${selectedVariant.key}-${item}`} className="rounded-full border border-white/10 px-2 py-0.5">
                                    {item}
                                  </span>
                                ))}
                                {selectedVariant.confidence ? (
                                  <span className="rounded-full border border-white/10 px-2 py-0.5">Confidence: {selectedVariant.confidence}</span>
                                ) : null}
                              </div>
                            </div>

                            {(selectedVariant.primitives || []).map((primitive, index) => (
                              <div key={`${selectedVariant.key}-${index}`} className="rounded-2xl border border-white/8 bg-white/[0.03] p-4">
                                <div className="text-sm font-medium text-slate-100">{primitive.title || primitive.kind || "Primitive"}</div>
                                <div className="mt-1 text-xs text-muted-foreground">{primitive.rationale || ""}</div>
                                <article className="markdown-preview mt-4 max-w-none">
                                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{primitive.content_markdown || "_No primitive content available._"}</ReactMarkdown>
                                </article>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ) : (
                    <div className="text-sm text-muted-foreground">Select a capability bundle to inspect its reusable primitives.</div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>

        {showKnowledgeDrawer ? (
          <>
            <div className="fixed inset-0 z-40 bg-black/45 backdrop-blur-sm" onClick={() => setShowKnowledgeDrawer(false)} />
            <aside className="fixed inset-y-0 right-0 z-50 w-full max-w-[420px] border-l border-white/10 bg-[#08111f]/96 p-4 shadow-[0_40px_120px_rgba(0,0,0,0.55)] backdrop-blur-2xl">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Knowledge Base</div>
                  <h2 className="mt-1 text-lg font-semibold text-slate-100">Search vault pages</h2>
                </div>
                <button
                  type="button"
                  onClick={() => setShowKnowledgeDrawer(false)}
                  className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5"
                >
                  Close
                </button>
              </div>

              <div className="mt-4 rounded-2xl border border-white/8 bg-[#0c1426]/80 px-3 py-2">
                <div className="flex items-center gap-2">
                  <Search className="h-4 w-4 text-muted-foreground" />
                  <input
                    value={knowledgeQuery}
                    onChange={(event) => setKnowledgeQuery(event.target.value)}
                    placeholder="Search titles, summaries, tags..."
                    className="w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-muted-foreground"
                  />
                </div>
              </div>

              <div className="mt-4 space-y-3 overflow-auto pb-6" style={{ maxHeight: "calc(100vh - 168px)" }}>
                {filteredKnowledgePages.slice(0, 28).map((page) => (
                  <a
                    key={asText(page.path)}
                    href={asText(page.api_url)}
                    target="_blank"
                    rel="noreferrer"
                    className="block rounded-2xl border border-white/8 bg-white/[0.04] p-3 transition hover:bg-white/[0.06]"
                  >
                    <div className="text-sm font-medium text-slate-100">{page.title || page.path}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {page.updated_at ? formatDateTimeTz(page.updated_at, { placeholder: page.updated_at }) : "Undated"}
                    </div>
                    <div className="mt-2 text-xs leading-5 text-slate-300">{page.summary || "No summary available."}</div>
                    {!!page.tags?.length && (
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {page.tags.slice(0, 4).map((tag) => (
                          <span key={`${page.path}-${tag}`} className="rounded-full border border-white/8 px-2 py-0.5 text-[10px] text-cyan-200/90">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </a>
                ))}
                {!filteredKnowledgePages.length ? (
                  <div className="rounded-2xl border border-dashed border-white/8 p-4 text-sm text-muted-foreground">
                    No vault pages match this query.
                  </div>
                ) : null}
              </div>
            </aside>
          </>
        ) : null}
      </div>
    </div>
  );
}
