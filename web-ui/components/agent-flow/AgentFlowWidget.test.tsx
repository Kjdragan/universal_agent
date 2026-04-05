import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentFlowWidget } from "./AgentFlowWidget";

type MockConversationMessage = {
  type: "message" | "tool_call" | "tool_result";
  content: string;
  timestamp: number;
};

let conversations = new Map<string, MockConversationMessage[]>();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock("@/hooks/agent-flow/use-ua-bridge", () => ({
  useUABridge: () => ({
    connectionStatus: "watching",
    pendingEvents: [],
    consumeEvents: vi.fn(),
    useMockData: false,
    archivesBySessionId: {
      session_alpha: {
        sessionId: "session_alpha",
        title: "Alpha session",
        status: "active",
        laneHint: "chat",
        normalizedEvents: [],
      },
    },
    recentSessionIds: ["session_alpha"],
    greatestHitSessionIds: [],
    mode: "recent",
    selectedSessionId: "session_alpha",
    selectedArchive: {
      sessionId: "session_alpha",
      title: "Alpha session",
      status: "active",
      laneHint: "chat",
      normalizedEvents: [],
    },
    selectedIsLive: true,
    selectedPlaybackEvents: [],
    selectedPlaybackMode: "live",
    selectionSource: "manual",
    currentReplayGeneration: 0,
    currentReplayLoopIndex: 0,
    currentSpotlightTitle: "Alpha session",
    currentSpotlightIsLive: true,
    setMode: vi.fn(),
    selectSession: vi.fn(),
    clearManualSelection: vi.fn(),
    advanceGreatestHitsLoop: vi.fn(),
    restartReplay: vi.fn(),
  }),
}));

vi.mock("@/hooks/agent-flow/use-agent-simulation", () => ({
  useAgentSimulation: () => ({
    frameRef: { current: null },
    agents: new Map(),
    toolCalls: [],
    particles: [],
    edges: [],
    discoveries: [],
    fileAttention: [],
    timelineEntries: [],
    currentTime: 12,
    isPlaying: false,
    speed: 1,
    maxTimeReached: 12,
    conversations,
    play: vi.fn(),
    pause: vi.fn(),
    restart: vi.fn(),
    setSpeed: vi.fn(),
    seekToTime: vi.fn(),
    updateAgentPosition: vi.fn(),
    loadEventPlayback: vi.fn(),
    hydrateToLatest: vi.fn(),
  }),
}));

vi.mock("@/hooks/agent-flow/use-selection-state", () => ({
  useSelectionState: () => ({
    selectedAgentId: null,
    hoveredAgentId: null,
    selectedToolCallId: null,
    selectedDiscoveryId: null,
    selectedAgentWorldPos: null,
    selectedToolData: null,
    selectedToolScreenPos: null,
    selectedDiscoveryData: null,
    selectedDiscoveryScreenPos: null,
    contextMenu: null,
    clearAllSelections: vi.fn(),
    clearAgent: vi.fn(),
    clearTool: vi.fn(),
    clearDiscovery: vi.fn(),
    setContextMenu: vi.fn(),
    handleAgentClick: vi.fn(),
    setHoveredAgentId: vi.fn(),
    handleContextMenu: vi.fn(),
    handleToolCallClick: vi.fn(),
    handleDiscoveryClick: vi.fn(),
  }),
}));

vi.mock("@/hooks/agent-flow/use-keyboard-shortcuts", () => ({
  useKeyboardShortcuts: vi.fn(),
}));

vi.mock("@/hooks/agent-flow/use-audio-effects", () => ({
  useAudioEffects: () => ({
    isMuted: true,
    seekingRef: { current: false },
    handleToggleMute: vi.fn(),
  }),
}));

vi.mock("./canvas", () => ({
  AgentCanvas: () => <div data-testid="agent-canvas" />,
}));

vi.mock("./control-bar", () => ({
  ControlBar: ({ eventCount }: { eventCount?: number }) => <div data-testid="control-bar">{eventCount ?? 0}</div>,
}));

vi.mock("./agent-detail-card", () => ({
  AgentDetailCard: () => null,
}));

vi.mock("./glass-context-menu", () => ({
  GlassContextMenu: () => null,
}));

vi.mock("./tool-detail-popup", () => ({
  ToolDetailPopup: () => null,
}));

vi.mock("./discovery-detail-popup", () => ({
  DiscoveryDetailPopup: () => null,
}));

vi.mock("./file-attention-panel", () => ({
  FileAttentionPanel: () => null,
}));

vi.mock("./timeline-panel", () => ({
  TimelinePanel: () => null,
}));

vi.mock("./chat-panel", () => ({
  AgentChatPanel: () => null,
}));

vi.mock("./session-transcript-panel", () => ({
  SessionTranscriptPanel: () => null,
}));

vi.mock("./tool-content-renderer", () => ({
  OpenFileProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("./message-feed-panel", () => ({
  MessageFeedPanel: () => null,
}));

vi.mock("./spotlight-bar", () => ({
  SpotlightBar: () => null,
}));

describe("AgentFlowWidget", () => {
  beforeEach(() => {
    conversations = new Map([
      [
        "agent_alpha",
        [{ type: "message", content: "first message", timestamp: 10 }],
      ],
    ]);
  });

  it("updates timeline event counts across rerenders without crashing", () => {
    const { rerender } = render(<AgentFlowWidget mode="compact" />);

    expect(screen.getByTestId("control-bar")).toHaveTextContent("1");

    conversations = new Map([
      [
        "agent_alpha",
        [
          { type: "message", content: "first message", timestamp: 10 },
          { type: "tool_call", content: "read file", timestamp: 20 },
        ],
      ],
    ]);

    rerender(<AgentFlowWidget mode="compact" />);

    expect(screen.getByTestId("control-bar")).toHaveTextContent("2");
  });
});
