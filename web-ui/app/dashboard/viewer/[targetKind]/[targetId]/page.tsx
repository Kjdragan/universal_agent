// Centralized three-panel viewer page (Track B Commit 2 + Track C Commit 2).
//
// Renders chat history | logs | workspace files from a single backend
// hydration call. While readiness=pending, polls every 2s.
//
// Live writer mode (Track C C2): when the URL query has role=writer AND
// the resolved target is_live_session=true AND the feature flag
// NEXT_PUBLIC_UA_VIEWER_LIVE_WRITER=1 is set, the left panel renders the
// new <StreamingChat> component (WebSocket attach + composer) instead of
// the read-only HistoryPanel. With the flag OFF, the read-only Track B
// behavior is unchanged — that's the rollback path.
//
// Producers MUST reach this page via openViewer() (see lib/viewer/openViewer.ts).
// Building the URL by hand is the bug Track B exists to eliminate.

"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import StreamingChat from "@/components/chat/StreamingChat";
import type { HydrationPayload } from "@/lib/viewer/types";

const POLL_INTERVAL_MS = 2000;

type Params = { targetKind: string; targetId: string };

type FetchState =
  | { state: "loading" }
  | { state: "ok"; payload: HydrationPayload }
  | { state: "error"; status: number; message: string };

async function fetchHydration(
  targetKind: string,
  targetId: string,
): Promise<FetchState> {
  const url = `/api/viewer/hydrate?target_kind=${encodeURIComponent(
    targetKind,
  )}&target_id=${encodeURIComponent(targetId)}`;
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    return {
      state: "error",
      status: 0,
      message: err instanceof Error ? err.message : "network error",
    };
  }
  if (res.status === 200) {
    const payload = (await res.json()) as HydrationPayload;
    return { state: "ok", payload };
  }
  let message = `Hydrate returned ${res.status}`;
  try {
    const body = await res.json();
    message = body?.detail?.message || message;
  } catch {
    /* ignore */
  }
  return { state: "error", status: res.status, message };
}

function formatTime(ts: number | null): string {
  if (ts === null || ts === undefined) return "";
  try {
    return new Date(ts * 1000).toISOString().slice(11, 19);
  } catch {
    return "";
  }
}

function ReadinessBanner({
  payload,
  liveWriterFlag,
  currentRole,
}: {
  payload: HydrationPayload;
  liveWriterFlag: boolean;
  currentRole: string;
}) {
  const { readiness, target } = payload;
  let bg = "#222";
  let label = `Pending — ${readiness.reason ?? "no marker yet"}`;
  if (readiness.state === "ready") {
    bg = "#0d4d2e";
    label = `Ready — ${readiness.reason ?? "ok"}`;
  } else if (readiness.state === "failed") {
    bg = "#5a1414";
    label = `Failed — ${readiness.reason ?? "unknown"}`;
  }
  return (
    <div
      style={{
        background: bg,
        color: "#fff",
        padding: "8px 12px",
        fontFamily: "system-ui",
        fontSize: 13,
        display: "flex",
        gap: 16,
        alignItems: "center",
      }}
    >
      <span>{label}</span>
      <span style={{ opacity: 0.6 }}>
        {target.target_kind}/{target.target_id}
      </span>
      {target.is_live_session && currentRole !== "writer" ? (
        liveWriterFlag ? (
          // Track C C2: same route, writer mode → live composer attaches
          // via <StreamingChat>. No need to bounce out to the legacy root.
          <a
            href={`${target.viewer_href}?role=writer`}
            style={{
              marginLeft: "auto",
              color: "#7fffd4",
              textDecoration: "underline",
            }}
          >
            Connect live →
          </a>
        ) : (
          // Flag off (default until validated) → legacy root viewer.
          <a
            href={`/?session_id=${encodeURIComponent(target.session_id ?? target.target_id)}${
              target.run_id ? `&run_id=${encodeURIComponent(target.run_id)}` : ""
            }`}
            style={{
              marginLeft: "auto",
              color: "#7fffd4",
              textDecoration: "underline",
            }}
          >
            Connect live →
          </a>
        )
      ) : null}
      {currentRole === "writer" ? (
        <span style={{ marginLeft: "auto", color: "#7fffd4", fontWeight: 600 }}>
          ◉ writer
        </span>
      ) : null}
    </div>
  );
}

function HistoryPanel({ payload }: { payload: HydrationPayload }) {
  if (payload.history.length === 0) {
    return (
      <div style={{ padding: 16, color: "#888", fontFamily: "system-ui" }}>
        No history yet.{" "}
        {payload.readiness.state === "pending" ? "Waiting for trace.json or run.log…" : ""}
      </div>
    );
  }
  return (
    <div style={{ padding: 12, fontFamily: "system-ui", fontSize: 13 }}>
      {payload.history.map((m, i) => (
        <div
          key={i}
          style={{
            marginBottom: 12,
            padding: 8,
            borderLeft: `3px solid ${
              m.role === "user" ? "#5a8eff" :
              m.role === "assistant" ? "#7fffd4" :
              "#888"
            }`,
            background: "#181818",
          }}
        >
          <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>
            <strong style={{ textTransform: "uppercase", letterSpacing: "0.04em" }}>
              {m.role}
            </strong>
            {m.sub_agent ? <span style={{ marginLeft: 8 }}>via {m.sub_agent}</span> : null}
            {m.ts ? <span style={{ marginLeft: 8 }}>{formatTime(m.ts)}</span> : null}
          </div>
          <pre
            style={{
              margin: 0,
              whiteSpace: "pre-wrap",
              fontFamily: "ui-monospace, SF Mono, monospace",
              color: "#ddd",
            }}
          >
            {m.content}
          </pre>
        </div>
      ))}
    </div>
  );
}

