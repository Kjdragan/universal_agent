import { describe, expect, it } from "vitest";

import { buildChatUrl } from "./chatWindow";

describe("buildChatUrl", () => {
  it("can carry live session attachment and durable run context together", () => {
    expect(
      buildChatUrl({
        sessionId: "daemon_simone_todo",
        runId: "run_dashboard_lineage",
        attachMode: "tail",
        role: "viewer",
      }),
    ).toBe("/?session_id=daemon_simone_todo&run_id=run_dashboard_lineage&attach=tail&role=viewer");
  });
});
