import { Suspense } from "react";
import { StoragePageClient } from "@/components/storage/StoragePageClient";

function StorageLoading() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-6">
      <div className="mx-auto w-full max-w-7xl rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-sm text-slate-300">
        Loading storage...
      </div>
    </main>
  );
}

export default function StoragePage() {
  return (
    <Suspense fallback={<StorageLoading />}>
      <StoragePageClient />
    </Suspense>
  );
}
