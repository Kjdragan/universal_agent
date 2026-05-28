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
    ).toEqual({
      sessionId: "daemon_simone_todo",
      workspaceName: "run_workspace_basename",
    });
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

  it("uses run-only mode when no session id exists", () => {
    expect(
      resolveTaskWorkspaceTarget({
        canonical_execution_run_id: "run_history_only",
        links: {
          workspace_name: "run_history_workspace",
        },
      }),
    ).toEqual({
      runId: "run_history_only",
      workspaceName: "run_history_workspace",
    });
  });

  // ── VP-mission workspace fallback (2026-05-28 regression fix) ─────────────
  // When a Task Hub card was delegated to Cody / Atlas, the completed-card
  // enrichment sets sessionId/runId to ``vp-mission-<id>``. That mirror id
  // is NOT a key the backend resolver's session/run catalog recognizes — it
  // returns 404. The same payload always carries the VP mission workspace
  // dir + name; the resolver's workspace-path branch finds Cody's mission
  // directory and returns a usable target. Propagate every hint we have so
  // openViewer() can fall through cleanly.
  it("propagates workspace_dir and workspace_name when present", () => {
    expect(
      resolveTaskWorkspaceTarget({
        links: {
          session_id: "vp-mission-79ffc956d5fe046e07182583",
          workspace_dir:
            "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external/vp-mission-79ffc956d5fe046e07182583/vp-mission-79ffc956d5fe046e07182583",
          workspace_name:
            "vp_coder_primary_external/vp-mission-79ffc956d5fe046e07182583/vp-mission-79ffc956d5fe046e07182583",
        },
        canonical_execution_session_id: "vp-mission-79ffc956d5fe046e07182583",
        canonical_execution_run_id: "vp-mission-79ffc956d5fe046e07182583",
        canonical_execution_workspace:
          "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external/vp-mission-79ffc956d5fe046e07182583/vp-mission-79ffc956d5fe046e07182583",
      }),
    ).toEqual({
      sessionId: "vp-mission-79ffc956d5fe046e07182583",
      workspaceDir:
        "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external/vp-mission-79ffc956d5fe046e07182583/vp-mission-79ffc956d5fe046e07182583",
      workspaceName:
        "vp_coder_primary_external/vp-mission-79ffc956d5fe046e07182583/vp-mission-79ffc956d5fe046e07182583",
    });
  });

  it("returns a valid target when only workspace info is present", () => {
    // Pure workspace-only resolution path: e.g. a vp_mission mirror row
    // that has no assignment session/run but carries a result_ref-derived
    // workspace_dir. Pre-fix this returned null and the Workspace button
    // was hidden (or rendered and 404'd).
    expect(
      resolveTaskWorkspaceTarget({
        canonical_execution_workspace:
          "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external/vp-mission-abc/vp-mission-abc",
        links: {
          workspace_dir:
            "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external/vp-mission-abc/vp-mission-abc",
          workspace_name: "vp_coder_primary_external/vp-mission-abc/vp-mission-abc",
        },
      }),
    ).toEqual({
      workspaceDir:
        "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external/vp-mission-abc/vp-mission-abc",
      workspaceName: "vp_coder_primary_external/vp-mission-abc/vp-mission-abc",
    });
  });

  it("returns null only when no identity hint at all is present", () => {
    expect(resolveTaskWorkspaceTarget({})).toBeNull();
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
