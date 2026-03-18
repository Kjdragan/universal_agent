"use client";

import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { FolderOpen, ArrowLeft } from "lucide-react";
import { ExplorerPanel } from "@/components/storage/ExplorerPanel";

type ExplorerScope = "workspaces" | "artifacts" | "vps";

function normalizeScope(value: string | null): ExplorerScope {
  if (value === "artifacts") return "artifacts";
  if (value === "vps") return "vps";
  return "workspaces";
}

export function StoragePageClient() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const explorerScope = normalizeScope(searchParams.get("scope"));
  const explorerPath = searchParams.get("path") || "";
  const previewPath = searchParams.get("preview") || "";

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground p-4 md:p-6">
      <div className="mx-auto flex h-full w-full max-w-7xl flex-col gap-4">
        {/* ── Top Bar ── */}
        <header className="shrink-0 flex items-center gap-3 rounded-xl border border-border/50 bg-background/60 px-5 py-3 backdrop-blur-sm">
          <FolderOpen className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold tracking-tight text-foreground">
            File Explorer
          </h1>
          <div className="ml-auto flex items-center gap-2">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 rounded-md border border-border/50 bg-card/40 px-3 py-1.5 text-xs font-medium text-foreground/80 transition-colors hover:bg-card/50/50 hover:text-white"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to App
            </Link>
          </div>
        </header>

        {/* ── Explorer ── */}
        <section className="min-h-0 flex-1">
          <ExplorerPanel
            initialScope={explorerScope}
            initialPath={explorerPath}
            initialPreviewPath={previewPath}
          />
        </section>
      </div>
    </main>
  );
}
