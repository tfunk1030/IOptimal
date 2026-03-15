export function SessionStatusBadge({ status }: { status: string }) {
  if (status === "complete") {
    return <span className="rounded bg-emerald-600/20 px-2 py-1 text-xs text-emerald-300">Complete</span>;
  }
  if (status === "error") {
    return <span className="rounded bg-red-600/20 px-2 py-1 text-xs text-red-300">Error</span>;
  }
  return <span className="rounded bg-amber-600/20 px-2 py-1 text-xs text-amber-300">Processing</span>;
}

