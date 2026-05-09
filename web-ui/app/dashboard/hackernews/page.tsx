"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ExternalLink,
  MessageCircle,
  RefreshCw,
  Search,
  AlertTriangle,
} from "lucide-react";

const API = "/api/dashboard/gateway/api/v1/hackernews";

const T = {
  bg0: "#0a0c10",
  bg2: "#12161f",
  bg3: "#161b26",
  bg4: "#1c2230",
  line: "#1f2735",
  line2: "#283040",
  t0: "#e6e9ef",
  t1: "#aab2c0",
  t2: "#6c7689",
  t3: "#4a5266",
  hn: "#ff6600",
  hn2: "#ff8a3d",
  hnDim: "#3a1f0c",
  up: "#4ade80",
  down: "#f87171",
  newColor: "#60a5fa",
  warn: "#fbbf24",
  ok: "#22c55e",
  fontMono:
    "ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace",
  fontSans:
    "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif",
};

type Story = {
  id?: number;
  rank?: number;
  title?: string;
  url?: string;
  by?: string;
  score?: number;
  descendants?: number;
  time?: number;
  age?: string;
  host?: string;
};

type MoverChange = {
  id?: number;
  title?: string;
  delta?: number;
  status?: string;
  rank?: number;
  score?: number;
};

type Movers = {
  since?: string;
  changes?: MoverChange[];
};

type Pulse = {
  topic?: string;
  count?: number;
  trend?: number[];
  avg_points?: number;
  pct_change?: number;
};

type Snapshot = {
  meta: {
    generated_at: string;
    errors: string[];
    watchlist: string[];
    duration_seconds: number;
    schema_version: number;
  };
  top_stories: Story[] | null;
  movers: Movers | null;
  controversial: Story[] | null;
  pulses: Record<string, Pulse | null>;
  show_hn: Story[] | null;
  ask_hn: Story[] | null;
  hiring: { companies?: Array<{ name: string; months?: number }> } | null;
};

function formatAge(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "–";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

function pillState(generatedAt: string | undefined): {
  color: string;
  label: string;
  status: "cold" | "ok" | "stale" | "error";
} {
  if (!generatedAt) {
    return { color: T.down, label: "cold start · awaiting first sync", status: "cold" };
  }
  const ts = new Date(generatedAt).getTime();
  if (!isFinite(ts)) {
    return { color: T.down, label: "cold start · awaiting first sync", status: "cold" };
  }
  const ageSec = (Date.now() - ts) / 1000;
  if (ageSec <= 2700) {
    return { color: T.ok, label: `idle · synced ${formatAge(ageSec)} ago`, status: "ok" };
  }
  if (ageSec <= 7200) {
    return { color: T.warn, label: `stale · synced ${formatAge(ageSec)} ago`, status: "stale" };
  }
  return { color: T.down, label: `error · last good ${formatAge(ageSec)} ago`, status: "error" };
}

function deriveHost(story: Story): string {
  if (story.host) return story.host;
  if (!story.url) return "news.ycombinator.com";
  try {
    return new URL(story.url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function commentUrl(id?: number): string {
  return id ? `https://news.ycombinator.com/item?id=${id}` : "#";
}

function scoreOf(s: Story): number {
  return Number(s.score ?? 0);
}

function commentsOf(s: Story): number {
  return Number(s.descendants ?? 0);
}

function hnAge(s: Story): string {
  if (s.age) return s.age;
  if (!s.time) return "";
  const sec = Math.max(0, Date.now() / 1000 - Number(s.time));
  return formatAge(sec) + " ago";
}

function trendPath(values: number[] | undefined): {
  fill: string;
  stroke: string;
} {
  if (!values || values.length === 0) {
    return { fill: "M0,30 L100,30 L100,30 L0,30 Z", stroke: "M0,30 L100,30" };
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const dx = values.length === 1 ? 0 : 100 / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * dx;
    const y = 28 - ((v - min) / span) * 24;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const stroke = `M${points.join(" L")}`;
  const fill = `${stroke} L100,30 L0,30 Z`;
  return { fill, stroke };
}

/* ───────────────────────── PANELS ───────────────────────── */

function StatusPill({ generatedAt }: { generatedAt: string | undefined }) {
  const pill = pillState(generatedAt);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 8px",
        borderRadius: 999,
        border: `1px solid ${T.line2}`,
        background: T.bg2,
        color: T.t1,
        fontFamily: T.fontMono,
        fontSize: 11.5,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: pill.color,
          boxShadow: `0 0 0 3px ${pill.color}22`,
        }}
      />
      {pill.label}
    </span>
  );
}

function PanelHeading({
  title,
  accent,
  sub,
  right,
}: {
  title: React.ReactNode;
  accent?: string;
  sub?: string;
  right?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "7px 14px",
        borderBottom: `1px solid ${T.line}`,
        background: `linear-gradient(180deg, ${T.bg3}, ${T.bg2})`,
      }}
    >
      <h3
        style={{
          margin: 0,
          fontSize: 12,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: ".1em",
          color: T.t1,
        }}
      >
        {title}
        {accent ? <span style={{ color: T.hn }}> {accent}</span> : null}
      </h3>
      {sub ? (
        <span style={{ fontFamily: T.fontMono, fontSize: 11, color: T.t2 }}>
          {sub}
        </span>
      ) : null}
      <div style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center" }}>
        {right}
      </div>
    </div>
  );
}

