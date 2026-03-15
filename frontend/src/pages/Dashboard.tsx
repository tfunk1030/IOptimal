import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link } from 'react-router-dom';

export default function Dashboard() {
  const [latestSession, setLatestSession] = useState<any>(null);

  useEffect(() => {
    fetchLatestSession();

    // Subscribe to new sessions
    const channel = supabase
      .channel('public:sessions')
      .on('postgres_changes', 
        { event: '*', schema: 'public', table: 'sessions' }, 
        (payload) => {
          console.log('Session change received!', payload);
          // If a new session is added or updated to complete, fetch again
          fetchLatestSession();
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  async function fetchLatestSession() {
    const { data, error } = await supabase
      .from('sessions')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(1)
      .single();

    if (data) setLatestSession(data);
  }

  if (!latestSession) return <div className="p-4">Loading dashboard...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      
      <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
        <h2 className="text-xl font-semibold mb-4">Latest Session</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-gray-900 p-4 rounded">
            <div className="text-sm text-gray-400">Track</div>
            <div className="font-medium">{latestSession.track}</div>
          </div>
          <div className="bg-gray-900 p-4 rounded">
            <div className="text-sm text-gray-400">Car</div>
            <div className="font-medium">{latestSession.car}</div>
          </div>
          <div className="bg-gray-900 p-4 rounded">
            <div className="text-sm text-gray-400">Status</div>
            <div className={`font-medium ${latestSession.status === 'complete' ? 'text-green-400' : 'text-yellow-400'}`}>
              {latestSession.status}
            </div>
          </div>
          <div className="bg-gray-900 p-4 rounded">
            <div className="text-sm text-gray-400">Date</div>
            <div className="font-medium">{new Date(latestSession.created_at).toLocaleDateString()}</div>
          </div>
        </div>

        <Link to={`/session/${latestSession.id}`} className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded font-medium">
          View Full Report
        </Link>
      </div>
    </div>
  );
}
