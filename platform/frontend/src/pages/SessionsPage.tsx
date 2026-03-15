import { Link } from "react-router-dom";
import { useEffect, useState } from "react";

import { SessionStatusBadge } from "../components/SessionStatusBadge";
import { fetchSessions } from "../lib/api";
import type { SessionSummary } from "../types/api";

export function SessionsPage({ token }: { token: string | null }) {
  const [rows, setRows] = useState<SessionSummary[]>([]);

  useEffect(() => {
    void fetchSessions(token, { limit: 100, offset: 0 }).then((res) => setRows(res.items));
  }, [token]);

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Sessions</h2>
      <div className="overflow-x-auto rounded border border-slate-700">
        <table className="w-full text-sm">
          <thead className="bg-slate-900/60 text-slate-300">
            <tr>
              <th className="px-3 py-2 text-left">Date</th>
              <th className="px-3 py-2 text-left">Track</th>
              <th className="px-3 py-2 text-left">Car</th>
              <th className="px-3 py-2 text-left">Lap Time</th>
              <th className="px-3 py-2 text-left">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-slate-800">
                <td className="px-3 py-2">
                  <Link to={`/session/${row.id}`}>{row.created_at?.slice(0, 19).replace("T", " ") ?? "-"}</Link>
                </td>
                <td className="px-3 py-2">{row.track}</td>
                <td className="px-3 py-2">{row.car}</td>
                <td className="px-3 py-2 font-mono">{row.best_lap_time ?? "-"}</td>
                <td className="px-3 py-2">
                  <SessionStatusBadge status={row.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

