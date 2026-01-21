/**
 * TypeScript types for Universal Agent events and data structures.
 */

// =============================================================================
// Event Types
// =============================================================================

export type EventType =
  | "text"
  | "tool_call"
  | "tool_result"
  | "thinking"
  | "status"
  | "auth_required"
  | "error"
  | "session_info"
  | "iteration_end"
  | "work_product"
  | "connected"
  | "query_complete"
  | "pong"
  | "query"
  | "approval"
  | "ping";

// =============================================================================
// WebSocket Event
// =============================================================================

export interface WebSocketEvent {
  type: EventType;
  data: EventData;
  timestamp: number;
}

export type EventData =
  | TextEventData
  | ToolCallEventData
  | ToolResultEventData
  | ThinkingEventData
  | StatusEventData
  | AuthRequiredEventData
  | ErrorEventData
  | SessionInfoEventData
  | IterationEndEventData
  | WorkProductEventData
  | ConnectedEventData
  | QueryCompleteEventData
  | Record<string, unknown>;

// =============================================================================
// Text Event
// =============================================================================

export interface TextEventData {
  text: string;
}

// =============================================================================
// Tool Call Event
// =============================================================================

export interface ToolCallEventData {
  name: string;
  id: string;
  input: Record<string, unknown>;
  time_offset: number;
}

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  time_offset: number;
  result?: ToolResult;
  status: "pending" | "running" | "complete" | "error";
}

// =============================================================================
// Tool Result Event
// =============================================================================

export interface ToolResultEventData {
  tool_use_id: string;
  is_error: boolean;
  content_preview: string;
  content_size: number;
}

export interface ToolResult {
  tool_use_id: string;
  is_error: boolean;
  content_preview: string;
  content_size: number;
  content?: string;
}

// =============================================================================
// Thinking Event
// =============================================================================

export interface ThinkingEventData {
  thinking: string;
}

// =============================================================================
// Status Event
// =============================================================================

export interface StatusEventData {
  status: string;
  iteration?: number;
  tokens?: number;
  threshold?: number;
  [key: string]: unknown;
}

// =============================================================================
// Auth Required Event
// =============================================================================

export interface AuthRequiredEventData {
  auth_link: string;
}

// =============================================================================
// Error Event
// =============================================================================

export interface ErrorEventData {
  message: string;
  details?: Record<string, unknown>;
}

// =============================================================================
// Session Info Event
// =============================================================================

export interface SessionInfoEventData {
  session: SessionInfo;
}

export interface ConnectedEventData {
  message: string;
  session: SessionInfo;
}

export interface SessionInfo {
  session_id: string;
  workspace: string;
  user_id: string;
  session_url?: string;
  logfire_enabled?: boolean;
}

// =============================================================================
// Iteration End Event
// =============================================================================

export interface IterationEndEventData {
  iteration: number;
  tool_calls: number;
  duration_seconds: number;
  token_usage: TokenUsage;
}

// =============================================================================
// Work Product Event
// =============================================================================

export interface WorkProductEventData {
  content_type: string;
  content: string;
  filename: string;
  path: string;
}

export interface WorkProduct {
  id: string;
  content_type: string;
  content: string;
  filename: string;
  path: string;
  timestamp: number;
}

// =============================================================================
// Query Complete Event
// =============================================================================

export interface QueryCompleteEventData {
  session_id: string;
}

// =============================================================================
// Token Usage
// =============================================================================

export interface TokenUsage {
  input: number;
  output: number;
  total: number;
}

// =============================================================================
// Message Types
// =============================================================================

export type MessageRole = "user" | "assistant" | "system";

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: number;
  tool_calls?: ToolCall[];
  thinking?: string;
  is_complete: boolean;
}

// =============================================================================
// Session Types
// =============================================================================

export interface Session {
  session_id: string;
  timestamp: number;
  workspace_path: string;
  status: "incomplete" | "complete" | "error";
  files?: SessionFiles;
}

export interface SessionFiles {
  work_products: FileInfo[];
  search_results: FileInfo[];
  workbench_activity: FileInfo[];
  other: FileInfo[];
}

export interface FileInfo {
  name: string;
  path: string;
  size: number;
}

// =============================================================================
// Connection Status
// =============================================================================

export type ConnectionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "processing"
  | "error";

// =============================================================================
// UI State
// =============================================================================

export interface ViewMode {
  main: "chat" | "monitor" | "split";
  showWorkProducts: boolean;
  showActivity: boolean;
}

// =============================================================================
// File Browser Types
// =============================================================================

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  modified?: number;
  children?: FileNode[];
}

// =============================================================================
// Approval Types (URW)
// =============================================================================

export interface ApprovalRequired {
  phase_id: string;
  phase_name: string;
  phase_description: string;
  tasks: TaskInfo[];
  requires_followup: boolean;
}

export interface TaskInfo {
  id: string;
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}

export interface ApprovalResponse {
  phase_id: string;
  approved: boolean;
  followup_input?: string;
}