function ErrorChip({ kind }: { kind: string }) {
  return (
    <div
      style={{
        margin: 14,
        padding: "8px 10px",
        borderRadius: 6,
        border: `1px solid rgba(248,113,113,.3)`,
        background: "rgba(248,113,113,.06)",
        color: T.down,
        fontFamily: T.fontMono,
        fontSize: 11,
        display: "inline-flex",
        gap: 8,
        alignItems: "center",
      }}
    >
      <AlertTriangle size={12} />
      couldn’t load · {kind}
    </div>
  );
}

function TopStoriesPanel({
  stories,
  errored,
  generatedAt,
}: {
  stories: Story[] | null;
  errored: boolean;
  generatedAt?: string;
}) {
  const subAge = generatedAt
    ? `refreshed ${formatAge((Date.now() - new Date(generatedAt).getTime()) / 1000)} ago · cron */30m`
    : "cron */30m";
  const top = (stories ?? []).slice(0, 10);
  return (
    <section style={panelStyle}>
      <PanelHeading
        title={<>Top Stories</>}
        accent="/ front page"
        sub={subAge}
        right={
          <span style={countStyle}>
            {top.length} of {(stories ?? []).length}
          </span>
        }
      />
      {errored ? (
        <ErrorChip kind="top stories" />
      ) : top.length === 0 ? (
        <EmptyRow label="no stories yet" />
      ) : (
        <div style={{ padding: "4px 0" }}>
          {top.map((s, i) => (
            <StoryRow key={s.id ?? i} story={s} rank={i + 1} dim={i >= 8} />
          ))}
        </div>
      )}
    </section>
  );
}

