type Entry = {
  parameter: string;
  current: number | string | null;
  recommended: number | string | null;
};

export function SetupDeltaTable({ rows }: { rows: Entry[] }) {
  return (
    <div className="overflow-x-auto rounded border border-slate-700">
      <table className="w-full text-left text-sm font-mono">
        <thead className="bg-slate-900/60">
          <tr>
            <th className="px-3 py-2">Parameter</th>
            <th className="px-3 py-2">Current</th>
            <th className="px-3 py-2">Recommended</th>
            <th className="px-3 py-2">Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const currentNum = typeof row.current === "number" ? row.current : null;
            const recommendedNum = typeof row.recommended === "number" ? row.recommended : null;
            const delta = currentNum !== null && recommendedNum !== null ? recommendedNum - currentNum : null;
            const className =
              delta === null ? "text-slate-300" : Math.abs(delta) > 3 ? "text-red-300" : "text-emerald-300";
            return (
              <tr key={row.parameter} className="border-t border-slate-800">
                <td className="px-3 py-2">{row.parameter}</td>
                <td className="px-3 py-2">{String(row.current ?? "-")}</td>
                <td className="px-3 py-2">{String(row.recommended ?? "-")}</td>
                <td className={`px-3 py-2 ${className}`}>{delta === null ? "-" : delta.toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

