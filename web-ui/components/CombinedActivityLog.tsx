import React, { useEffect, useRef, useMemo } from 'react';
import { useAgentStore } from '@/lib/store';
import { cn } from '@/lib/utils';
import { ChevronRight, ChevronDown, Terminal, Play, CheckCircle2, XCircle, AlertCircle, Info } from 'lucide-react';
import { format } from 'date-fns';

interface LogEntry {
    id: string;
    message: string;
    level: string;
    prefix: string;
    timestamp: number;
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

export function CombinedActivityLog() {
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
        <div className="flex flex-col h-full bg-background border rounded-lg overflow-hidden">
            <div className="p-3 border-b bg-muted/30 flex justify-between items-center">
                <h3 className="font-semibold text-sm flex items-center gap-2">
                    <Terminal className="w-4 h-4" />
                    Activity & Logs
                </h3>
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        className="text-[10px] px-2 py-1 rounded border bg-background/60 hover:bg-background transition-colors"
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
                        <ActivityItemRow key={item.id} item={item} expandMode={expandMode} />
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
        <div className="border rounded bg-background/50 overflow-hidden">
            <div
                className="flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-muted/50 transition-colors text-xs"
                onClick={() => {
                    if (expandMode === 'expanded') return;
                    setExpanded(!effectiveExpanded);
                }}
            >
                <span className="text-muted-foreground">
                    {effectiveExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                </span>
                <span className="font-semibold text-muted-foreground">{label}</span>
                <span className="text-[10px] text-muted-foreground/60 font-mono">({size})</span>
                {!effectiveExpanded && <span className="ml-auto text-muted-foreground/50 font-mono truncate max-w-[200px]">{preview}</span>}
            </div>

            {effectiveExpanded && (
                <div className="border-t bg-background p-0">
                    <pre className={cn(
                        "p-2 text-xs font-mono whitespace-pre-wrap break-words",
                        isError ? "text-red-600 dark:text-red-400" : "text-foreground"
                    )}>
                        {data?.content_preview || jsonString}
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
        <div className="border rounded-md bg-card/50 text-sm overflow-hidden">
            <div
                className={cn(
                    "flex items-center gap-2 p-2 cursor-pointer hover:bg-accent/50 transition-colors",
                    tool.status === 'running' && "bg-blue-500/5 border-l-2 border-l-blue-500",
                    tool.status === 'complete' && "bg-green-500/5 border-l-2 border-l-green-500",
                    tool.status === 'error' && "bg-red-500/5 border-l-2 border-l-red-500"
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
                        <span className="font-mono font-medium text-primary truncate">{tool.name}</span>
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
                    {headerPreview}
                    {hasMore && !effectiveOpen && <span className="text-muted-foreground ml-1">...</span>}
                </div>
            </div>

            {effectiveOpen && hasMore && (
                <div className="mt-1 pl-[110px] text-muted-foreground whitespace-pre-wrap break-words">
                    {log.message}
                </div>
            )}
        </div>
    );
}
