import { useState } from "react";
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer } from "recharts";

import { fetchTeamKnowledge } from "../lib/api";
import type { TeamKnowledge } from "../types/api";

const styleData = [
  { metric: "Smoothness", value: 72 },
  { metric: "Consistency", value: 65 },
  { metric: "Aggression", value: 83 },
  { metric: "Trail Braking", value: 58 }
];

export function TeamKnowledgePage({ token }: { token: string | null }) {
  const [car, setCar] = useState("bmw");
  const [track, setTrack] = useState("Sebring");
  const [data, setData] = useState<TeamKnowledge | null>(null);

  async function load() {
    setData(await fetchTeamKnowledge(token, car, track));
  }

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold">Team Knowledge</h2>
      <div className="flex flex-wrap gap-3">
        <input
          className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
          value={car}
          onChange={(e) => setCar(e.target.value)}
          placeholder="car"
        />
        <input
          className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
          value={track}
          onChange={(e) => setTrack(e.target.value)}
          placeholder="track"
        />
        <button className="rounded bg-cyan-600 px-4 py-2 font-semibold text-slate-900" onClick={() => void load()}>
          Load
        </button>
      </div>
      {data ? (
        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded border border-slate-700 bg-panel p-4">
            <p>Sessions ingested: {data.session_count}</p>
            <p>Driver sessions: {data.driver_session_count ?? "-"}</p>
            <p>Fallback mode: {data.fallback_mode}</p>
            <h3 className="mt-3 font-semibold">Recurring Issues</h3>
            <ul className="list-disc pl-5 text-sm text-slate-300">
              {data.recurring_issues.map((item, idx) => (
                <li key={idx}>{JSON.stringify(item)}</li>
              ))}
            </ul>
          </section>
          <section className="rounded border border-slate-700 bg-panel p-4">
            <h3 className="mb-2 font-semibold">Driver Style Radar</h3>
            <div className="h-64">
              <ResponsiveContainer>
                <RadarChart data={styleData}>
                  <PolarGrid />
                  <PolarAngleAxis dataKey="metric" />
                  <Radar dataKey="value" stroke="#67e8f9" fill="#67e8f9" fillOpacity={0.4} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </section>
        </div>
      ) : (
        <p className="text-slate-300">Load a car/track to view team knowledge.</p>
      )}
    </div>
  );
}

