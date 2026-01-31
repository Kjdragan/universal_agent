/**
 * InputModal - Generic modal for user input requests (Harness interview, etc.)
 */

import React, { useState } from "react";
import { useAgentStore } from "@/lib/store";
import { getWebSocket } from "@/lib/websocket";

interface InputRequest {
    input_id: string;
    question: string;
    category: string;
    options: string[];
}

interface InputModalProps {
    request: InputRequest | null;
    onSubmit: (response: string) => void;
    onCancel: () => void;
}

export function InputModal({ request, onSubmit, onCancel }: InputModalProps) {
    const [response, setResponse] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);
    const connectionStatus = useAgentStore((s) => s.connectionStatus);

    if (!request) return null;

    const handleSubmit = (val?: string) => {
        const finalVal = val !== undefined ? val : response;
        if (!finalVal.trim() && !request.options.length) return;
        if (connectionStatus !== "connected" && connectionStatus !== "processing") return;

        setIsSubmitting(true);
        try {
            onSubmit(finalVal);
            setResponse("");
        } finally {
            setIsSubmitting(false);
        }
    };

    const handleOptionClick = (opt: string) => {
        handleSubmit(opt);
    };

    const canSubmit = (connectionStatus === "connected" || connectionStatus === "processing") && !isSubmitting;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
            <div className="glass-strong rounded-xl max-w-lg w-full shadow-2xl border border-primary/30 overflow-hidden flex flex-col">
                {/* Header */}
                <div className="p-4 border-b border-border/50 bg-gradient-to-r from-primary/10 to-secondary/10 flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-lg">
                        ❓
                    </div>
                    <div>
                        <h2 className="text-sm font-bold gradient-text">Input Required</h2>
                        <p className="text-xs text-muted-foreground uppercase tracking-wider">{request.category}</p>
                    </div>
                </div>

                {/* Content */}
                <div className="p-6 space-y-4">
                    <p className="text-sm font-medium leading-relaxed text-foreground">
                        {request.question}
                    </p>

                    {/* Options (if any) */}
                    {request.options && request.options.length > 0 && (
                        <div className="grid grid-cols-1 gap-2">
                            {request.options.map((opt, idx) => (
                                <button
                                    key={idx}
                                    onClick={() => handleOptionClick(opt)}
                                    disabled={!canSubmit}
                                    className="text-left px-4 py-3 rounded-lg bg-background/50 border border-border/50 hover:border-primary/50 hover:bg-primary/5 transition-all text-sm font-medium disabled:opacity-50"
                                >
                                    {opt}
                                </button>
                            ))}
                        </div>
                    )}

                    {/* Text Input */}
                    <div className="relative">
                        <textarea
                            autoFocus
                            value={response}
                            onChange={(e) => setResponse(e.target.value)}
                            placeholder="Type your answer here..."
                            rows={3}
                            className="w-full bg-background/50 border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary resize-none"
                            disabled={!canSubmit}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                                    handleSubmit();
                                }
                            }}
                        />
                        <div className="mt-1 text-[10px] text-muted-foreground text-right">
                            Press Ctrl+Enter to submit
                        </div>
                    </div>
                </div>

                {/* Footer */}
                <div className="p-4 border-t border-border/50 bg-background/30 flex justify-end gap-3">
                    <button
                        onClick={onCancel}
                        disabled={!canSubmit}
                        className="px-4 py-2 rounded-lg text-xs font-medium hover:bg-destructive/10 hover:text-destructive transition-all disabled:opacity-50"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => handleSubmit()}
                        disabled={!canSubmit || (!response.trim() && !request.options.length)}
                        className="px-6 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground text-xs font-bold transition-all shadow-lg shadow-primary/20 disabled:opacity-50"
                    >
                        {isSubmitting ? "Sending..." : "Submit Answer"}
                    </button>
                </div>
            </div>
        </div>
    );
}

export function useInputModal() {
    const [pendingInput, setPendingInput] = useState<InputRequest | null>(null);
    const ws = getWebSocket();

    React.useEffect(() => {
        const unsubscribe = ws.on("input_required", (event) => {
            const data = event.data as Record<string, unknown>;
            setPendingInput({
                input_id: (data.input_id as string) ?? "default",
                question: (data.question as string) ?? "",
                category: (data.category as string) ?? "general",
                options: (data.options as string[]) ?? [],
            });
        });

        return unsubscribe;
    }, [ws]);

    const handleSubmit = (response: string) => {
        if (!pendingInput) return;
        ws.sendInputResponse(pendingInput.input_id, response);
        setPendingInput(null);
    };

    const handleCancel = () => {
        setPendingInput(null);
    };

    return {
        pendingInput,
        handleSubmit,
        handleCancel,
    };
}
