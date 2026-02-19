"use client";

import React from "react";
import { useAgentStore } from "@/lib/store";

const LINKIFIABLE_TOKEN_REGEX =
  /(https?:\/\/[^\s<>"'`]+|www\.[^\s<>"'`]+|(?:\/|\.\.?\/)[A-Za-z0-9._~\-\/]+|[A-Za-z]:\\[^\s<>"'`]+|(?:[A-Za-z0-9._~\-]+\/)+[A-Za-z0-9._~\-]+\.[A-Za-z0-9]+)/g;

const isLikelyUrl = (value: string) =>
  /^https?:\/\//i.test(value) || /^www\./i.test(value);

const normalizeUrl = (value: string) =>
  /^www\./i.test(value) ? `https://${value}` : value;

const isLikelyPath = (value: string) => {
  if (!value) return false;
  const looksAbsoluteUnix = value.startsWith("/");
  const looksRelative = value.startsWith("./") || value.startsWith("../");
  const looksWindows = /^[A-Za-z]:\\/.test(value);
  const looksArtifacts = value.startsWith("artifacts/");
  const looksImplicitFile = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+\.[A-Za-z0-9]+$/.test(
    value,
  );
  const looksImplicitNestedFile = /^(?:[A-Za-z0-9._~-]+\/)+[A-Za-z0-9._~-]+\.[A-Za-z0-9]+$/.test(
    value,
  );
  return (
    looksAbsoluteUnix ||
    looksRelative ||
    looksWindows ||
    looksArtifacts ||
    looksImplicitFile ||
    looksImplicitNestedFile
  );
};

const splitTrailingPunctuation = (token: string): [string, string] => {
  const match = token.match(/^(.*?)([),.;!?]+)$/);
  if (!match) return [token, ""];
  return [match[1], match[2]];
};

const resolveArtifactRelativePath = (path: string): string | null => {
  const normalized = path.replace(/\\/g, "/");
  if (normalized.startsWith("artifacts/")) {
    return normalized.slice("artifacts/".length);
  }
  const marker = "/artifacts/";
  const idx = normalized.indexOf(marker);
  if (idx >= 0) {
    return normalized.slice(idx + marker.length);
  }
  return null;
};

export function PathLink({ path, className }: { path: string; className?: string }) {
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const currentSession = useAgentStore((s) => s.currentSession);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        const name = path.split(/[\\/]/).pop() || path;

        const artifactRelativePath = resolveArtifactRelativePath(path);
        if (artifactRelativePath) {
          setViewingFile({ name, path: artifactRelativePath, type: "artifact" });
          return;
        }

        let fullPath = path;
        // Resolve relative paths
        if (!path.startsWith("/") && !path.match(/^[a-zA-Z]:\\/)) {
          if (currentSession?.workspace) {
            const cleanPath = path.replace(/^\.\//, "");
            const workspace = currentSession.workspace.endsWith("/")
              ? currentSession.workspace
              : currentSession.workspace + "/";
            fullPath = workspace + cleanPath;
          }
        }

        // Strip session workspace prefix from absolute paths to get relative paths
        // for the file API. The API expects: /api/files/{session_id}/{relative_path}
        if (currentSession?.workspace && fullPath.startsWith(currentSession.workspace)) {
          const wsPrefix = currentSession.workspace.endsWith("/")
            ? currentSession.workspace
            : currentSession.workspace + "/";
          if (fullPath.startsWith(wsPrefix)) {
            fullPath = fullPath.slice(wsPrefix.length);
          }
        }

        setViewingFile({ name, path: fullPath, type: "file" });
      }}
      className={
        className ||
        "text-cyan-400 hover:underline cursor-pointer break-all font-mono bg-cyan-500/10 px-1 rounded mx-0.5 text-left"
      }
      title="Open file preview"
    >
      {path}
    </button>
  );
}

export function LinkifiedText({ text }: { text: string }) {
  const parts = text.split(LINKIFIABLE_TOKEN_REGEX);

  return (
    <>
      {parts.map((part, index) => {
        if (index % 2 === 0) return part;
        const [token, trailing] = splitTrailingPunctuation(part);
        if (!token) return part;

        if (isLikelyUrl(token)) {
          return (
            <React.Fragment key={`${token}-${index}`}>
              <a
                href={normalizeUrl(token)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyan-400 hover:underline font-medium break-all"
                onClick={(e) => e.stopPropagation()}
              >
                {token}
              </a>
              {trailing}
            </React.Fragment>
          );
        }

        if (isLikelyPath(token)) {
          return (
            <React.Fragment key={`${token}-${index}`}>
              <PathLink path={token} />
              {trailing}
            </React.Fragment>
          );
        }

        return part;
      })}
    </>
  );
}

export const linkify = {
  isLikelyUrl,
  isLikelyPath,
  normalizeUrl,
};
