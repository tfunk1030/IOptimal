import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';

export default function TeamKnowledge() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Team Knowledge</h1>
      <p className="text-gray-400">Team aggregate models and recurring issues.</p>
      
      <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
        <h2 className="text-xl font-semibold mb-4">Select Car & Track</h2>
        <div className="flex gap-4">
          <select className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white">
            <option>BMW M Hybrid V8</option>
            <option>Porsche 963</option>
            <option>Cadillac V-Series.R</option>
            <option>Acura ARX-06</option>
            <option>Ferrari 499P</option>
          </select>
          <select className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white">
            <option>Sebring International</option>
            <option>Le Mans</option>
            <option>Daytona</option>
          </select>
          <button className="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-white font-medium">Load Knowledge</button>
        </div>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
          <h2 className="text-xl font-semibold mb-4">Empirical Model</h2>
          <div className="text-gray-400 text-sm">Aggregated from all team sessions.</div>
          {/* Placeholder for chart */}
          <div className="h-64 flex items-center justify-center border border-dashed border-gray-600 mt-4 rounded text-gray-500">
            Model Visualization
          </div>
        </div>
        
        <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
          <h2 className="text-xl font-semibold mb-4">Recurring Issues</h2>
          <ul className="space-y-2 mt-4">
            <li className="flex justify-between p-3 bg-gray-900 rounded">
              <span>T1 (Sunset Bend)</span>
              <span className="text-red-400">High Oversteer</span>
            </li>
            <li className="flex justify-between p-3 bg-gray-900 rounded">
              <span>T17 (Ulmann Straight)</span>
              <span className="text-yellow-400">Braking Instability</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
