"use client";

import { useMemo, useRef, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/core";
// Register commonly-needed languages to keep bundle small
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import javascript from "highlight.js/lib/languages/javascript";
import bash from "highlight.js/lib/languages/bash";
import json_lang from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import sql from "highlight.js/lib/languages/sql";
import markdown_lang from "highlight.js/lib/languages/markdown";
import "highlight.js/styles/github-dark-dimmed.css";

import { FileType, getLanguageHint } from "./useFilePreview";
import {
    FileText,
    FileCode,
    FileJson,
    Image as ImageIcon,
    Terminal,
    Globe,
    Copy,
    Check,
} from "lucide-react";

// Register languages
hljs.registerLanguage("python", python);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("json", json_lang);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("css", css);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("markdown", markdown_lang);

function formatBytes(bytes?: number | null): string {
    if (!bytes || bytes <= 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fileTypeLabel(fileType: FileType): string {
    const labels: Record<FileType, string> = {
        markdown: "Markdown",
        json: "JSON",
        html: "HTML",
        code: "Code",
        log: "Log",
        image: "Image",
        text: "Text",
    };
    return labels[fileType] || "Text";
}

function FileTypeIcon({ fileType }: { fileType: FileType }) {
    const iconClass = "h-4 w-4";
    switch (fileType) {
        case "markdown": return <FileText className={iconClass} />;
        case "json": return <FileJson className={iconClass} />;
        case "html": return <Globe className={iconClass} />;
        case "code": return <FileCode className={iconClass} />;
        case "log": return <Terminal className={iconClass} />;
        case "image": return <ImageIcon className={iconClass} />;
        default: return <FileText className={iconClass} />;
    }
}

// ── Syntax Highlighted Code Block ──────────────────────────────────────────

function HighlightedCode({ code, language }: { code: string; language?: string }) {
    const codeRef = useRef<HTMLElement>(null);

    useEffect(() => {
        if (codeRef.current) {
            // Reset previous highlighting
            codeRef.current.removeAttribute("data-highlighted");
            try {
                if (language && hljs.getLanguage(language)) {
                    const result = hljs.highlight(code, { language });
                    codeRef.current.innerHTML = result.value;
                } else {
                    const result = hljs.highlightAuto(code);
                    codeRef.current.innerHTML = result.value;
                }
            } catch {
                codeRef.current.textContent = code;
            }
        }
    }, [code, language]);

    return (
        <pre className="h-full overflow-auto rounded-lg border border-slate-700/50 bg-[#22272e] p-4 text-[13px] leading-6">
            <code ref={codeRef} className="font-mono">{code}</code>
        </pre>
    );
}

// ── JSON Pretty Viewer ────────────────────────────────────────────────────

function JsonViewer({ content }: { content: string }) {
    const formatted = useMemo(() => {
        try {
            const parsed = JSON.parse(content);
            return JSON.stringify(parsed, null, 2);
        } catch {
            return content;
        }
    }, [content]);

    return <HighlightedCode code={formatted} language="json" />;
}

// ── Log/Terminal Viewer ───────────────────────────────────────────────────

function LogViewer({ content }: { content: string }) {
    const lines = useMemo(() => content.split("\n"), [content]);

    return (
        <div className="h-full overflow-auto rounded-lg border border-slate-700/50 bg-[#0d1117] p-4">
            <table className="w-full border-collapse font-mono text-[12px] leading-5">
                <tbody>
                    {lines.map((line, i) => (
                        <tr key={i} className="hover:bg-slate-800/40">
                            <td className="select-none pr-4 text-right text-slate-600 align-top w-[1%] whitespace-nowrap">
                                {i + 1}
                            </td>
                            <td className="whitespace-pre-wrap break-all text-slate-300">
                                {line}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// ── HTML Iframe Preview ───────────────────────────────────────────────────

function HtmlPreview({ content }: { content: string }) {
    const srcDoc = useMemo(() => {
        // Wrap in a basic HTML skeleton if it looks like a fragment
        if (!content.toLowerCase().includes("<!doctype") && !content.toLowerCase().includes("<html")) {
            return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{font-family:system-ui,-apple-system,sans-serif;padding:16px;background:#0f172a;color:#e2e8f0;}</style></head><body>${content}</body></html>`;
        }
        return content;
    }, [content]);

    return (
        <div className="h-full overflow-hidden rounded-lg border border-slate-700/50">
            <iframe
                srcDoc={srcDoc}
                sandbox="allow-same-origin"
                className="h-full w-full bg-white"
                title="HTML Preview"
            />
        </div>
    );
}

// ── Main FilePreview Component ────────────────────────────────────────────

type FilePreviewProps = {
    title: string;
    content: string;
    fileType: FileType;
    isLoading: boolean;
    imageUrl: string;
    error: string;
    filePath?: string;
};

export function FilePreview({
    title,
    content,
    fileType,
    isLoading,
    imageUrl,
    error,
    filePath,
}: FilePreviewProps) {
    const [copied, setCopied] = useState(false);
    const language = filePath ? getLanguageHint(filePath) : undefined;
    const fileName = title ? title.split("/").pop() || title : "";
    const contentSize = content ? new Blob([content]).size : 0;

    const handleCopy = async () => {
        if (!content) return;
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch { /* ignore */ }
    };

    // ── Empty state ──
    if (!title && !isLoading) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-500">
                <FileText className="h-12 w-12 opacity-30" />
                <p className="text-sm">Select a file to preview</p>
            </div>
        );
    }

    return (
        <div className="flex h-full flex-col">
            {/* ── Header ── */}
            {title && (
                <div className="mb-3 flex items-center gap-2 rounded-lg border border-slate-700/50 bg-slate-800/40 px-3 py-2">
                    <FileTypeIcon fileType={fileType} />
                    <span className="flex-1 truncate font-mono text-sm text-slate-200" title={title}>
                        {fileName}
                    </span>
                    <span className="rounded-md border border-slate-600/40 bg-slate-700/30 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
                        {fileTypeLabel(fileType)}
                    </span>
                    {contentSize > 0 && (
                        <span className="text-[11px] text-slate-500">{formatBytes(contentSize)}</span>
                    )}
                    {content && fileType !== "image" && (
                        <button
                            type="button"
                            onClick={handleCopy}
                            className="inline-flex items-center gap-1 rounded-md border border-slate-600/40 bg-slate-700/30 px-2 py-1 text-[10px] text-slate-400 transition-colors hover:bg-slate-600/40 hover:text-slate-200"
                            title="Copy to clipboard"
                        >
                            {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                            {copied ? "Copied" : "Copy"}
                        </button>
                    )}
                </div>
            )}

            {/* ── Content ── */}
            <div className="min-h-0 flex-1">
                {isLoading ? (
                    <div className="flex h-full items-center justify-center">
                        <div className="flex items-center gap-3 text-slate-400">
                            <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan-500/30 border-t-cyan-500" />
                            <span className="text-sm">Loading file...</span>
                        </div>
                    </div>
                ) : error ? (
                    <div className="flex h-full items-center justify-center">
                        <div className="rounded-lg border border-red-700/50 bg-red-600/10 px-4 py-3 text-sm text-red-300">
                            {error}
                        </div>
                    </div>
                ) : imageUrl ? (
                    <div className="flex h-full items-center justify-center overflow-auto rounded-lg border border-slate-700/50 bg-slate-950/80 p-4">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={imageUrl}
                            alt={fileName || "preview image"}
                            className="max-h-full max-w-full object-contain"
                        />
                    </div>
                ) : fileType === "markdown" && content ? (
                    <div className="h-full overflow-auto rounded-lg border border-slate-700/50 bg-slate-950/80 p-4 text-[13px] leading-7 text-slate-200">
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            className="prose prose-sm max-w-none prose-invert prose-headings:text-cyan-300 prose-a:text-cyan-400 prose-code:text-emerald-300 prose-pre:bg-[#22272e] prose-pre:border prose-pre:border-slate-700/50"
                        >
                            {content}
                        </ReactMarkdown>
                    </div>
                ) : fileType === "json" && content ? (
                    <JsonViewer content={content} />
                ) : fileType === "html" && content ? (
                    <HtmlPreview content={content} />
                ) : fileType === "code" && content ? (
                    <HighlightedCode code={content} language={language} />
                ) : fileType === "log" && content ? (
                    <LogViewer content={content} />
                ) : content ? (
                    <pre className="h-full overflow-auto rounded-lg border border-slate-700/50 bg-slate-950/80 p-4 text-[13px] leading-6 text-slate-300 font-mono">
                        {content}
                    </pre>
                ) : (
                    <div className="flex h-full items-center justify-center text-sm text-slate-500">
                        File is empty
                    </div>
                )}
            </div>
        </div>
    );
}
