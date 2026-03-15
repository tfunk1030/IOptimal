import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchResult } from "../lib/api";
import type { SessionResult } from "../types/api";

export function SetupPage({ token }: { token: string | null }) {
  const { id } = useParams();
  const [result, setResult] = useState<SessionResult | null>(null);

  useEffect(() => {
    if (!id) {
      return;
    }
    void fetchResult(token, id).then(setResult);
  }, [id, token]);

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Setup Detail</h2>
      <pre className="rounded border border-slate-700 bg-panel p-4 text-xs">
        {result ? JSON.stringify(result.results, null, 2) : "Loading setup..."}
      </pre>
      {id ? (
        <a
          className="inline-block rounded bg-cyan-600 px-4 py-2 font-semibold text-slate-900"
          href={`${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/api/setups/${id}`}
          target="_blank"
          rel="noreferrer"
        >
          Download .sto
        </a>
      ) : null}
    </div>
  );
}

