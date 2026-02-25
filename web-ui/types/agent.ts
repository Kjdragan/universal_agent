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
  | "input_required"
  | "input_response"
  | "ping"
  | "cancel"
  | "cancelled"
  | "system_event"
  | "system_presence";

// =============================================================================
// WebSocket Event
// =============================================================================

export interface WebSocketEvent {
  type: EventType;
  data: EventData;
  timestamp: number;
  time_offset?: number;
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
  | InputRequiredEventData
  | SystemEventData
  | SystemPresenceData
  | Record<string, unknown>;

// =============================================================================
// Text Event
// =============================================================================

export interface TextEventData {
  text: string;
  author?: string;
  time_offset?: number;
  final?: boolean;
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
  timestamp?: number; // Added for interleaved sorting
  result?: ToolResult;
  status: "pending" | "running" | "complete" | "error";
  error?: string; // Optional top-level error
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
  event_kind?: string;
  compact_boundary?: Record<string, unknown>;
  source?: string;
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

export type MessageType = "speech" | "thought";

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: number;
  time_offset: number;
  tool_calls?: ToolCall[];
  thinking?: string;
  is_complete: boolean;
  author?: string;
  messageType?: MessageType;
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

// =============================================================================
// Input Required Types
// =============================================================================

export interface InputRequiredEventData {
  input_id: string;
  question: string;
  category: string;
  options: string[];
}

// =============================================================================
// System Events / Presence
// =============================================================================

export interface SystemEventData {
  event_id?: string;
  type?: string;
  payload?: Record<string, unknown>;
  created_at?: string;
}

export interface SystemPresenceData {
  node_id?: string;
  status?: string;
  reason?: string | null;
  metadata?: Record<string, unknown>;
  updated_at?: string;
}

// =============================================================================
// Storage UX Types
// =============================================================================



export interface StorageSessionItem {
  session_id: string;
  source_type: "web" | "hook" | "telegram" | "vp" | "other";
  status: string;
  ready: boolean;
  completed_at_epoch?: number | null;
  updated_at_epoch?: number | null;
  modified_epoch?: number | null;
  size_bytes?: number | null;
  root_path: string;
  run_log_path?: string | null;
}

export interface StorageArtifactItem {
  path: string;
  slug: string;
  title: string;
  status: string;
  video_id?: string | null;
  video_url?: string | null;
  updated_at_epoch?: number | null;
  manifest_path?: string | null;
  readme_path?: string | null;
  implementation_path?: string | null;
}

export interface StorageOverview {
  pending_ready_count: number;
  latest_sessions: {
    web: StorageSessionItem | null;
    hook: StorageSessionItem | null;
    telegram: StorageSessionItem | null;
    vp?: StorageSessionItem | null;
  };
  latest_artifact: StorageArtifactItem | null;
  workspace_root: string;
  artifacts_root: string;
}
