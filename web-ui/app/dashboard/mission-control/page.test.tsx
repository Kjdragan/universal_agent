/**
 * Phase 2 — Mission Control async refresh UI.
 *
 * Pins the contract between the new async-job backend (Phase 1) and the
 * dashboard panel:
 *   - Click "Refresh Mission Control" → POST /api/v1/dashboard/mission-control/refresh
 *   - Frontend polls GET /api/v1/dashboard/mission-control/refresh/{job_id}
 *   - Pill walks through "Refreshing cards…" → "Synthesizing brief…" → idle
 *   - On completion, the rest of the dashboard re-fetches (refreshKey bump)
 *   - On failure, an inline retry banner surfaces phase + error
 *   - Polling stops on terminal state
 *
 * Tests pass `window.__UA_MC_POLL_MS = 10` so the polling loop runs at
 * ~10ms rather than the production 2000ms. This avoids fake timers,
 * which interact badly with the page's own setInterval(60_000).
 */

import type { AnchorHTMLAttributes } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MissionControlPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 500): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: new Headers(),
  } as Response;
}

const TILES_PAYLOAD = {
  status: "ok",
  generated_at: "2026-05-04T17:00:00Z",
  tiles: [],
};

const CARDS_PAYLOAD = { status: "ok", cards: [] };

const QUEUE_PAYLOAD = {
  status: "ok",
  items: [],
  pagination: { total: 0, offset: 0, limit: 10, count: 0, has_more: false },
};

const HEALTH_PAYLOAD = {
  status: "healthy",
  timestamp: "2026-05-04T17:00:00Z",
  db_status: "connected",
};

const READOUT_PAYLOAD_BEFORE = {
  status: "ok",
  generated_at: "2026-05-04T16:00:00Z",
  readout: {
    id: "before",
    headline: "Stale Sunday brief.",
    generated_at_utc: "2026-05-04T16:00:00Z",
    executive_snapshot: [],
    sections: [],
  },
  journal: [],
};

const READOUT_PAYLOAD_AFTER = {
  status: "ok",
  generated_at: "2026-05-04T17:01:00Z",
  readout: {
    id: "after",
    headline: "Fresh Monday brief.",
    generated_at_utc: "2026-05-04T17:01:00Z",
    executive_snapshot: [],
    sections: [],
  },
  journal: [],
};

type FetchInput = RequestInfo | URL;

function buildFetchMock(jobScript: Array<Record<string, unknown>>) {
  let postCount = 0;
  let pollIdx = 0;
  let readoutAfterRefresh = false;
  const calls: { method: string; url: string }[] = [];

  const mock = vi.fn(async (input: FetchInput, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ method, url });

    if (url.endsWith("/api/v1/dashboard/mission-control/refresh") && method === "POST") {
      postCount += 1;
      // Reset poll cursor so a retry walks the script from the top.
      pollIdx = 0;
      readoutAfterRefresh = false;
      return jsonResponse(
        {
          job_id: `job-${postCount}`,
          status: "queued",
          created_at: "2026-05-04T17:00:00Z",
        },
        true,
        202,
      );
    }

    if (url.includes("/api/v1/dashboard/mission-control/refresh/")) {
      const next = jobScript[Math.min(pollIdx, jobScript.length - 1)] || { status: "completed" };
      pollIdx += 1;
      if ((next.status as string) === "completed") {
        readoutAfterRefresh = true;
      }
      return jsonResponse(next);
    }

    if (url.includes("/api/v1/dashboard/chief-of-staff")) {
      return jsonResponse(readoutAfterRefresh ? READOUT_PAYLOAD_AFTER : READOUT_PAYLOAD_BEFORE);
    }

    if (url.includes("/api/v1/dashboard/mission-control/tiles")) return jsonResponse(TILES_PAYLOAD);
    if (url.includes("/api/v1/dashboard/mission-control/cards")) return jsonResponse(CARDS_PAYLOAD);
    if (url.includes("/api/v1/dashboard/todolist/agent-queue")) return jsonResponse(QUEUE_PAYLOAD);
    if (url.includes("/api/v1/health")) return jsonResponse(HEALTH_PAYLOAD);
    return jsonResponse({});
  });

  return {
    mock,
    counts: () => ({
      postCount,
      pollIdx,
      calls,
    }),
  };
}

declare global {
  interface Window {
    __UA_MC_POLL_MS?: number;
  }
}