function StoryRow({
  story,
  rank,
  dim,
}: {
  story: Story;
  rank: number;
  dim?: boolean;
}) {
  const host = deriveHost(story);
  const articleUrl = story.url || commentUrl(story.id);
  // Avoid nested <a> tags — invalid HTML, Chromium silently closes the outer one
  // at the first inner one, killing clicks on most of the row. Wrapper is a
  // <div>; the title block and the action icons each have their own <a>.
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "30px 1fr auto",
        gap: 10,
        padding: "5px 14px",
        alignItems: "center",
        borderTop: `1px solid ${rank === 1 ? "transparent" : T.line}`,
        color: T.t0,
      }}
    >
      <div
        style={{
          fontFamily: T.fontMono,
          fontSize: 12.5,
          fontWeight: 600,
          color: dim ? T.t3 : T.hn,
          textAlign: "right",
        }}
      >
        {String(rank).padStart(2, "0")}
      </div>
      <a
        href={articleUrl}
        target="_blank"
        rel="noopener noreferrer"
        title={story.title}
        style={{ minWidth: 0, color: T.t0, textDecoration: "none", display: "block" }}
      >
        <div
          style={{
            color: T.t0,
            fontSize: 13.5,
            fontWeight: 500,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {story.title ?? "(untitled)"}
        </div>
        <div
          style={{
            marginTop: 1,
            fontFamily: T.fontMono,
            fontSize: 11,
            color: T.t2,
            display: "inline-flex",
            gap: 10,
            alignItems: "center",
          }}
        >
          {host ? (
            <span style={{ color: T.t1 }}>
              <span style={{ color: T.t3 }}>(</span>
              {host}
              <span style={{ color: T.t3 }}>)</span>
            </span>
          ) : null}
          {story.by ? (
            <>
              <span style={{ color: T.t3 }}>·</span>
              <span>by {story.by}</span>
            </>
          ) : null}
          <span style={{ color: T.t3 }}>·</span>
          <span style={{ color: T.t3 }}>{hnAge(story)}</span>
        </div>
      </a>
      <div
        style={{
          display: "inline-flex",
          gap: 12,
          alignItems: "center",
          fontFamily: T.fontMono,
          fontSize: 11.5,
          color: T.t1,
        }}
      >
        <span style={{ color: T.hn, fontWeight: 600 }}>
          <span style={{ color: T.hn2, fontSize: 9.5, marginRight: 3 }}>▲</span>
          {scoreOf(story)}
        </span>
        <span>{commentsOf(story)} cmt</span>
        <span style={{ display: "inline-flex", gap: 4, marginLeft: 8 }}>
          <ActionIcon href={articleUrl} label="Open article">
            <ExternalLink size={11} />
          </ActionIcon>
          <ActionIcon href={commentUrl(story.id)} label="HN comments" accent>
            <MessageCircle size={11} />
          </ActionIcon>
        </span>
      </div>
    </div>
  );
}

function ActionIcon({
  href,
  label,
  accent,
  children,
}: {
  href: string;
  label: string;
  accent?: boolean;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={label}
      onClick={(e) => e.stopPropagation()}
      style={{
        width: 24,
        height: 24,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 5,
        color: accent ? T.hn : T.t2,
        border: "1px solid transparent",
      }}
    >
      {children}
    </a>
  );
}

