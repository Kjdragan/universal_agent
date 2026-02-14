import Link from "next/link";

export const dynamic = "force-static";

export default function FilesPage() {
  // In production, FileBrowser is usually served by the reverse proxy directly at `/files/`.
  // In local Next dev, that route would otherwise 404, so provide a friendly landing page.
  const configured = (process.env.NEXT_PUBLIC_FILEBROWSER_URL || process.env.UA_FILEBROWSER_URL || "").trim();

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100 p-6">
      <div className="mx-auto w-full max-w-2xl rounded-xl border border-slate-800 bg-slate-900/70 p-5">
        <h1 className="text-lg font-semibold tracking-tight">File Browser</h1>
        <p className="mt-2 text-sm text-slate-300">
          This route exists so the dashboard&apos;s <span className="font-mono">/files/</span> link doesn&apos;t 404 in local dev.
        </p>

        <div className="mt-4 space-y-2 text-sm text-slate-300">
          <p>
            For session work products, the built-in Files panel (right sidebar in Chat) is usually enough.
          </p>
          <p>
            If you run an external FileBrowser service, set <span className="font-mono">NEXT_PUBLIC_FILEBROWSER_URL</span> (or{" "}
            <span className="font-mono">UA_FILEBROWSER_URL</span>) and open it directly.
          </p>
          {configured && (
            <p className="text-xs text-slate-400">
              Configured URL: <span className="font-mono break-all">{configured}</span>
            </p>
          )}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Link
            href="/"
            className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800/70"
          >
            Back to Chat
          </Link>
          {configured && (
            <a
              href={configured}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md border border-cyan-700 bg-cyan-600/20 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-600/30"
            >
              Open FileBrowser
            </a>
          )}
        </div>
      </div>
    </main>
  );
}

