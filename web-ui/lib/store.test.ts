import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { processWebSocketEvent, sanitizeToolPayload, useAgentStore } from "./store";
import type { WebSocketEvent } from "@/types/agent";

describe("tool payload sanitization", () => {
  beforeEach(() => {
    useAgentStore.getState().reset();
  });

  afterEach(() => {
    useAgentStore.getState().reset();
  });

  it("redacts attachment content while preserving metadata", () => {
    const sanitized = sanitizeToolPayload({
      filename: "story.md",
      content_id: "story",
      content: "A".repeat(2048),
    }) as Record<string, unknown>;

    expect(sanitized.filename).toBe("story.md");
    expect(sanitized.content_id).toBe("story");
    expect(String(sanitized.content)).toContain("[redacted content");
  });

  it("sanitizes live tool events before storing them for activity rendering", () => {
    const toolCallEvent: WebSocketEvent = {
      type: "tool_call",
      timestamp: Date.now(),
      data: {
        id: "tool-1",
        name: "prepare_agentmail_attachment",
        input: {
          path: "/tmp/story.md",
          filename: "story.md",
          content_id: "story",
        },
        time_offset: 0,
      },
    };

    const toolResultEvent: WebSocketEvent = {
      type: "tool_result",
      timestamp: Date.now(),
      data: {
        tool_use_id: "tool-1",
        is_error: false,
        content_preview: JSON.stringify({
          filename: "story.md",
          content_id: "story",
          content: "B".repeat(4096),
        }),
        content_size: 4096,
      },
    };

    processWebSocketEvent(toolCallEvent);
    processWebSocketEvent(toolResultEvent);

    const [stored] = useAgentStore.getState().toolCalls;
    expect(stored.name).toBe("prepare_agentmail_attachment");
    expect(stored.result?.content_preview).toContain("[redacted content");
    expect(stored.result?.content_preview).toContain("story.md");
    expect(stored.result?.content_preview).not.toContain("BBBBBBBB");
  });
});
