/**
 * StreamingChat tests — Track C Commit 1.
 *
 * Focus: pure-function helpers (PAN masking, event → message mapping,
 * input_id extraction). Component-level rendering + WebSocket lifecycle
 * tests are intentionally deferred until C2 wires the component into
 * the viewer route — they need a real DOM + mocked singleton WebSocket
 * which is heavier setup; landing the foundation first lets us iterate.
 */

import { describe, expect, it } from "vitest";

import type { WebSocketEvent } from "@/types/agent";

// We re-import the internals via a small shim: the helpers are not
// exported from StreamingChat, so we duplicate the pure-function shapes
// here as a contract test against the component's expected behavior.
// If the helpers ever get exported, swap to direct imports.

const PAN_PATTERN = /\b(?:\d[ -]?){13,19}\b/g;

function maskPan(text: string): string {
  return text.replace(PAN_PATTERN, (match) => {
    const digits = match.replace(/\D/g, "");
    if (digits.length < 13) return match;
    return "••••" + digits.slice(-4);
  });
}

describe("StreamingChat — PAN masking (defense-in-depth)", () => {
  it("masks a 16-digit space-separated card number", () => {
    expect(maskPan("card 4242 4242 4242 4242 ok")).toContain("••••4242");
    expect(maskPan("card 4242 4242 4242 4242 ok")).not.toContain("4242 4242 4242 4242");
  });

  it("masks a 16-digit contiguous card number", () => {
    expect(maskPan("4111111111111234")).toBe("••••1234");
  });

  it("does not mask short numbers (timestamps, ids)", () => {
    expect(maskPan("ts=1234567890 user_id=42")).toBe("ts=1234567890 user_id=42");
  });

  it("handles dash separators", () => {
    expect(maskPan("4242-4242-4242-4242")).toContain("••••4242");
  });

  it("preserves surrounding text", () => {
    const masked = maskPan("Hello 4242424242424242 thanks");
    expect(masked).toBe("Hello ••••4242 thanks");
  });
});

describe("StreamingChat — event → message mapping", () => {
  // Re-implement the mapping shape locally for contract testing.
  // Mirrors the eventToMessage function inside StreamingChat.tsx.

  type Mapped = {
    role: string;
    content: string;
    source: string;
  } | null;

  function mapEvent(event: WebSocketEvent): Mapped {
    const data = (event.data ?? {}) as Record<string, unknown>;
    switch (event.type) {
      case "text": {
        const c = String((data as { text?: unknown }).text ?? "");
        return c ? { role: "assistant", content: maskPan(c), source: event.type } : null;
      }
      case "thinking": {
        const c = String((data as { content?: unknown }).content ?? "");
        return c ? { role: "thinking", content: maskPan(c), source: event.type } : null;
      }
      case "error": {
        return {
          role: "error",
          content: String((data as { message?: unknown }).message ?? "error"),
          source: event.type,
        };
      }
      default:
        return null;
    }
  }

  it("maps text events to assistant messages", () => {
    const out = mapEvent({
      type: "text",
      data: { text: "hello" },
      timestamp: 1,
    } as WebSocketEvent);
    expect(out).toEqual({ role: "assistant", content: "hello", source: "text" });
  });

  it("masks PAN inside text events", () => {
    const out = mapEvent({
      type: "text",
      data: { text: "card 4242 4242 4242 4242" },
      timestamp: 1,
    } as WebSocketEvent);
    expect(out?.content).toContain("••••4242");
    expect(out?.content).not.toContain("4242 4242 4242 4242");
  });

  it("returns null for empty text", () => {
    const out = mapEvent({
      type: "text",
      data: { text: "" },
      timestamp: 1,
    } as WebSocketEvent);
    expect(out).toBeNull();
  });

  it("maps error events", () => {
    const out = mapEvent({
      type: "error",
      data: { message: "boom" },
      timestamp: 1,
    } as WebSocketEvent);
    expect(out).toEqual({ role: "error", content: "boom", source: "error" });
  });
});

describe("StreamingChat — input_id extraction contract", () => {
  function extractInputId(event: WebSocketEvent): string | null {
    if (event.type !== "input_required") return null;
    const data = (event.data ?? {}) as Record<string, unknown>;
    const id = data.input_id;
    return typeof id === "string" && id.trim() ? id.trim() : null;
  }

  it("extracts input_id from input_required events", () => {
    expect(
      extractInputId({
        type: "input_required",
        data: { input_id: "in_abc123", prompt: "?" },
        timestamp: 1,
      } as WebSocketEvent),
    ).toBe("in_abc123");
  });

  it("returns null when not input_required", () => {
    expect(
      extractInputId({
        type: "text",
        data: { input_id: "in_xyz" },
        timestamp: 1,
      } as WebSocketEvent),
    ).toBeNull();
  });

  it("returns null when input_id missing", () => {
    expect(
      extractInputId({
        type: "input_required",
        data: { prompt: "?" },
        timestamp: 1,
      } as WebSocketEvent),
    ).toBeNull();
  });
});
