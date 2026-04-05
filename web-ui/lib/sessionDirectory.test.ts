import { describe, expect, it, vi, afterEach } from "vitest";
import { fetchSessionDirectory } from "./sessionDirectory";

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 500): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("fetchSessionDirectory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("normalizes mixed ops and runs payloads into safe session rows", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/ops/sessions")) {
        return jsonResponse({
          sessions: [
            {
              session_id: "SESSION_ALPHA",
              status: null,
              source: null,
              channel: null,
              owner: null,
              memory_mode: null,
              description: null,
              workspace_dir: "/tmp/SESSION_ALPHA",
              last_activity: null,
              active_connections: null,
              active_runs: null,
              heartbeat_last: null,
            },
          ],
        });
      }

      if (url.includes("/api/v1/runs")) {
        return jsonResponse({
          runs: [
            {
              run_id: "run_alpha",
              workspace_dir: "/tmp/SESSION_ALPHA",
              status: null,
              run_kind: null,
              trigger_source: null,
              attempt_count: null,
            },
          ],
        });
      }

      throw new Error(`Unhandled request: ${url}`);
    });

    vi.stubGlobal("fetch", fetchMock);

    const rows = await fetchSessionDirectory(25);

    expect(rows).toEqual([
      expect.objectContaining({
        session_id: "SESSION_ALPHA",
        status: "unknown",
        source: "chat",
        channel: "chat",
        owner: "unknown",
        memory_mode: "direct_only",
        run_id: "run_alpha",
        run_status: undefined,
      }),
    ]);
  });
});
