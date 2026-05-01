// Single canonical entry point for opening the three-panel viewer.
//
// Every producer (Task Hub, Sessions, Calendar, Proactive, Chat) MUST
// call openViewer() instead of building viewer URLs locally. This is
// the contract that ends the per-producer URL drift documented in
// docs/three_panel_viewer_track_b_spec.md.

import { CHAT_WINDOW_NAME } from "@/lib/chatWindow";
import type { ResolveInput, SessionViewTarget } from "@/lib/viewer/types";

export type OpenViewerOptions = ResolveInput & {
  attachMode?: "default" | "tail";
  role?: "writer" | "viewer";
  /** When true, navigate in-place instead of opening a new window. */
  inPlace?: boolean;
};

export type OpenViewerResult =
  | { ok: true; target: SessionViewTarget }
  | { ok: false; code: string; message: string };

/** POST to /api/viewer/resolve and return the typed target. */
export async function resolveSessionViewTarget(
  input: ResolveInput,
): Promise<OpenViewerResult> {
  const body: ResolveInput = {};
  if (input.session_id) body.session_id = input.session_id;
  if (input.run_id) body.run_id = input.run_id;
  if (input.workspace_dir) body.workspace_dir = input.workspace_dir;
  if (input.workspace_name) body.workspace_name = input.workspace_name;

  if (Object.keys(body).length === 0) {
    return {
      ok: false,
      code: "no_inputs",
      message: "openViewer requires at least one of session_id, run_id, workspace_dir, or workspace_name.",
    };
  }

  let response: Response;
  try {
    response = await fetch("/api/viewer/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    return {
      ok: false,
      code: "network_error",
      message: err instanceof Error ? err.message : String(err),
    };
  }

  if (response.status === 200) {
    const target = (await response.json()) as SessionViewTarget;
    return { ok: true, target };
  }

  if (response.status === 404) {
    return {
      ok: false,
      code: "viewer_target_not_found",
      message: "Could not resolve a viewer target for this item.",
    };
  }

  return {
    ok: false,
    code: `http_${response.status}`,
    message: `Resolver returned ${response.status}`,
  };
}

/** Resolve, then navigate. Single function every producer should call. */
export async function openViewer(options: OpenViewerOptions): Promise<OpenViewerResult> {
  const result = await resolveSessionViewTarget(options);
  if (!result.ok) {
    if (typeof window !== "undefined" && options.inPlace !== true) {
      // Best-effort surface: alert is acceptable for a missing-target case
      // because it's user-initiated and rare. Producers can override by
      // catching the result and rendering their own toast.
      // eslint-disable-next-line no-alert
      window.alert(result.message);
    }
    return result;
  }

  if (typeof window === "undefined") {
    return result;
  }

  const url = new URL(result.target.viewer_href, window.location.origin);
  if (options.attachMode === "tail") {
    url.searchParams.set("attach", "tail");
  }
  if (options.role === "viewer") {
    url.searchParams.set("role", "viewer");
  }

  const href = url.toString();
  if (options.inPlace) {
    window.location.href = href;
  } else {
    const opened = window.open(href, CHAT_WINDOW_NAME);
    if (opened && typeof opened.focus === "function") {
      opened.focus();
    } else {
      // Popup blocked — fall through to in-place navigation.
      window.location.href = href;
    }
  }

  return result;
}
