"use client";

import { useCallback, useState } from "react";

export type FileType =
    | "markdown"
    | "json"
    | "html"
    | "code"
    | "log"
    | "image"
    | "text";

const CODE_EXTENSIONS = new Set([
    "py", "ts", "tsx", "js", "jsx", "sh", "bash", "zsh",
    "yaml", "yml", "toml", "css", "scss", "sql", "rs",
    "go", "java", "c", "cpp", "h", "rb", "php", "swift",
    "kt", "r", "lua", "pl", "ex", "exs", "zig", "vim",
    "dockerfile", "makefile", "cmake", "xml",
]);

const IMAGE_EXTENSIONS = new Set([
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif", "ico",
]);

function getExtension(path: string): string {
    const basename = path.split("/").pop() || "";
    const dotIndex = basename.lastIndexOf(".");
    return dotIndex >= 0 ? basename.slice(dotIndex + 1).toLowerCase() : "";
}

export function detectFileType(path: string): FileType {
    const ext = getExtension(path);
    if (!ext) return "text";
    if (ext === "md" || ext === "markdown" || ext === "mdx") return "markdown";
    if (ext === "json" || ext === "jsonl") return "json";
    if (ext === "html" || ext === "htm") return "html";
    if (ext === "log" || ext === "txt" || ext === "out") return "log";
    if (IMAGE_EXTENSIONS.has(ext)) return "image";
    if (CODE_EXTENSIONS.has(ext)) return "code";
    // Dotfiles and config files
    if (basename(path).startsWith(".") || ext === "env" || ext === "cfg" || ext === "ini" || ext === "conf") return "code";
    return "text";
}

function basename(path: string): string {
    return path.split("/").pop() || path;
}

export function getLanguageHint(path: string): string | undefined {
    const ext = getExtension(path);
    const map: Record<string, string> = {
        py: "python", ts: "typescript", tsx: "typescript", js: "javascript",
        jsx: "javascript", sh: "bash", bash: "bash", zsh: "bash",
        yaml: "yaml", yml: "yaml", toml: "toml", css: "css", scss: "scss",
        sql: "sql", rs: "rust", go: "go", java: "java", c: "c", cpp: "cpp",
        h: "c", rb: "ruby", php: "php", swift: "swift", kt: "kotlin",
        r: "r", lua: "lua", xml: "xml", json: "json", md: "markdown",
        html: "html", dockerfile: "dockerfile",
    };
    return map[ext];
}

export interface FilePreviewState {
    title: string;
    content: string;
    fileType: FileType;
    isLoading: boolean;
    imageUrl: string;
    error: string;
}

const INITIAL_STATE: FilePreviewState = {
    title: "",
    content: "",
    fileType: "text",
    isLoading: false,
    imageUrl: "",
    error: "",
};

export function useFilePreview(scope: string) {
    const [state, setState] = useState<FilePreviewState>(INITIAL_STATE);

    const previewFile = useCallback(async (filePath: string) => {
        const normalizedPath = String(filePath || "").trim();
        if (!normalizedPath) return;

        const fileType = detectFileType(normalizedPath);
        setState({
            title: normalizedPath,
            content: "",
            fileType,
            isLoading: true,
            imageUrl: "",
            error: "",
        });

        if (fileType === "image") {
            setState((prev) => ({
                ...prev,
                isLoading: false,
                imageUrl: `/api/vps/file?scope=${scope}&path=${encodeURIComponent(normalizedPath)}`,
            }));
            return;
        }

        try {
            const res = await fetch(
                `/api/vps/file?scope=${scope}&path=${encodeURIComponent(normalizedPath)}`,
            );
            const text = await res.text();
            if (!res.ok) {
                throw new Error(text || `Failed (${res.status})`);
            }
            setState((prev) => ({
                ...prev,
                content: text,
                isLoading: false,
            }));
        } catch (err: any) {
            setState((prev) => ({
                ...prev,
                isLoading: false,
                error: err?.message || "Failed to load file",
            }));
        }
    }, [scope]);

    const clearPreview = useCallback(() => {
        setState(INITIAL_STATE);
    }, []);

    return { ...state, previewFile, clearPreview };
}
