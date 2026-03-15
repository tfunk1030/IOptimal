import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { supabase } from '../lib/supabase';

export default function SessionDetail() {
  const { id } = useParams();
  const [session, setSession] = useState<any>(null);

  useEffect(() => {
    if (id) fetchSession(id);
  }, [id]);

  async function fetchSession(sessionId: string) {
    const { data } = await supabase
      .from('sessions')
      .select('*')
      .eq('id', sessionId)
      .single();
    if (data) setSession(data);
  }

  if (!session) return <div className="p-4">Loading session...</div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center border-b border-gray-700 pb-4">
        <div>
          <h1 className="text-2xl font-bold">{session.track}</h1>
          <p className="text-gray-400">{session.car} • {new Date(session.created_at).toLocaleString()}</p>
        </div>
        <button className="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-white font-medium">
          Download .sto
        </button>
      </div>

      <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
        <h2 className="text-xl font-semibold mb-4">Engineering Report</h2>
        <pre className="whitespace-pre-wrap font-mono text-sm text-gray-300 bg-gray-900 p-4 rounded overflow-auto">
          {session.results?.report || "No report generated."}
        </pre>
      </div>
    </div>
  );
}
