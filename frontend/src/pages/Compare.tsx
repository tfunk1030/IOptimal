import { useState } from 'react';

export default function Compare() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Compare Sessions</h1>
      <p className="text-gray-400">Select two sessions to compare setups and telemetry.</p>
      
      <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
        <h2 className="text-xl font-semibold mb-4">Select Sessions</h2>
        <div className="flex gap-4">
          <select className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white flex-1">
            <option>Session A...</option>
          </select>
          <div className="flex items-center text-gray-500">vs</div>
          <select className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white flex-1">
            <option>Session B...</option>
          </select>
          <button className="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-white font-medium">Compare</button>
        </div>
      </div>
      
      <div className="bg-gray-800 p-6 rounded-lg border border-gray-700 min-h-[400px] flex items-center justify-center text-gray-500">
        Select sessions above to view comparison.
      </div>
    </div>
  );
}
