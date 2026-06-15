"use client";

import { useMemo, useRef, useState } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

export type GraphNode = { id: string; title: string; kind: string };
export type GraphEdge = { source: string; target: string };

type PositionedNode = GraphNode & SimulationNodeDatum & { x: number; y: number };

const KIND_COLOR: Record<string, string> = {
  source: "#3b82f6",
  entity: "#f59e0b",
  concept: "#10b981",
  analysis: "#a855f7",
};

export function kindColor(kind: string): string {
  return KIND_COLOR[kind] ?? "#94a3b8";
}

const WIDTH = 760;
const HEIGHT = 560;

/**
 * Force-directed knowledge graph rendered in SVG on top of `d3-force` (already a
 * web-ui dependency — no new package). The simulation is run to convergence once
 * per data change (static layout — robust, no animation re-render storms); the
 * user gets pan (drag background) + zoom (wheel) + node selection.
 */
export default function WikiForceGraph({
  nodes,
  edges,
  selectedPath,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedPath?: string | null;
  onSelect?: (path: string) => void;
}) {
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const [hover, setHover] = useState<string | null>(null);
  const panRef = useRef<{ startX: number; startY: number; ox: number; oy: number } | null>(null);

  // Static layout computed once per data change — deterministic given (nodes,
  // edges), so useMemo (not an effect+setState, which triggers cascading renders).
  const positions = useMemo<PositionedNode[]>(() => {
    if (!nodes.length) return [];
    const simNodes: PositionedNode[] = nodes.map((n, i) => ({
      ...n,
      // Deterministic seed spread (no Math.random) so layout is stable per render.
      x: WIDTH / 2 + Math.cos(i) * 30,
      y: HEIGHT / 2 + Math.sin(i) * 30,
    }));
    const idset = new Set(simNodes.map((n) => n.id));
    const simLinks = edges
      .filter((e) => idset.has(e.source) && idset.has(e.target))
      .map((e) => ({ ...e })) as unknown as SimulationLinkDatum<PositionedNode>[];
    const sim = forceSimulation(simNodes)
      .force(
        "link",
        forceLink(simLinks)
          .id((d) => (d as PositionedNode).id)
          .distance(72)
          .strength(0.55),
      )
      .force("charge", forceManyBody().strength(-240))
      .force("center", forceCenter(WIDTH / 2, HEIGHT / 2))
      .force("collide", forceCollide(26))
      .stop();
    const ticks = Math.min(420, 140 + nodes.length * 6);
    for (let i = 0; i < ticks; i += 1) sim.tick();
    return simNodes.map((n) => ({ ...n, x: n.x ?? WIDTH / 2, y: n.y ?? HEIGHT / 2 }));
  }, [nodes, edges]);

  const posById = useMemo(() => {
    const m = new Map<string, PositionedNode>();
    positions.forEach((p) => m.set(p.id, p));
    return m;
  }, [positions]);

  if (!nodes.length) {
    return (
      <div className="flex h-full min-h-[300px] items-center justify-center text-sm text-slate-400">
        This vault has no pages to graph yet.
      </div>
    );
  }

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="h-full w-full cursor-grab touch-none rounded-lg bg-slate-950/40"
      onWheel={(e) => {
        const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        setView((v) => ({ ...v, k: Math.min(3, Math.max(0.35, v.k * factor)) }));
      }}
      onMouseDown={(e) => {
        panRef.current = { startX: e.clientX, startY: e.clientY, ox: view.x, oy: view.y };
      }}
      onMouseMove={(e) => {
        const p = panRef.current;
        if (!p) return;
        setView((v) => ({ ...v, x: p.ox + (e.clientX - p.startX), y: p.oy + (e.clientY - p.startY) }));
      }}
      onMouseUp={() => {
        panRef.current = null;
      }}
      onMouseLeave={() => {
        panRef.current = null;
      }}
    >
      <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
        {edges.map((e, i) => {
          const a = posById.get(e.source);
          const b = posById.get(e.target);
          if (!a || !b) return null;
          const active = hover === e.source || hover === e.target || selectedPath === e.source || selectedPath === e.target;
          return (
            <line
              key={`e-${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={active ? "#64748b" : "#334155"}
              strokeWidth={active ? 1.6 : 1}
            />
          );
        })}
        {positions.map((n) => {
          const selected = selectedPath === n.id;
          const r = n.kind === "source" ? 11 : 8;
          return (
            <g
              key={n.id}
              transform={`translate(${n.x},${n.y})`}
              className="cursor-pointer"
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover((h) => (h === n.id ? null : h))}
              onClick={(e) => {
                e.stopPropagation();
                onSelect?.(n.id);
              }}
            >
              <circle
                r={selected ? r + 3 : r}
                fill={kindColor(n.kind)}
                stroke={selected ? "#e2e8f0" : "#0f172a"}
                strokeWidth={selected ? 2.5 : 1.5}
                opacity={hover && hover !== n.id && !selected ? 0.55 : 1}
              />
              {(hover === n.id || selected || positions.length <= 30) && (
                <text
                  x={r + 4}
                  y={4}
                  fontSize={11}
                  fill="#cbd5e1"
                  style={{ pointerEvents: "none" }}
                >
                  {n.title.length > 28 ? `${n.title.slice(0, 27)}…` : n.title}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
