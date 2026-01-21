/**
 * ApprovalModal - Modal for URW phase approvals
 *
 * Used when the agent requires user approval for:
 * - Planning phase (before execution begins)
 * - Replan requests (when orchestrator needs to change direction)
 * - Other critical checkpoints
 */

import React, { useState } from "react";
import { useAgentStore } from "@/lib/store";
import { getWebSocket } from "@/lib/websocket";

// Types
interface ApprovalRequest {
  phase_id: string;
  phase_name: string;
  phase_description: string;
  tasks: TaskInfo[];
  requires_followup: boolean;
}

interface TaskInfo {
  id: string;
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}

interface ApprovalModalProps {
  request: ApprovalRequest | null;
  onApprove: (followupInput?: string) => void;
  onReject: () => void;
}

// =============================================================================
// Task List Component
// =============================================================================

function TaskList({ tasks }: { tasks: TaskInfo[] }) {
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-2">
        Tasks ({tasks.length})
      </h4>
      <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin">
        {tasks.map((task, index) => (
          <div
            key={task.id}
            className="flex items-start gap-2 p-2 rounded bg-background/50 border border-border/50"
          >
            <span className="text-xs text-muted-foreground mt-0.5">
              {index + 1}.
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium">{task.content}</div>
              {task.activeForm && (
                <div className="text-xs text-muted-foreground mt-0.5">
                  Status: {task.activeForm}
                </div>
              )}
            </div>
            {task.status === "completed" && (
              <span className="text-green-500">✓</span>
            )}
            {task.status === "in_progress" && (
              <span className="text-primary animate-pulse">●</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Approval Modal Component
// =============================================================================

export function ApprovalModal({ request, onApprove, onReject }: ApprovalModalProps) {
  const [followupInput, setFollowupInput] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const connectionStatus = useAgentStore((s) => s.connectionStatus);

  if (!request) return null;

  const handleApprove = () => {
    if (connectionStatus !== "connected") return;

    setIsSubmitting(true);
    try {
      onApprove(followupInput || undefined);
      setFollowupInput("");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReject = () => {
    if (connectionStatus !== "connected") return;

    setIsSubmitting(true);
    try {
      onReject();
      setFollowupInput("");
    } finally {
      setIsSubmitting(false);
    }
  };

  const canSubmit = connectionStatus === "connected" && !isSubmitting;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="glass-strong rounded-xl max-w-2xl w-full max-h-[80vh] overflow-hidden flex flex-col shadow-2xl border border-primary/30">
        {/* Header */}
        <div className="p-6 border-b border-border/50 bg-gradient-to-r from-primary/10 to-secondary/10">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary/20 flex items-center justify-center">
              <span className="text-xl">📋</span>
            </div>
            <div>
              <h2 className="text-lg font-bold gradient-text">Approval Required</h2>
              <p className="text-sm text-muted-foreground">{request.phase_name}</p>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 scrollbar-thin">
          {/* Description */}
          {request.phase_description && (
            <div className="mb-6">
              <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                Description
              </h4>
              <p className="text-sm leading-relaxed">{request.phase_description}</p>
            </div>
          )}

          {/* Tasks */}
          {request.tasks.length > 0 && <TaskList tasks={request.tasks} />}

          {/* Followup Input (if required) */}
          {request.requires_followup && (
            <div className="mt-6 p-4 rounded-lg bg-accent/10 border border-accent/30">
              <label className="block text-sm font-semibold mb-2">
                Additional Input Required
              </label>
              <textarea
                value={followupInput}
                onChange={(e) => setFollowupInput(e.target.value)}
                placeholder="Provide additional context or instructions for the agent..."
                rows={4}
                className="w-full bg-background/50 border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary resize-none"
                disabled={!canSubmit}
              />
            </div>
          )}
        </div>

        {/* Footer - Actions */}
        <div className="p-6 border-t border-border/50 bg-background/30 flex justify-end gap-3">
          <button
            onClick={handleReject}
            disabled={!canSubmit}
            className="px-4 py-2 rounded-lg border border-border/50 bg-background/50 hover:bg-destructive/10 hover:border-destructive/50 hover:text-destructive transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
          >
            Reject
          </button>
          <button
            onClick={handleApprove}
            disabled={!canSubmit}
            className="px-6 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium shadow-lg shadow-primary/20"
          >
            {isSubmitting ? "Processing..." : "Approve & Continue"}
          </button>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Hook for Approval Management
// =============================================================================

export function useApprovalModal() {
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequest | null>(null);
  const ws = getWebSocket();

  // Set up listener for approval requests
  React.useEffect(() => {
    const unsubscribe = ws.on("approval", (event) => {
      const data = event.data as Record<string, unknown>;
      setPendingApproval({
        phase_id: (data.phase_id as string) ?? "",
        phase_name: (data.phase_name as string) ?? "Approval Required",
        phase_description: (data.phase_description as string) ?? "",
        tasks: (data.tasks as TaskInfo[]) ?? [],
        requires_followup: (data.requires_followup as boolean) ?? false,
      });
    });

    return unsubscribe;
  }, [ws]);

  const handleApprove = (followupInput?: string) => {
    if (!pendingApproval) return;

    // Send approval via WebSocket
    ws.sendApproval({
      phase_id: pendingApproval.phase_id,
      approved: true,
      followup_input: followupInput,
    });

    setPendingApproval(null);
  };

  const handleReject = () => {
    if (!pendingApproval) return;

    // Send rejection via WebSocket
    ws.sendApproval({
      phase_id: pendingApproval.phase_id,
      approved: false,
    });

    setPendingApproval(null);
  };

  return {
    pendingApproval,
    handleApprove,
    handleReject,
  };
}
