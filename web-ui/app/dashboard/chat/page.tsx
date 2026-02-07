"use client";

import Link from "next/link";

export default function DashboardChatPage() {
  return (
    <div className="h-full min-h-[80vh] space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Chat</h1>
          <p className="text-sm text-slate-400">Embedded live console.</p>
        </div>
        <Link
          href="/"
          className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
        >
          Open Full Chat
        </Link>
      </div>
      <div className="h-[calc(100vh-12rem)] overflow-hidden rounded-xl border border-slate-800">
        <iframe title="Universal Agent Chat" src="/" className="h-full w-full border-0" />
      </div>
    </div>
  );
}
