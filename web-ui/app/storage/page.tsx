import { Suspense } from "react";
import { StoragePageClient } from "@/components/storage/StoragePageClient";

function StorageLoading() {
  return (
    <main className="min-h-screen bg-background text-foreground p-4 md:p-6">
      <div className="mx-auto w-full max-w-7xl rounded-xl border border-border bg-background/70 p-4 text-sm text-foreground/80">
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
