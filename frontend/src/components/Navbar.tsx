import { Link } from 'react-router-dom';
import { LayoutDashboard, List, Activity, Users, GitCompare } from 'lucide-react';

export default function Navbar() {
  return (
    <nav className="bg-gray-800 border-b border-gray-700 p-4">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <div className="flex items-center gap-6">
          <Link to="/" className="text-xl font-bold text-white flex items-center gap-2">
            <Activity className="w-6 h-6 text-blue-500" />
            iOptimal
          </Link>
          <div className="flex items-center gap-4 text-sm font-medium text-gray-400">
            <Link to="/dashboard" className="hover:text-white flex items-center gap-1">
              <LayoutDashboard className="w-4 h-4" /> Dashboard
            </Link>
            <Link to="/sessions" className="hover:text-white flex items-center gap-1">
              <List className="w-4 h-4" /> Sessions
            </Link>
            <Link to="/team/knowledge" className="hover:text-white flex items-center gap-1">
              <Users className="w-4 h-4" /> Team Knowledge
            </Link>
            <Link to="/compare" className="hover:text-white flex items-center gap-1">
              <GitCompare className="w-4 h-4" /> Compare
            </Link>
          </div>
        </div>
      </div>
    </nav>
  );
}
