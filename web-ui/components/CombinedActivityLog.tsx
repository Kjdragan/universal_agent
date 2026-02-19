import React, { useEffect, useRef, useMemo } from 'react';
import { useAgentStore } from '@/lib/store';
import { cn } from '@/lib/utils';
import { ChevronRight, ChevronDown, Terminal, Play, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';
import { format } from 'date-fns';
import { LinkifiedText } from "@/components/LinkifiedText";

interface LogEntry {
    id: string;
    message: string;
    level: string;
    prefix: string;
    timestamp: number;
    event_kind?: string;
    metadata?: Record<string, unknown>;
    type: 'log';
}

interface ToolEntry {
    id: string;
    name: string;
    status: 'pending' | 'running' | 'complete' | 'error';
    input: any;
    result?: any;
    timestamp: number;
    type: 'tool';
}

type ActivityItem = LogEntry | ToolEntry;
type ExpandMode = 'collapsed' | 'open' | 'expanded';

export function CombinedActivityLog({ onCollapse }: { onCollapse?: () => void } = {}) {
    const logs = useAgentStore((state) => state.logs);
    const toolCalls = useAgentStore((state) => state.toolCalls);
    const scrollRef = useRef<HTMLDivElement>(null);
    const [expandMode, setExpandMode] = React.useState<ExpandMode>('expanded');

    // Merge and sort
    const items: ActivityItem[] = useMemo(() => {
        const logItems: LogEntry[] = logs.map(l => ({ ...l, type: 'log' as const }));
        // Provide fallback for timestamp to satisfy ToolEntry required type
        const toolItems: ToolEntry[] = toolCalls.map(t => ({
            ...t,
            type: 'tool' as const,
            timestamp: t.timestamp ?? 0
        }));

        return [...logItems, ...toolItems].sort((a, b) => a.timestamp - b.timestamp);
    }, [logs, toolCalls]);

    // Auto-scroll
    useEffect(() => {
        if (scrollRef.current) {
            const scrollElement = scrollRef.current;
            scrollElement.scrollTop = scrollElement.scrollHeight;
        }
    }, [items.length]);

    return (
        <div className="flex flex-col h-full bg-slate-950 border border-slate-800 rounded-lg overflow-hidden">
            <div className="p-3 border-b border-slate-800 bg-slate-900/60 flex justify-between items-center">
                <h3 className="font-semibold text-sm flex items-center gap-2 text-slate-200">
                    <Terminal className="w-4 h-4 text-cyan-500/70" />
                    Activity & Logs
                </h3>
                <div className="flex items-center gap-2">
                    {onCollapse && (
                        <button
                            type="button"
                            className="text-[10px] px-2 py-1 rounded border border-slate-700 bg-slate-800/60 hover:bg-slate-800 transition-colors text-slate-300"
                            title="Collapse activity panel"
                            onClick={onCollapse}
                        >
                            ◀
                        </button>
                    )}
                    <button
                        type="button"
                        className="text-[10px] px-2 py-1 rounded border border-slate-700 bg-slate-800/60 hover:bg-slate-800 transition-colors text-slate-300"
                        title="Toggle activity panel expansion"
                        onClick={() => {
                            setExpandMode((prev) => {
                                if (prev === 'expanded') return 'collapsed';
                                if (prev === 'collapsed') return 'open';
                                return 'expanded';
                            });
                        }}
                    >
                        {expandMode === 'expanded' ? 'Expanded' : expandMode === 'open' ? 'Open' : 'Collapsed'}
                    </button>
                    <span className="text-xs text-muted-foreground">{items.length} events</span>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4 scrollbar-thin" ref={scrollRef}>
                <div className="space-y-3">
                    {items.map((item) => (
                        <ActivityItemRow key={`${item.type}:${item.id}:${item.timestamp}`} item={item} expandMode={expandMode} />
                    ))}
                    {items.length === 0 && (
                        <div className="text-center text-muted-foreground py-8 text-sm">
                            No activity recorded yet.
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function ActivityItemRow({ item, expandMode }: { item: ActivityItem; expandMode: ExpandMode }) {
    if (item.type === 'tool') {
        return <ToolRow tool={item as ToolEntry} expandMode={expandMode} />;
    }
    return <LogRow log={item as LogEntry} expandMode={expandMode} />;
}

const CollapsibleData = ({ label, data, isError = false, expandMode }: { label: string, data: any, isError?: boolean, expandMode: ExpandMode }) => {
    const [expanded, setExpanded] = React.useState(false);
    const jsonString = useMemo(() => JSON.stringify(data, null, 2), [data]);
    const preview = useMemo(() => {
        if (typeof data === 'string') return data.slice(0, 60) + (data.length > 60 ? '...' : '');
        if (typeof data === 'object') return Object.keys(data).join(', ').slice(0, 60) + '...';
        return String(data);
    }, [data]);
    const effectiveExpanded = expandMode === 'collapsed' ? false : expandMode === 'expanded' ? true : expanded;

    // Calculate approximate size for the label
    const size = useMemo(() => {
        const len = jsonString.length;
        if (len < 1024) return `${len} B`;
        return `${(len / 1024).toFixed(1)} KB`;
    }, [jsonString]);

    return (
        <div className="border border-slate-800 rounded bg-slate-900/30 overflow-hidden">
            <div
                className="flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-slate-800/50 transition-colors text-xs"
                onClick={() => {
                    if (expandMode === 'expanded') return;
                    setExpanded(!effectiveExpanded);
                }}
            >
                <span className="text-slate-500">
                    {effectiveExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                </span>
                <span className="font-semibold text-slate-400">{label}</span>
                <span className="text-[10px] text-slate-500/60 font-mono">({size})</span>
                {!effectiveExpanded && <span className="ml-auto text-slate-500/50 font-mono truncate max-w-[200px]">{preview}</span>}
            </div>

            {effectiveExpanded && (
                <div className="border-t border-slate-800 bg-slate-950 p-0">
                    <pre className={cn(
                        "p-2 text-xs font-mono whitespace-pre-wrap break-words",
                        isError ? "text-red-400" : "text-slate-300"
                    )}>
                        <LinkifiedText text={String(data?.content_preview ?? jsonString)} />
                    </pre>
                </div>
            )}
        </div>
    );
};

function ToolRow({ tool, expandMode }: { tool: ToolEntry; expandMode: ExpandMode }) {
    const [isOpen, setIsOpen] = React.useState(true);
    const effectiveOpen = expandMode === 'collapsed' ? false : expandMode === 'expanded' ? true : isOpen;

    return (
        <div className="border border-slate-800 rounded-md bg-slate-900/40 text-sm overflow-hidden">
            <div
                className={cn(
                    "flex items-center gap-2 p-2 cursor-pointer hover:bg-slate-800/60 transition-colors",
                    tool.status === 'running' && "bg-blue-500/10 border-l-2 border-l-blue-500",
                    tool.status === 'complete' && "bg-green-500/10 border-l-2 border-l-green-500",
                    tool.status === 'error' && "bg-red-500/10 border-l-2 border-l-red-500"
                )}
                onClick={() => {
                    if (expandMode === 'expanded') return;
                    setIsOpen(!effectiveOpen);
                }}
            >
                <div className="text-muted-foreground">
                    {effectiveOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </div>

                <div className="flex items-center justify-center w-6 h-6 rounded-full bg-background border shrink-0">
                    {tool.status === 'running' && <Play className="w-3 h-3 text-blue-500 animate-pulse" />}
                    {tool.status === 'complete' && <CheckCircle2 className="w-3 h-3 text-green-500" />}
                    {tool.status === 'error' && <XCircle className="w-3 h-3 text-red-500" />}
                    {tool.status === 'pending' && <div className="w-2 h-2 rounded-full bg-muted-foreground" />}
                </div>

                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="font-mono font-medium text-cyan-400 truncate">{tool.name}</span>
                        <span className="text-xs text-muted-foreground ml-auto whitespace-nowrap">
                            {format(tool.timestamp, 'HH:mm:ss.SSS')}
                        </span>
                    </div>
                </div>
            </div>

            {effectiveOpen && (
                <div className="p-2 bg-muted/20 border-t space-y-2">
                    <CollapsibleData label="Input" data={tool.input} expandMode={expandMode} />
                    {tool.result && (
                        <CollapsibleData
                            label="Result"
                            data={tool.result}
                            isError={tool.status === 'error'}
                            expandMode={expandMode}
                        />
                    )}
                </div>
            )}
        </div>
    );
}

function LogRow({ log, expandMode }: { log: LogEntry; expandMode: ExpandMode }) {
    const [isOpen, setIsOpen] = React.useState(false);

    if (log.event_kind === "sdk_compact_boundary") {
        return <CompactBoundaryRow log={log} expandMode={expandMode} />;
    }
    const isError = log.level === 'ERROR' || log.level === 'CRITICAL';

    // Truncate long log messages for the header
    const headerPreview = log.message.split('\n')[0].slice(0, 100);
    const hasMore = log.message.length > 100 || log.message.includes('\n');
    const effectiveOpen = hasMore && (expandMode === 'expanded' ? true : expandMode === 'collapsed' ? false : isOpen);

    return (
        <div className={cn(
            "text-xs font-mono border-l-2 pl-2 py-1",
            isError ? "border-l-red-500" : "border-l-gray-300 dark:border-l-gray-700"
        )}>
            <div
                className={cn(
                    "flex items-start gap-2 cursor-pointer hover:opacity-80",
                    isError ? "text-red-600 dark:text-red-400" : "text-foreground"
                )}
                onClick={() => {
                    if (!hasMore || expandMode === 'expanded') return;
                    setIsOpen(!effectiveOpen);
                }}
            >
                <span className="text-muted-foreground min-w-[80px] shrink-0">
                    {format(log.timestamp, 'HH:mm:ss.SSS')}
                </span>
                <span className={cn(
                    "font-bold px-1.5 rounded text-[10px]",
                    isError ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300" : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
                )}>
                    {log.level}
                </span>
                <div className="flex-1 whitespace-pre-wrap break-words">
                    {log.prefix && <span className="text-blue-500 mr-1">{log.prefix}</span>}
                    <LinkifiedText text={headerPreview} />
                    {hasMore && !effectiveOpen && <span className="text-muted-foreground ml-1">...</span>}
                </div>
            </div>

            {effectiveOpen && hasMore && (
                <div className="mt-1 pl-[110px] text-muted-foreground whitespace-pre-wrap break-words">
                    <LinkifiedText text={log.message} />
                </div>
            )}
        </div>
    );
}

function CompactBoundaryRow({ log, expandMode }: { log: LogEntry; expandMode: ExpandMode }) {
    const [isOpen, setIsOpen] = React.useState(false);
    const metadata = (log.metadata ?? {}) as Record<string, unknown>;
    const payload = (metadata.compact_boundary as Record<string, unknown>) ?? {};
    const reason = String(payload.subtype ?? payload.reason ?? "auto_compaction");
    const before = Number(payload.tokens_before ?? NaN);
    const after = Number(payload.tokens_after ?? NaN);
    const showTokenDelta = Number.isFinite(before) && Number.isFinite(after);
    const hasPayload = Object.keys(payload).length > 0;
    const effectiveOpen = expandMode === 'collapsed' ? false : expandMode === 'expanded' ? true : isOpen;

    return (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/8 overflow-hidden">
            <div
                className="flex items-start gap-2 p-2 text-xs cursor-pointer hover:bg-amber-500/10 transition-colors"
                onClick={() => {
                    if (!hasPayload || expandMode === 'expanded') return;
                    setIsOpen(!effectiveOpen);
                }}
            >
                <AlertCircle className="w-4 h-4 text-amber-400 mt-[1px] shrink-0" />
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="font-semibold uppercase tracking-[0.12em] text-[10px] text-amber-300">
                            Context Compaction
                        </span>
                        <span className="text-[10px] text-amber-200/70 font-mono">{format(log.timestamp, 'HH:mm:ss.SSS')}</span>
                    </div>
                    <div className="mt-0.5 text-amber-100">
                        <LinkifiedText text={log.message || `SDK compact boundary received (${reason}).`} />
                    </div>
                    <div className="mt-1 text-[10px] text-amber-200/80 flex flex-wrap gap-3">
                        <span>reason: <span className="font-mono">{reason}</span></span>
                        {showTokenDelta && (
                            <span>tokens: <span className="font-mono">{Math.trunc(before)} -&gt; {Math.trunc(after)}</span></span>
                        )}
                    </div>
                </div>
                {hasPayload && (
                    <span className="text-amber-300/80">
                        {effectiveOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </span>
                )}
            </div>
            {effectiveOpen && hasPayload && (
                <div className="border-t border-amber-500/20 bg-slate-950/60 p-2">
                    <pre className="text-xs font-mono whitespace-pre-wrap break-words text-amber-100">
                        <LinkifiedText text={JSON.stringify(payload, null, 2)} />
                    </pre>
                </div>
            )}
        </div>
    );
}
