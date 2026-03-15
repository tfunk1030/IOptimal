import { useCallback, useEffect, useMemo, useState } from "react";

import { SessionStatusBadge } from "../components/SessionStatusBadge";
import { SetupDeltaTable } from "../components/SetupDeltaTable";
import { useSessionRealtime } from "../hooks/useSessionRealtime";
import { fetchResult, fetchSessions } from "../lib/api";
import type { SessionResult, SessionSummary } from "../types/api";

function deltaRows(result: SessionResult | null) {
  const step1 = (result?.results?.step1_rake as Record<string, unknown> | undefined) ?? {};
  const step2 = (result?.results?.step2_heave as Record<string, unknown> | undefined) ?? {};
  const current = (result?.results?.current_setup as Record<string, unknown> | undefined) ?? {};
  return [
    {
      parameter: "Rear RH",
      current: (current.static_rear_rh_mm as number | undefined) ?? null,
      recommended: (step1.static_rear_rh_mm as number | undefined) ?? null
    },
    {
      parameter: "Front Heave",
      current: (current.front_heave_nmm as number | undefined) ?? null,
      recommended: (step2.front_heave_nmm as number | undefined) ?? null
    },
    {
      parameter: "Rear Third",
      current: (current.rear_third_nmm as number | undefined) ?? null,
      recommended: (step2.rear_third_nmm as number | undefined) ?? null
    }
  ];
}

export function DashboardPage({
  token,
  teamId
}: {
  token: string | null;
  teamId?: string | null;
}) {
  const [latest, setLatest] = useState<SessionSummary | null>(null);
  const [result, setResult] = useState<SessionResult | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const sessions = await fetchSessions(token, { limit: 1, offset: 0 });
    const next = sessions.items[0] ?? null;
    setLatest(next);
    if (next) {
      setResult(await fetchResult(token, next.id));
    } else {
      setResult(null);
    }
    setLoading(false);
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useSessionRealtime({
    teamId,
    onSessionUpdate: () => {
      void load();
    }
  });

  const rows = useMemo(() => deltaRows(result), [result]);

  if (loading) {
    return <p>Loading dashboard…</p>;
  }

  return (
    <div className="space-y-6">
      <section className="rounded border border-slate-700 bg-panel p-4">
        <h2 className="text-lg font-semibold">Latest Session</h2>
        {!latest ? (
          <p className="text-slate-300">No sessions yet.</p>
        ) : (
          <div className="mt-2 grid gap-2 text-sm">
            <p>
              <span className="text-slate-400">Car:</span> {latest.car}
            </p>
            <p>
              <span className="text-slate-400">Track:</span> {latest.track}
            </p>
            <p>
              <span className="text-slate-400">Lap:</span> {latest.best_lap_time ?? "n/a"}
            </p>
            <SessionStatusBadge status={latest.status} />
          </div>
        )}
      </section>
      <section className="space-y-2">
        <h3 className="text-md font-semibold">Setup Delta</h3>
        <SetupDeltaTable rows={rows} />
      </section>
    </div>
  );
}

