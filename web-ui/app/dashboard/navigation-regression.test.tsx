import type { AnchorHTMLAttributes } from "react";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "./page";
import ToDoListDashboardPage from "./todolist/page";

const fetchSessionDirectoryMock = vi.fn();

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

vi.mock("@/lib/chatWindow", () => ({
  openOrFocusChatWindow: vi.fn(),
}));

vi.mock("@/lib/sessionDirectory", () => ({
  fetchSessionDirectory: (...args: unknown[]) => fetchSessionDirectoryMock(...args),
  deleteSessionDirectoryEntry: vi.fn(),
}));

vi.mock("@/components/agent-flow/AgentFlowWidget", () => ({
  AgentFlowWidget: () => <div data-testid="agent-flow-widget">agent-flow-widget</div>,
}));

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 500): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("dashboard navigation regression", () => {
  const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>();

  beforeEach(() => {
    fetchSessionDirectoryMock.mockReset();
    fetchSessionDirectoryMock.mockResolvedValue([
      {
        session_id: "session_alpha",
        status: "active",
        source: "chat",
        channel: "chat",
        owner: "owner_primary",
        memory_mode: "direct_only",
        description: "Alpha session",
        workspace_dir: "/tmp/session_alpha",
        last_activity: "2026-04-05T15:00:00Z",
        is_live_session: true,
      },
    ]);

    localStorage.clear();
    localStorage.setItem("ua.deleted_completed_tasks.v1", "{not-json");

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);

      if (url.includes("/api/v1/dashboard/summary")) {
        return jsonResponse({
          sessions: { active: 1, total: 1 },
          approvals: { pending: 0, total: 0 },
          cron: { total: 1, enabled: 1 },
          notifications: { unread: 1, total: 1 },
          deployment_profile: { profile: "production" },
        });
      }

      if (url.includes("/api/v1/dashboard/notifications?")) {
        return jsonResponse({
          notifications: [
            {
              id: "notif-1",
              title: "Sanitized notification",
              kind: null,
              message: "Payload shape varies in production",
              severity: "error",
              status: "new",
              created_at: "2026-04-05T15:00:00Z",
              session_id: null,
              metadata: null,
            },
          ],
        });
      }

      if (url.includes("/api/v1/dashboard/approvals/highlight")) {
        return jsonResponse({
          pending_count: 0,
          banner: { show: false, text: "", focus_href: "/dashboard/todolist" },
          approvals: [],
        });
      }

      if (url.includes("/api/v1/ops/vp/sessions")) {
        return jsonResponse({ sessions: [] });
      }

      if (url.includes("/api/v1/ops/vp/missions")) {
        return jsonResponse({ missions: [] });
      }

      if (url.includes("/api/v1/ops/metrics/vp")) {
        return jsonResponse({
          latency_seconds: { count: 0, avg_seconds: null, p95_seconds: null, max_seconds: null },
          recent_events: [],
        });
      }

      if (url.includes("/api/v1/dashboard/todolist/overview")) {
        return jsonResponse({
          status: "ok",
          approvals_pending: 0,
          queue_health: {
            dispatch_queue_size: 0,
            dispatch_eligible: 0,
            threshold: 3,
            status_counts: {},
            source_counts: {},
          },
          agent_activity: { active_agents: 0, active_assignments: 0, backlog_open: 0 },
          heartbeat: {
            enabled: true,
            configured_every_seconds: 30,
            min_interval_seconds: 30,
            effective_default_every_seconds: 30,
            session_count: 0,
            session_state_count: 0,
            busy_sessions: 0,
          },
          todo_dispatch: {},
        });
      }

      if (url.includes("/api/v1/dashboard/todolist/agent-queue")) {
        return jsonResponse({
          status: "ok",
          items: [],
          pagination: { total: 0, offset: 0, limit: 120, count: 0, has_more: false },
        });
      }

      if (url.includes("/api/v1/dashboard/todolist/agent-activity")) {
        return jsonResponse({
          active_agents: 0,
          active_assignments: [],
          metrics: {
            "1h": { new: 0, seized: 0, rejected: 0, completed: 0 },
            "24h": { new: 0, seized: 0, rejected: 0, completed: 0 },
          },
          backlog_open: 0,
        });
      }

      if (url.includes("/api/v1/dashboard/todolist/completed")) {
        return jsonResponse({ status: "ok", items: [] });
      }

      if (url.includes("/api/v1/dashboard/todolist/morning-report")) {
        return jsonResponse({ report: null });
      }

      throw new Error(`Unhandled fetch request in test: ${url}`);
    });

    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("survives dashboard -> todolist -> dashboard renders with production-like payloads", async () => {
    const firstDashboard = render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
    expect(screen.getByText("Sanitized notification")).toBeInTheDocument();
    expect(screen.getByTestId("agent-flow-widget")).toBeInTheDocument();

    firstDashboard.unmount();

    const todoList = render(<ToDoListDashboardPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Task Hub" })).toBeInTheDocument();
    });
    expect(screen.getByText(/No unassigned tasks\./i)).toBeInTheDocument();

    todoList.unmount();

    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
    expect(screen.getByText("Sanitized notification")).toBeInTheDocument();
    expect(screen.queryByText(/Application error: a client-side exception has occurred/i)).not.toBeInTheDocument();
  });
});
