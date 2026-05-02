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
    ).toEqual({ sessionId: "daemon_simone_todo" });
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

  // ── Track A regression guard ──────────────────────────────────────────────
  // A prior implementation stripped any session_id starting with `daemon_`,
  // which silently broke every Task Hub workspace link. The tests above pin
  // the correct behavior — these cases re-state the contract explicitly so
  // any future regression to that bug is caught immediately.
  it("does NOT strip daemon_ prefix from session ids (regression)", () => {
    expect(
      resolveTaskWorkspaceTarget({
        links: { session_id: "daemon_simone_todo" },
        canonical_execution_run_id: "run_xyz",
      }),
    ).toEqual({ sessionId: "daemon_simone_todo" });

    expect(
      resolveTaskWorkspaceTarget({
        canonical_execution_session_id: "daemon_cody_todo",
      }),
    ).toEqual({ sessionId: "daemon_cody_todo" });

    expect(
      resolveTaskWorkspaceTarget({
        assigned_session_id: "daemon_atlas_todo",
      }),
    ).toEqual({ sessionId: "daemon_atlas_todo" });
  });

  // ── Daemon-task split regression guard ────────────────────────────────────
  // Simone-todo / daemon-executed tasks carry BOTH a session_id (the daemon
  // executor's persistent workspace, where the agent actually wrote logs +
  // produced work_products/) and a run_id (the per-task run record's
  // metadata-only workspace: manifest, activity journal, attempts/). Users
  // clicking "Workspace" want to see the agent's actual work.
  //
  // The backend resolver tries run_id FIRST and stops there — so passing
  // both makes the empty run-metadata workspace win. To get the daemon's
  // full session workspace, we must drop run_id from the payload when
  // session_id is present.
  it("drops runId when sessionId is present (daemon task content fix)", () => {
    expect(
      resolveTaskWorkspaceTarget({
        links: { session_id: "daemon_simone_todo" },
        canonical_execution_run_id: "run_d4f966853f82",
      }),
    ).toEqual({ sessionId: "daemon_simone_todo" });
  });
});