function LogsPanel({ payload }: { payload: HydrationPayload }) {
  if (payload.logs.length === 0) {
    return (
      <div style={{ padding: 16, color: "#888", fontFamily: "system-ui" }}>
        No logs yet.
      </div>
    );
  }
  return (
    <pre
      style={{
        margin: 0,
        padding: 12,
        fontFamily: "ui-monospace, SF Mono, monospace",
        fontSize: 12,
        color: "#ddd",
        whiteSpace: "pre-wrap",
        background: "#0d0d0d",
        height: "100%",
        overflow: "auto",
      }}
    >
      {payload.logs.map((entry, i) => (
        <div
          key={i}
          style={{
            color:
              entry.level === "error" ? "#ff7070" :
              entry.level === "warn" ? "#ffcc70" :
              entry.level === "debug" ? "#888" :
              "#ddd",
          }}
        >
          <span style={{ opacity: 0.5 }}>[{entry.channel}]</span> {entry.message}
        </div>
      ))}
    </pre>
  );
}

function WorkspacePanel({ payload }: { payload: HydrationPayload }) {
  if (payload.workspace_entries.length === 0) {
    return (
      <div style={{ padding: 16, color: "#888", fontFamily: "system-ui" }}>
        Workspace is empty.
      </div>
    );
  }
  return (
    <div style={{ padding: 12, fontFamily: "system-ui", fontSize: 12 }}>
      <div style={{ color: "#888", fontSize: 11, marginBottom: 8 }}>
        {payload.workspace_root}
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {payload.workspace_entries.map((e) => (
            <tr key={e.name} style={{ borderBottom: "1px solid #222" }}>
              <td style={{ padding: "4px 8px" }}>
                {e.type === "dir" ? "📁" : "📄"} {e.name}
              </td>
              <td style={{ padding: "4px 8px", color: "#888", textAlign: "right" }}>
                {e.type === "file" ? `${e.size}b` : ""}
              </td>
              <td style={{ padding: "4px 8px", color: "#888" }}>
                {e.mtime ? formatTime(e.mtime) : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ViewerPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { targetKind, targetId } = use(params);
  const searchParams = useSearchParams();
  const role = searchParams?.get("role") ?? "viewer";
  const liveWriterFlag = process.env.NEXT_PUBLIC_UA_VIEWER_LIVE_WRITER === "1";
  const [state, setState] = useState<FetchState>({ state: "loading" });
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loop() {
      const result = await fetchHydration(targetKind, targetId);
      if (cancelled) return;
      setState(result);
      // Continue polling while still pending (and not in an error state).
      if (
        result.state === "ok" &&
        result.payload.readiness.state === "pending"
      ) {
        pollTimer.current = setTimeout(loop, POLL_INTERVAL_MS);
      }
    }

    loop();
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [targetKind, targetId]);

  const layoutStyle = useMemo<React.CSSProperties>(
    () => ({
      display: "grid",
      gridTemplateColumns: "1fr 1fr 320px",
      height: "calc(100vh - 36px)",
      background: "#0a0a0a",
      color: "#ddd",
    }),
    [],
  );

  if (state.state === "loading") {
    return (
      <div style={{ padding: 24, color: "#888", fontFamily: "system-ui" }}>
        Loading viewer…
      </div>
    );
  }

  if (state.state === "error") {
    return (
      <div style={{ padding: 24, color: "#ff7070", fontFamily: "system-ui" }}>
        <h1 style={{ marginTop: 0 }}>Could not load viewer</h1>
        <p>{state.message}</p>
        <p style={{ color: "#888", fontSize: 12 }}>
          Status {state.status}.{" "}
          {state.status === 404
            ? "The target_kind/target_id pair did not resolve. Check that you reached this page via openViewer() — building the URL by hand is the bug Track B fixes."
            : ""}
        </p>
      </div>
    );
  }

  const target = state.payload.target;
  const liveWriterEnabled =
    liveWriterFlag && role === "writer" && target.is_live_session;

  return (
    <div>
      <ReadinessBanner
        payload={state.payload}
        liveWriterFlag={liveWriterFlag}
        currentRole={role}
      />
      <div style={layoutStyle}>
        <div style={{ borderRight: "1px solid #222", overflow: "hidden" }}>
          {liveWriterEnabled ? (
            <StreamingChat
              sessionId={target.session_id}
              runId={target.run_id}
              readOnly={false}
            />
          ) : (
            <div style={{ overflow: "auto", height: "100%" }}>
              <HistoryPanel payload={state.payload} />
            </div>
          )}
        </div>
        <div style={{ borderRight: "1px solid #222", overflow: "auto" }}>
          <LogsPanel payload={state.payload} />
        </div>
        <div style={{ overflow: "auto" }}>
          <WorkspacePanel payload={state.payload} />
        </div>
      </div>
    </div>
  );
}
