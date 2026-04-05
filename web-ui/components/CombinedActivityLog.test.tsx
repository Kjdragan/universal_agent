import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CombinedActivityLog } from "./CombinedActivityLog";
import { useAgentStore } from "@/lib/store";

describe("CombinedActivityLog", () => {
  beforeEach(() => {
    useAgentStore.getState().reset();
  });

  afterEach(() => {
    useAgentStore.getState().reset();
  });

  it("renders sanitized attachment payloads instead of raw base64", () => {
    useAgentStore.setState({
      toolCalls: [
        {
          id: "tool-1",
          name: "prepare_agentmail_attachment",
          input: {
            filename: "story.md",
            content_id: "story",
            path: "/tmp/story.md",
          },
          status: "complete",
          timestamp: Date.now(),
          time_offset: 0,
          result: {
            tool_use_id: "tool-1",
            is_error: false,
            content_preview: JSON.stringify({
              filename: "story.md",
              content_id: "story",
              content: "A".repeat(4096),
            }),
            content_size: 4096,
          },
        },
      ],
    });

    render(<CombinedActivityLog />);

    expect(screen.getByText(/prepare_agentmail_attachment/i)).toBeInTheDocument();
    expect(screen.getByText(/\[redacted content/i)).toBeInTheDocument();
    expect(screen.queryByText(/AAAAAA/)).not.toBeInTheDocument();
  });
});