describe("Mission Control async refresh", () => {
  beforeEach(() => {
    // Drive the polling loop fast so multi-step scripts finish well
    // within the default vitest test timeout.
    window.__UA_MC_POLL_MS = 10;
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete window.__UA_MC_POLL_MS;
  });

  it("posts to the async refresh endpoint and disables the button while in flight", async () => {
    // Use a script that holds in cards_running for several polls so the
    // assertion has time to observe the disabled in-flight state.
    const harness = buildFetchMock([
      { job_id: "job-1", status: "cards_running" },
      { job_id: "job-1", status: "cards_running" },
      { job_id: "job-1", status: "cards_running" },
      { job_id: "job-1", status: "cards_running" },
      { job_id: "job-1", status: "completed", readout_id: "after" },
    ]);
    vi.stubGlobal("fetch", harness.mock);

    render(<MissionControlPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/Stale Sunday brief\./).length).toBeGreaterThan(0);
    });

    const button = screen.getByRole("button", { name: /Refresh Mission Control/i });
    fireEvent.click(button);

    await waitFor(() => expect(harness.counts().postCount).toBe(1));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Refreshing cards/i })).toBeDisabled(),
    );
  });

  it("walks pill through cards_running → readout_running → completed", async () => {
    const harness = buildFetchMock([
      { job_id: "job-1", status: "cards_running" },
      { job_id: "job-1", status: "cards_running" },
      { job_id: "job-1", status: "readout_running" },
      { job_id: "job-1", status: "readout_running" },
      { job_id: "job-1", status: "completed", readout_id: "after" },
    ]);
    vi.stubGlobal("fetch", harness.mock);

    render(<MissionControlPage />);
    await waitFor(() => expect(screen.getAllByText(/Stale Sunday brief\./).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /Refresh Mission Control/i }));

    await waitFor(() => screen.getByRole("button", { name: /Refreshing cards/i }));
    await waitFor(() => screen.getByRole("button", { name: /Synthesizing brief/i }));
    await waitFor(() => screen.getByRole("button", { name: /Refresh Mission Control/i }));
  });

  it("triggers a dashboard-wide reload when the job completes", async () => {
    const harness = buildFetchMock([
      { job_id: "job-1", status: "completed", readout_id: "after" },
    ]);
    vi.stubGlobal("fetch", harness.mock);

    render(<MissionControlPage />);
    await waitFor(() => expect(screen.getAllByText(/Stale Sunday brief\./).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /Refresh Mission Control/i }));

    await waitFor(
      () => expect(screen.getAllByText(/Fresh Monday brief\./).length).toBeGreaterThan(0),
      { timeout: 4000 },
    );
  });

  it("shows a retry-able error banner on terminal failed status", async () => {
    const harness = buildFetchMock([
      { job_id: "job-1", status: "cards_running" },
      {
        job_id: "job-1",
        status: "failed",
        failed_phase: "readout",
        error: "anthropic 500 from upstream",
      },
    ]);
    vi.stubGlobal("fetch", harness.mock);

    render(<MissionControlPage />);
    await waitFor(() => expect(screen.getAllByText(/Stale Sunday brief\./).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /Refresh Mission Control/i }));

    await waitFor(
      () => expect(screen.getByText(/anthropic 500 from upstream/)).toBeInTheDocument(),
      { timeout: 4000 },
    );

    // Two elements contain "Try again" — the main button label
    // ("Refresh failed — Try again") and the inline banner retry
    // button ("Try again"). Match the latter with an exact-name regex.
    fireEvent.click(screen.getByRole("button", { name: /^Try again$/i }));
    await waitFor(() => expect(harness.counts().postCount).toBe(2));
  });

  it("stops polling on terminal status (does not keep hitting GET forever)", async () => {
    const harness = buildFetchMock([
      { job_id: "job-1", status: "completed", readout_id: "after" },
    ]);
    vi.stubGlobal("fetch", harness.mock);

    render(<MissionControlPage />);
    await waitFor(() => expect(screen.getAllByText(/Stale Sunday brief\./).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /Refresh Mission Control/i }));

    // Wait for completion.
    await waitFor(
      () => expect(screen.getAllByText(/Fresh Monday brief\./).length).toBeGreaterThan(0),
      { timeout: 4000 },
    );

    const pollsAtCompletion = harness.counts().pollIdx;
    // Sleep multiple poll intervals; pollIdx must NOT advance.
    await new Promise((r) => setTimeout(r, 200));
    expect(harness.counts().pollIdx).toBe(pollsAtCompletion);
  });
});
