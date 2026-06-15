"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

import WikiForceGraph, {
  kindColor,
  type GraphEdge,
  type GraphNode,
} from "@/components/dashboard/WikiForceGraph";

const API_BASE = "/api/dashboard/gateway";

type VaultSummary = {
  slug: string;
  title: string;
  kind: string;
  root: string;
  created_at: string;
  updated_at: string;
  page_count: number;
  source_count: number;
  entity_count: number;
  concept_count: number;
};

type Backlink = { path: string; title: string; kind: string };

type PageDetail = {
  path: string;
  title: string;
  kind: string;
  summary: string;
  tags: string[];
  content: string;
  backlinks: Backlink[];
};

type VaultDetail = {
  vault: VaultSummary;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  pages: { path: string; title: string; kind: string }[];
};

const LEGEND: { kind: string; label: string }[] = [
  { kind: "source", label: "Source" },
  { kind: "entity", label: "Entity" },
  { kind: "concept", label: "Concept" },
  { kind: "analysis", label: "Analysis" },
];

function fmtDate(iso: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-US", { timeZone: "America/Chicago" });
  } catch {
    return iso;
  }
}

export default function WikiVaultsPage() {
  const [vaults, setVaults] = useState<VaultSummary[]>([]);
  const [loadingVaults, setLoadingVaults] = useState(false);
  const [error, setError] = useState("");
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [detail, setDetail] = useState<VaultDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [page, setPage] = useState<PageDetail | null>(null);
  const [loadingPage, setLoadingPage] = useState(false);

  const loadVaults = useCallback(async () => {
    setLoadingVaults(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/wiki/vaults`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      const data = await res.json();
      setVaults(Array.isArray(data.vaults) ? data.vaults : []);
    } catch (err) {
      setError((err as Error).message || "Failed to load vaults.");
    } finally {
      setLoadingVaults(false);
    }
  }, []);

  const loadDetail = useCallback(async (slug: string) => {
    setLoadingDetail(true);
    setDetail(null);
    setSelectedPath(null);
    setPage(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/wiki/vaults/${encodeURIComponent(slug)}`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      setDetail(await res.json());
    } catch (err) {
      setError((err as Error).message || "Failed to load vault.");
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const loadPage = useCallback(
    async (slug: string, path: string) => {
      setLoadingPage(true);
      setSelectedPath(path);
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/wiki/vaults/${encodeURIComponent(slug)}/page?path=${encodeURIComponent(path)}`,
          { cache: "no-store" },
        );
        if (!res.ok) throw new Error(`Load failed (${res.status})`);
        const data = await res.json();
        setPage(data.page ?? null);
      } catch {
        setPage(null);
      } finally {
        setLoadingPage(false);
      }
    },
    [],
  );

  useEffect(() => {
    loadVaults();
  }, [loadVaults]);

  useEffect(() => {
    if (selectedSlug) loadDetail(selectedSlug);
  }, [selectedSlug, loadDetail]);

  const onSelectNode = useCallback(
    (path: string) => {
      if (selectedSlug) loadPage(selectedSlug, path);
    },
    [selectedSlug, loadPage],
  );

  const vault = detail?.vault;
  const isSeed = useMemo(
    () => !!vault && (vault.entity_count ?? 0) === 0 && (vault.concept_count ?? 0) === 0,
    [vault],
  );

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Wiki Vaults</h1>
          <p className="text-sm text-slate-400">
            Per-topic knowledge vaults — explore the materialized graph, read pages, follow backlinks.
          </p>
        </div>
        <button
          onClick={loadVaults}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800"
        >
          Refresh
        </button>
      </header>

      {error && <div className="rounded-md bg-red-950/60 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)_360px]">
        {/* Vault list */}
        <aside className="min-h-0 overflow-auto rounded-lg border border-slate-800 bg-slate-900/40">
          <div className="border-b border-slate-800 px-3 py-2 text-xs uppercase tracking-wide text-slate-500">
            {loadingVaults ? "Loading…" : `${vaults.length} vault${vaults.length === 1 ? "" : "s"}`}
          </div>
          <ul>
            {vaults.map((v) => {
              const active = v.slug === selectedSlug;
              return (
                <li key={`${v.root}/${v.slug}`}>
                  <button
                    onClick={() => setSelectedSlug(v.slug)}
                    className={`block w-full border-b border-slate-800/60 px-3 py-2 text-left hover:bg-slate-800/60 ${
                      active ? "bg-slate-800" : ""
                    }`}
                  >
                    <div className="truncate text-sm font-medium text-slate-100">{v.title}</div>
                    <div className="mt-0.5 flex flex-wrap gap-2 text-[11px] text-slate-400">
                      <span>{v.source_count} src</span>
                      <span>{v.entity_count} ent</span>
                      <span>{v.concept_count} con</span>
                      <span className="text-slate-600">· {v.root}</span>
                    </div>
                  </button>
                </li>
              );
            })}
            {!loadingVaults && vaults.length === 0 && (
              <li className="px-3 py-4 text-sm text-slate-500">No vaults found.</li>
            )}
          </ul>
        </aside>

        {/* Graph */}
        <section className="flex min-h-0 flex-col rounded-lg border border-slate-800 bg-slate-900/40">
          <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <div className="truncate text-sm text-slate-200">
              {vault ? vault.title : "Select a vault"}
              {vault && (
                <span className="ml-2 text-xs text-slate-500">
                  {detail?.graph.nodes.length ?? 0} nodes · {detail?.graph.edges.length ?? 0} links · created {fmtDate(vault.created_at)}
                </span>
              )}
            </div>
            <div className="flex gap-3 text-[11px] text-slate-400">
              {LEGEND.map((l) => (
                <span key={l.kind} className="flex items-center gap-1">
                  <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: kindColor(l.kind) }} />
                  {l.label}
                </span>
              ))}
            </div>
          </div>
          <div className="relative min-h-0 flex-1">
            {loadingDetail && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">Loading graph…</div>
            )}
            {!loadingDetail && detail && (
              <WikiForceGraph
                nodes={detail.graph.nodes}
                edges={detail.graph.edges}
                selectedPath={selectedPath}
                onSelect={onSelectNode}
              />
            )}
            {!loadingDetail && !detail && (
              <div className="flex h-full items-center justify-center text-sm text-slate-500">
                Pick a vault on the left to render its knowledge graph.
              </div>
            )}
            {!loadingDetail && detail && isSeed && (
              <div className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-amber-950/70 px-3 py-1.5 text-[11px] text-amber-300">
                Seed vault — no entity/concept pages yet. Re-ingest its source to materialize the graph.
              </div>
            )}
          </div>
        </section>

        {/* Reader */}
        <aside className="min-h-0 overflow-auto rounded-lg border border-slate-800 bg-slate-900/40">
          <div className="border-b border-slate-800 px-3 py-2 text-xs uppercase tracking-wide text-slate-500">
            {loadingPage ? "Loading page…" : page ? page.kind || "page" : "Reader"}
          </div>
          {!page && !loadingPage && (
            <div className="px-3 py-4 text-sm text-slate-500">
              Click a node in the graph to read its page.
            </div>
          )}
          {page && (
            <div className="px-3 py-3">
              <h2 className="text-base font-semibold text-slate-100">{page.title}</h2>
              {page.summary && <p className="mt-1 text-xs text-slate-400">{page.summary}</p>}
              {page.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {page.tags.slice(0, 16).map((t) => (
                    <span key={t} className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {page.backlinks.length > 0 && (
                <div className="mt-3">
                  <div className="text-[11px] uppercase tracking-wide text-slate-500">Backlinks</div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {page.backlinks.map((b) => (
                      <button
                        key={b.path}
                        onClick={() => selectedSlug && loadPage(selectedSlug, b.path)}
                        className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
                      >
                        ← {b.title}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="markdown-body mt-3 space-y-2 border-t border-slate-800 pt-3 text-sm leading-relaxed text-slate-300">
                <ReactMarkdown>{page.content}</ReactMarkdown>
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
