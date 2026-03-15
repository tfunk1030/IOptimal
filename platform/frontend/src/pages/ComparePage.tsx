import { useEffect, useState } from "react";

import { fetchSessions } from "../lib/api";
import type { SessionSummary } from "../types/api";

type ComparePayload = {
  sessions: Array<Record<string, unknown>>;
  setup_diff: Array<Record<string, unknown>>;
  style_diff: Array<Record<string, unknown>>;
  performance_diff: Array<Record<string, unknown>>;
};

export function ComparePage({ token }: { token: string | null }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<ComparePayload | null>(null);

  useEffect(() => {
    void fetchSessions(token, { limit: 30, offset: 0 }).then((res) => setSessions(res.items));
  }, [token]);

  async function compare() {
    if (selected.length < 2) {
      return;
    }
    const params = selected.map((s) => `session_ids=${encodeURIComponent(s)}`).join("&");
    const response = await fetch(`${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/api/team/compare?${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    setResult(await response.json());
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Compare Sessions</h2>
      <div className="grid gap-2 md:grid-cols-2">
        {sessions.map((session) => (
          <label key={session.id} className="flex items-center gap-2 rounded border border-slate-700 px-3 py-2">
            <input
              type="checkbox"
              checked={selected.includes(session.id)}
              onChange={(event) => {
                if (event.target.checked) {
                  setSelected((current) => [...current, session.id]);
                } else {
                  setSelected((current) => current.filter((id) => id !== session.id));
                }
              }}
            />
            <span className="text-sm">
              {session.car} @ {session.track} ({session.id.slice(0, 8)})
            </span>
          </label>
        ))}
      </div>
      <button className="rounded bg-cyan-600 px-4 py-2 font-semibold text-slate-900" onClick={() => void compare()}>
        Compare Selected
      </button>
      <pre className="max-h-96 overflow-auto rounded border border-slate-700 bg-panel p-3 text-xs">
        {result ? JSON.stringify(result, null, 2) : "No comparison yet."}
      </pre>
    </div>
  );
}

