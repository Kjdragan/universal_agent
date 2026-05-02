import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { openViewer, resolveSessionViewTarget } from "./openViewer";

const FAKE_TARGET = {
  target_kind: "run",
  target_id: "run_abc",
  run_id: "run_abc",
  session_id: null,
  workspace_dir: "/tmp/ws",
  is_live_session: false,
  source: "test",
  viewer_href: "/dashboard/viewer/run/run_abc",
};

describe("resolveSessionViewTarget", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns no_inputs when nothing provided", async () => {
    const r = await resolveSessionViewTarget({});
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("no_inputs");
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("posts only the non-empty fields", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      json: async () => FAKE_TARGET,
    });
    await resolveSessionViewTarget({
      session_id: "daemon_simone_todo",
      run_id: "",
      workspace_dir: undefined,
    });
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/viewer/resolve");
    expect(JSON.parse(call[1].body)).toEqual({ session_id: "daemon_simone_todo" });
  });

  it("returns the target on 200", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      json: async () => FAKE_TARGET,
    });
    const r = await resolveSessionViewTarget({ run_id: "run_abc" });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.target.viewer_href).toBe("/dashboard/viewer/run/run_abc");
  });

  it("maps 404 to viewer_target_not_found", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 404,
      json: async () => ({ detail: { code: "viewer_target_not_found" } }),
    });
    const r = await resolveSessionViewTarget({ run_id: "run_unknown" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("viewer_target_not_found");
  });

  it("maps network errors", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("offline"),
    );
    const r = await resolveSessionViewTarget({ run_id: "x" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.code).toBe("network_error");
      expect(r.message).toContain("offline");
    }
  });
});

describe("openViewer", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
    // Stub window.open + window.location for jsdom
    Object.defineProperty(window, "open", {
      writable: true,
      value: vi.fn(() => ({ focus: vi.fn() })),
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens the legacy app/page.tsx URL with session_id/run_id query params", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      json: async () => FAKE_TARGET,
    });
    await openViewer({
      run_id: "run_abc",
      attachMode: "tail",
      role: "viewer",
    });
    const opened = (window.open as ReturnType<typeof vi.fn>).mock.calls[0][0];
    // Legacy three-panel UI lives at `/`. We pass session_id+run_id via
    // query so app/page.tsx can rehydrate via trace.json + run.log.
    expect(opened).toContain("run_id=run_abc");
    expect(opened).not.toContain("/dashboard/viewer/");
    expect(opened).toContain("attach=tail");
    expect(opened).toContain("role=viewer");
  });

  it("includes session_id when the resolver returns one", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      json: async () => ({
        ...FAKE_TARGET,
        target_kind: "session",
        target_id: "daemon_simone_todo",
        session_id: "daemon_simone_todo",
        run_id: "run_xyz",
      }),
    });
    await openViewer({ session_id: "daemon_simone_todo" });
    const opened = (window.open as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(opened).toContain("session_id=daemon_simone_todo");
    expect(opened).toContain("run_id=run_xyz");
  });

  it("does not call window.open when not in browser context", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      json: async () => FAKE_TARGET,
    });
    const orig = global.window;
    // @ts-expect-error: simulate SSR
    delete global.window;
    const r = await openViewer({ run_id: "run_abc" });
    // restore
    global.window = orig;
    expect(r.ok).toBe(true);
  });

  it("returns error result for unknown targets without throwing", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 404,
      json: async () => ({ detail: { code: "viewer_target_not_found" } }),
    });
    // Suppress alert
    Object.defineProperty(window, "alert", { writable: true, value: vi.fn() });
    const r = await openViewer({ run_id: "run_unknown", inPlace: true });
    expect(r.ok).toBe(false);
  });
});
