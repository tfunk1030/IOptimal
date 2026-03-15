import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { SessionStatusBadge } from "../components/SessionStatusBadge";
import { SetupDeltaTable } from "../components/SetupDeltaTable";
import { fetchResult } from "../lib/api";
import type { SessionResult } from "../types/api";

export function SessionDetailPage({ token }: { token: string | null }) {
  const { id } = useParams();
  const [result, setResult] = useState<SessionResult | null>(null);

  useEffect(() => {
    if (!id) {
      return;
    }
    void fetchResult(token, id).then(setResult);
  }, [id, token]);

  if (!result) {
    return <p>Loading session...</p>;
  }
  const step1 = (result.results?.step1_rake as Record<string, unknown> | undefined) ?? {};
  const step2 = (result.results?.step2_heave as Record<string, unknown> | undefined) ?? {};
  const current = (result.results?.current_setup as Record<string, unknown> | undefined) ?? {};

  const rows = [
    {
      parameter: "Rear RH",
      current: (current.static_rear_rh_mm as number | undefined) ?? null,
      recommended: (step1.static_rear_rh_mm as number | undefined) ?? null
    },
    {
      parameter: "Front Heave",
      current: (current.front_heave_nmm as number | undefined) ?? null,
      recommended: (step2.front_heave_nmm as number | undefined) ?? null
    }
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Session {result.id}</h2>
        <SessionStatusBadge status={result.status} />
      </div>
      <section className="rounded border border-slate-700 bg-panel p-4">
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Engineering Report</h3>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs font-mono text-slate-200">
          {result.report_text || "Report unavailable."}
        </pre>
      </section>
      <section>
        <SetupDeltaTable rows={rows} />
      </section>
      {result.sto_storage_path ? (
        <a
          className="inline-block rounded bg-cyan-600 px-4 py-2 text-sm font-semibold text-slate-900"
          href={`${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/api/setups/${result.id}`}
          target="_blank"
          rel="noreferrer"
        >
          Download .sto
        </a>
      ) : null}
    </div>
  );
}