function HeatedPanel({
  rows,
  errored,
}: {
  rows: Story[] | null;
  errored: boolean;
}) {
  const list = (rows ?? []).slice(0, 12);
  return (
    <section style={{ ...panelStyle, marginTop: 10 }}>
      <PanelHeading
        title={
          <>
            <span style={{ color: T.warn }}>⚠ </span>Controversial &amp; Heated
          </>
        }
        sub="7-day · cmt:pts ratio"
        right={<span style={countStyle}>{list.length} shown</span>}
      />
      {errored ? (
        <ErrorChip kind="controversial" />
      ) : list.length === 0 ? (
        <EmptyRow label="no controversial stories" />
      ) : (
        <div>
          {list.map((s, i) => {
            const pts = scoreOf(s);
            const cmts = commentsOf(s);
            const ratio = pts ? cmts / pts : 0;
            return (
              <a
                key={s.id ?? i}
                href={s.url || commentUrl(s.id)}
                target="_blank"
                rel="noopener noreferrer"
                title={s.title}
                style={{
                  display: "grid",
                  gridTemplateColumns: "48px 1fr auto",
                  gap: 10,
                  alignItems: "center",
                  padding: "5px 14px",
                  borderTop: i === 0 ? "1px solid transparent" : `1px solid ${T.line}`,
                  color: T.t0,
                  textDecoration: "none",
                }}
              >
                <div
                  style={{
                    fontFamily: T.fontMono,
                    fontSize: 11.5,
                    fontWeight: 600,
                    color: T.warn,
                    background: "rgba(251,191,36,.08)",
                    border: "1px solid rgba(251,191,36,.18)",
                    padding: "2px 6px",
                    borderRadius: 5,
                    textAlign: "center",
                  }}
                >
                  {ratio ? `${ratio.toFixed(2)}×` : "–"}
                </div>
                <div
                  style={{
                    fontSize: 12.5,
                    color: T.t0,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {s.title}
                  <small
                    style={{
                      color: T.t2,
                      fontFamily: T.fontMono,
                      fontSize: 10.5,
                      marginLeft: 6,
                    }}
                  >
                    {deriveHost(s)} · {hnAge(s)}
                  </small>
                </div>
                <div
                  style={{
                    fontFamily: T.fontMono,
                    fontSize: 11,
                    color: T.t2,
                    display: "inline-flex",
                    gap: 10,
                  }}
                >
                  <span style={{ color: T.warn }}>{cmts} cmt</span>
                  <span style={{ color: T.t1 }}>{pts} pts</span>
                </div>
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}

function PulsePanel({
  pulses,
  watchlist,
}: {
  pulses: Record<string, Pulse | null> | undefined;
  watchlist: string[];
}) {
  return (
    <section style={panelStyle}>
      <PanelHeading
        title={<>Topic Pulse</>}
        sub="7-day mention volume"
        right={<span style={tabOnStyle}>7d</span>}
      />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 6,
          padding: "8px 10px 10px",
        }}
      >
        {watchlist.slice(0, 6).map((topic) => {
          const p = pulses?.[topic] ?? null;
          if (!p) {
            return (
              <div key={topic} style={tileStyle}>
                <div style={tileRow1}>
                  <span style={tileName}>{topic}</span>
                  <span style={{ ...tileCount, color: T.down }}>–</span>
                </div>
                <div style={{ height: 22 }} />
                <div style={tileRow3}>
                  <span style={{ color: T.down }}>load failed</span>
                </div>
              </div>
            );
          }
          const trend = trendPath(p.trend);
          const pct = Number(p.pct_change ?? 0);
          const pctColor = pct >= 0 ? T.up : T.down;
          const lineColor = pct >= 0 ? T.hn : "#7a8295";
          return (
            <div key={topic} style={tileStyle}>
              <div style={tileRow1}>
                <span style={tileName}>{topic}</span>
                <span style={tileCount}>
                  {Number(p.count ?? 0)}
                  <span
                    style={{ fontSize: 10, marginLeft: 3, color: pctColor }}
                  >
                    {pct >= 0 ? "▲" : "▼"}
                  </span>
                </span>
              </div>
              <svg
                viewBox="0 0 100 30"
                preserveAspectRatio="none"
                style={{ display: "block", width: "100%", height: 22 }}
              >
                <defs>
                  <linearGradient id={`g-${topic}`} x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0" stopColor={lineColor} stopOpacity=".35" />
                    <stop offset="1" stopColor={lineColor} stopOpacity="0" />
                  </linearGradient>
                </defs>
                <path d={trend.fill} fill={`url(#g-${topic})`} />
                <path
                  d={trend.stroke}
                  fill="none"
                  stroke={lineColor}
                  strokeWidth="1.5"
                />
              </svg>
              <div style={tileRow3}>
                <span>
                  avg <b style={{ color: T.t1, fontWeight: 600 }}>{Number(p.avg_points ?? 0)} pts</b>
                </span>
                <span style={{ color: pctColor }}>
                  {pct >= 0 ? "▲" : "▼"} {Math.abs(pct).toFixed(0)}%
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MoversPanel({
  movers,
  errored,
}: {
  movers: Movers | null;
  errored: boolean;
}) {
  const changes = (movers?.changes ?? []).slice(0, 12);
  return (
    <section style={panelStyle}>
      <PanelHeading
        title={<>Front-Page Movers</>}
        sub={`since last sync`}
        right={<span style={countStyle}>{changes.length} changes</span>}
      />
      {errored ? (
        <ErrorChip kind="movers" />
      ) : changes.length === 0 ? (
        <EmptyRow label="awaiting first comparison" />
      ) : (
        <div style={{ padding: "2px 0 6px" }}>
          {changes.map((c, i) => (
            <MoverRow key={c.id ?? i} change={c} first={i === 0} />
          ))}
        </div>
      )}
    </section>
  );
}

function MoverRow({ change, first }: { change: MoverChange; first?: boolean }) {
  // Movers don't carry a story URL, so clicking opens the HN comment thread.
  const href = commentUrl(change.id);
  const status = (change.status ?? "").toLowerCase();
  const delta = Number(change.delta ?? 0);
  let badge: React.ReactNode = null;
  if (status === "new") {
    badge = (
      <span
        style={{
          color: T.newColor,
          border: "1px solid rgba(96,165,250,.3)",
          background: "rgba(96,165,250,.06)",
          padding: "1px 6px",
          borderRadius: 4,
          fontSize: 10,
          letterSpacing: ".08em",
          textTransform: "uppercase",
          fontWeight: 700,
          fontFamily: T.fontMono,
        }}
      >
        NEW
      </span>
    );
  } else if (status === "dropped") {
    badge = (
      <span
        style={{
          color: T.down,
          border: "1px solid rgba(248,113,113,.3)",
          background: "rgba(248,113,113,.06)",
          padding: "1px 6px",
          borderRadius: 4,
          fontSize: 10,
          letterSpacing: ".08em",
          textTransform: "uppercase",
          fontWeight: 700,
          fontFamily: T.fontMono,
        }}
      >
        DROPPED
      </span>
    );
  } else {
    const up = delta > 0;
    badge = (
      <span
        style={{
          color: up ? T.up : T.down,
          fontFamily: T.fontMono,
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        {up ? "▲" : "▼"} {Math.abs(delta)}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={change.title}
      style={{
        display: "grid",
        gridTemplateColumns: "64px 1fr auto",
        gap: 10,
        alignItems: "center",
        padding: "5px 14px",
        borderTop: first ? "1px solid transparent" : `1px solid ${T.line}`,
        color: T.t0,
        textDecoration: "none",
      }}
    >
      <div>{badge}</div>
      <div
        style={{
          fontSize: 12.5,
          color: T.t0,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {change.title ?? "(untitled)"}
      </div>
      <div style={{ fontFamily: T.fontMono, fontSize: 11, color: T.t2 }}>
        {status === "dropped" ? (
          <span>off front · {Number(change.score ?? 0)} pts</span>
        ) : (
          <span>
            <b style={{ color: T.t1, fontWeight: 500 }}>
              #{Number(change.rank ?? 0)}
            </b>{" "}
            · {Number(change.score ?? 0)} pts
          </span>
        )}
      </div>
    </a>
  );
}

function DuoStoriesPanel({
  showHn,
  askHn,
  showError,
  askError,
}: {
  showHn: Story[] | null;
  askHn: Story[] | null;
  showError: boolean;
  askError: boolean;
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
      <SmallStoriesPanel
        kind="show"
        title="Show HN"
        rows={showHn}
        errored={showError}
      />
      <SmallStoriesPanel
        kind="ask"
        title="Ask HN"
        rows={askHn}
        errored={askError}
      />
    </div>
  );
}

function SmallStoriesPanel({
  kind,
  title,
  rows,
  errored,
}: {
  kind: "show" | "ask";
  title: string;
  rows: Story[] | null;
  errored: boolean;
}) {
  const tagBg = kind === "show" ? "rgba(255,102,0,.05)" : "rgba(167,139,250,.08)";
  const tagBorder = kind === "show" ? T.hnDim : "#4c3a85";
  const tagColor = kind === "show" ? T.hn : "#c4b5fd";
  const list = (rows ?? []).slice(0, 5);
  return (
    <section style={panelStyle}>
      <PanelHeading
        title={
          <>
            <span
              style={{
                fontFamily: T.fontMono,
                fontSize: 10,
                padding: "1px 5px",
                borderRadius: 4,
                border: `1px solid ${tagBorder}`,
                color: tagColor,
                background: tagBg,
                textTransform: "uppercase",
                letterSpacing: ".06em",
                fontWeight: 700,
                marginRight: 7,
              }}
            >
              {kind === "show" ? "Show" : "Ask"}
            </span>
            {title}
          </>
        }
        right={<span style={countStyle}>freshest · {list.length}</span>}
      />
      {errored ? (
        <ErrorChip kind={title} />
      ) : list.length === 0 ? (
        <EmptyRow label="empty" />
      ) : (
        <div>
          {list.map((s, i) => (
            <a
              key={s.id ?? i}
              href={s.url || commentUrl(s.id)}
              target="_blank"
              rel="noopener noreferrer"
              title={s.title}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto",
                alignItems: "center",
                gap: 8,
                padding: "5px 14px",
                borderTop: i === 0 ? "1px solid transparent" : `1px solid ${T.line}`,
                color: T.t0,
                textDecoration: "none",
              }}
            >
              <span
                style={{
                  fontSize: 12.5,
                  color: T.t0,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {s.title ?? "(untitled)"}
              </span>
              <span
                style={{
                  fontFamily: T.fontMono,
                  fontSize: 10.5,
                  color: T.t2,
                  whiteSpace: "nowrap",
                }}
              >
                {scoreOf(s)} · {hnAge(s)}
              </span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

function HiringPanel({
  data,
  errored,
}: {
  data: { companies?: Array<{ name: string; months?: number }> } | null;
  errored: boolean;
}) {
  const companies = (data?.companies ?? []).slice(0, 5);
  return (
    <section style={panelStyle}>
      <PanelHeading
        title={<>Who&apos;s Hiring · top 5</>}
        sub="last 3 threads"
        right={
          <span style={{ ...countStyle, color: T.t2 }}>▾ collapsed</span>
        }
      />
      {errored ? (
        <ErrorChip kind="hiring" />
      ) : companies.length === 0 ? (
        <EmptyRow label="awaiting hiring stats" />
      ) : (
        <div
          style={{
            padding: "8px 14px 11px",
            color: T.t2,
            fontFamily: T.fontMono,
            fontSize: 11,
            display: "flex",
            alignItems: "center",
            gap: 14,
            flexWrap: "wrap",
          }}
        >
          {companies.map((c, i) => (
            <span
              key={`${c.name}-${i}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "3px 8px",
                borderRadius: 5,
                border: `1px solid ${T.line2}`,
                background: T.bg3,
                color: T.t1,
              }}
            >
              <b style={{ color: T.t0, fontWeight: 600 }}>{c.name}</b>
              <span style={{ color: T.t3 }}>·</span>
              <span style={{ color: T.hn, fontWeight: 600 }}>
                {Number(c.months ?? 0)} mo
              </span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function EmptyRow({ label }: { label: string }) {
  return (
    <div
      style={{
        padding: "16px 14px",
        color: T.t3,
        fontFamily: T.fontMono,
        fontSize: 11,
      }}
    >
      {label}
    </div>
  );
}

/* ───────────────────────── PAGE ───────────────────────── */

const panelStyle: React.CSSProperties = {
  background: T.bg2,
  border: `1px solid ${T.line}`,
  borderRadius: 8,
  overflow: "hidden",
};
const countStyle: React.CSSProperties = {
  fontFamily: T.fontMono,
  fontSize: 11,
  color: T.t2,
};
const tabOnStyle: React.CSSProperties = {
  fontFamily: T.fontMono,
  fontSize: 11,
  color: T.hn,
  padding: "2px 7px",
  borderRadius: 999,
  border: `1px solid ${T.hnDim}`,
  background: "rgba(255,102,0,.05)",
};
const tileStyle: React.CSSProperties = {
  background: T.bg3,
  border: `1px solid ${T.line}`,
  borderRadius: 6,
  padding: "6px 9px 6px",
  cursor: "default",
};
const tileRow1: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginBottom: 2,
};
const tileName: React.CSSProperties = {
  fontFamily: T.fontMono,
  fontSize: 12,
  color: T.t0,
  fontWeight: 600,
  letterSpacing: ".02em",
};
const tileCount: React.CSSProperties = {
  fontFamily: T.fontMono,
  fontSize: 13,
  color: T.t0,
  fontWeight: 600,
};
const tileRow3: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginTop: 2,
  fontFamily: T.fontMono,
  fontSize: 10.5,
  color: T.t2,
};

export default function HackerNewsPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [, force] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/snapshot`, { cache: "no-store" });
      if (r.ok) {
        const data = (await r.json()) as Snapshot;
        setSnap(data);
        setLoadError(null);
      } else if (r.status === 503) {
        setSnap(null);
        setLoadError("cold");
      } else {
        setLoadError(`http ${r.status}`);
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "fetch failed");
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await fetch(`${API}/refresh`, { method: "POST" });
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  // Pure UI tick — re-renders so the "Nm ago" string updates. No network.
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const watchlist = useMemo(
    () =>
      snap?.meta.watchlist?.length
        ? snap.meta.watchlist
        : ["claude", "agent", "codex", "llm", "harness", "agentic"],
    [snap?.meta.watchlist],
  );

  const errors = new Set(snap?.meta.errors ?? []);
  const pulseErrored = (topic: string) => errors.has(`pulse_${topic}`);
  const pulses = useMemo(() => {
    const out: Record<string, Pulse | null> = {};
    for (const topic of watchlist) {
      out[topic] = pulseErrored(topic) ? null : (snap?.pulses?.[topic] ?? null);
    }
    return out;
  }, [snap, watchlist]);

  const storeCount = (snap?.top_stories?.length ?? 0) +
    (snap?.show_hn?.length ?? 0) +
    (snap?.ask_hn?.length ?? 0) +
    (snap?.controversial?.length ?? 0);

  return (
    <div
      className="hn-tab"
      style={{
        background: T.bg0,
        color: T.t0,
        fontFamily: T.fontSans,
        fontSize: 13,
        lineHeight: 1.45,
        minHeight: "100vh",
      }}
    >
      {/* Top bar */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto",
          alignItems: "center",
          gap: 16,
          padding: "8px 18px",
          borderBottom: `1px solid ${T.line}`,
          background: `linear-gradient(180deg, rgba(255,102,0,.02), transparent 70%), ${T.bg0}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, minWidth: 0 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 9,
              fontSize: 16,
              fontWeight: 600,
              color: T.t0,
            }}
          >
            <span
              style={{
                width: 18,
                height: 18,
                borderRadius: 4,
                background: T.hn,
                color: "#1a0d00",
                fontWeight: 800,
                fontSize: 12,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: T.fontMono,
              }}
            >
              Y
            </span>
            Hacker News
          </span>
          <span style={{ fontFamily: T.fontMono, fontSize: 11.5, color: T.t2 }}>
            operator <span style={{ color: T.t3 }}>/</span> intel{" "}
            <span style={{ color: T.t3 }}>/</span> hackernews
          </span>
        </div>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 14,
            fontFamily: T.fontMono,
            fontSize: 11.5,
            color: T.t2,
          }}
        >
          <span>
            store <b style={{ color: T.t0, fontWeight: 600 }}>{storeCount}</b>{" "}
            items
          </span>
          <StatusPill generatedAt={snap?.meta.generated_at} />
          <button
            onClick={refresh}
            disabled={refreshing}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "5px 9px",
              borderRadius: 6,
              border: `1px solid ${T.line2}`,
              background: T.bg2,
              color: T.t0,
              cursor: refreshing ? "wait" : "pointer",
              fontFamily: T.fontMono,
              fontSize: 11.5,
            }}
          >
            <RefreshCw
              size={12}
              style={{
                animation: refreshing ? "hn-spin 1.2s linear infinite" : "none",
              }}
            />
            {refreshing ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      </div>

      {/* Search */}
      <div
        style={{
          padding: "6px 18px 8px",
          borderBottom: `1px solid ${T.line}`,
          background: T.bg0,
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr auto auto",
            alignItems: "center",
            gap: 10,
            background: T.bg2,
            border: `1px solid ${T.line2}`,
            borderRadius: 6,
            padding: "5px 10px",
          }}
        >
          <Search size={13} color={T.t2} />
          <input
            disabled
            placeholder="Search across all synced HN content (search coming in Phase 2)"
            style={{
              background: "transparent",
              border: 0,
              outline: "none",
              color: T.t1,
              fontSize: 13.5,
              width: "100%",
            }}
          />
          <span
            style={{
              fontFamily: T.fontMono,
              fontSize: 11,
              color: T.t2,
              border: `1px solid ${T.line2}`,
              padding: "2px 7px",
              borderRadius: 5,
              background: T.bg3,
            }}
          >
            FTS5 · phase 2
          </span>
          <span
            style={{
              fontFamily: T.fontMono,
              fontSize: 10.5,
              color: T.t2,
              border: `1px solid ${T.line2}`,
              borderBottomWidth: 2,
              padding: "1px 6px",
              borderRadius: 4,
              background: T.bg3,
            }}
          >
            ⌘K
          </span>
        </div>
      </div>

      {/* Body */}
      {loadError === "cold" || (!snap && !loadError) ? (
        <div
          style={{
            padding: "32px 18px",
            color: T.t2,
            fontFamily: T.fontMono,
            fontSize: 12,
          }}
        >
          {loadError === "cold"
            ? "cold start · awaiting first sync — try Refresh now"
            : "loading…"}
        </div>
      ) : loadError ? (
        <div
          style={{
            padding: "32px 18px",
            color: T.down,
            fontFamily: T.fontMono,
            fontSize: 12,
          }}
        >
          load failed: {loadError}
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.55fr) minmax(0, 1fr)",
            gap: 10,
            padding: "10px 18px 14px",
          }}
        >
          <div>
            <TopStoriesPanel
              stories={snap!.top_stories}
              errored={errors.has("top_stories")}
              generatedAt={snap!.meta.generated_at}
            />
            <HeatedPanel
              rows={snap!.controversial}
              errored={errors.has("controversial")}
            />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
            <PulsePanel pulses={pulses} watchlist={watchlist} />
            <MoversPanel
              movers={snap!.movers}
              errored={errors.has("movers")}
            />
            <DuoStoriesPanel
              showHn={snap!.show_hn}
              askHn={snap!.ask_hn}
              showError={errors.has("show_hn")}
              askError={errors.has("ask_hn")}
            />
            <HiringPanel data={snap!.hiring} errored={errors.has("hiring")} />
          </div>
        </div>
      )}

      <div
        style={{
          padding: "6px 18px 12px",
          color: T.t3,
          fontFamily: T.fontMono,
          fontSize: 10.5,
          textAlign: "right",
        }}
      >
        hackernews-pp-cli · sqlite snapshot store · cron */30m · schema v
        {snap?.meta.schema_version ?? 1}
      </div>

      <style jsx global>{`
        @keyframes hn-spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
        }
        /* Row click affordance — subtle bg lift on hover, scoped to the HN tab. */
        .hn-tab a[href][target="_blank"]:hover {
          background: rgba(255, 102, 0, 0.04);
        }
      `}</style>
    </div>
  );
}
