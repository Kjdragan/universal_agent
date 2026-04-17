import { describe, expect, it } from "vitest";

import { resolveTaskWorkspaceTarget } from "./taskWorkspaceTarget";

describe("resolveTaskWorkspaceTarget", () => {
  it("prefers the live session id over workspace names", () => {
    expect(
      resolveTaskWorkspaceTarget({
        canonical_execution_run_id: "run_abc123",
        links: {
          workspace_name: "run_workspace_basename",
          session_id: "daemon_simone_todo",
        },
      }),
    ).toEqual({ sessionId: "daemon_simone_todo", runId: "run_abc123" });
  });

  it("falls back through canonical and assigned session ids", () => {
    expect(
      resolveTaskWorkspaceTarget({
        canonical_execution_session_id: "daemon_cody_todo",
        assigned_session_id: "daemon_simone_todo",
      }),
    ).toEqual({ sessionId: "daemon_cody_todo" });

    expect(
      resolveTaskWorkspaceTarget({
        assigned_session_id: "daemon_simone_todo",
      }),
    ).toEqual({ sessionId: "daemon_simone_todo" });
  });

  it("uses run-only mode only when no session id exists", () => {
    expect(
      resolveTaskWorkspaceTarget({
        canonical_execution_run_id: "run_history_only",
        links: {
          workspace_name: "run_history_workspace",
        },
      }),
    ).toEqual({ runId: "run_history_only" });
  });

  it("does not treat workspace paths as navigation identities", () => {
    expect(
      resolveTaskWorkspaceTarget({
        links: {
          workspace_name: "/tmp/AGENT_RUN_WORKSPACES/run_not_a_target",
        },
      }),
    ).toBeNull();
  });
});
