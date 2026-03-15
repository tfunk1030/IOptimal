import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link } from 'react-router-dom';

export default function SessionsList() {
  const [sessions, setSessions] = useState<any[]>([]);

  useEffect(() => {
    fetchSessions();

    const channel = supabase
      .channel('public:sessions-list')
      .on('postgres_changes', 
        { event: '*', schema: 'public', table: 'sessions' }, 
        () => {
          fetchSessions();
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  async function fetchSessions() {
    const { data } = await supabase
      .from('sessions')
      .select('*')
      .order('created_at', { ascending: false });
    if (data) setSessions(data);
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Sessions</h1>
      <div className="bg-gray-800 rounded-lg overflow-hidden border border-gray-700">
        <table className="w-full text-left">
          <thead className="bg-gray-900/50 border-b border-gray-700">
            <tr>
              <th className="p-4 font-medium text-gray-400">Date</th>
              <th className="p-4 font-medium text-gray-400">Track</th>
              <th className="p-4 font-medium text-gray-400">Car</th>
              <th className="p-4 font-medium text-gray-400">Status</th>
              <th className="p-4 font-medium text-gray-400">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {sessions.map((s) => (
              <tr key={s.id} className="hover:bg-gray-700/50">
                <td className="p-4">{new Date(s.created_at).toLocaleDateString()}</td>
                <td className="p-4 font-medium">{s.track}</td>
                <td className="p-4 text-gray-300">{s.car}</td>
                <td className="p-4">
                  <span className={`inline-flex items-center px-2 py-1 rounded text-xs font-medium ${s.status === 'complete' ? 'bg-green-900/50 text-green-400' : 'bg-yellow-900/50 text-yellow-400'}`}>
                    {s.status}
                  </span>
                </td>
                <td className="p-4">
                  <Link to={`/session/${s.id}`} className="text-blue-400 hover:text-blue-300 font-medium">View</Link>
                </td>
              </tr>
            ))}
            {sessions.length === 0 && (
              <tr>
                <td colSpan={5} className="p-8 text-center text-gray-500">No sessions found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
