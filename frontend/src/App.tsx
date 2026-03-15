import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import SessionDetail from './pages/SessionDetail';
import SessionsList from './pages/SessionsList';
import TeamKnowledge from './pages/TeamKnowledge';
import Compare from './pages/Compare';
import Navbar from './components/Navbar';

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col">
        <Navbar />
        <main className="flex-1 p-6 max-w-7xl mx-auto w-full">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/sessions" element={<SessionsList />} />
            <Route path="/session/:id" element={<SessionDetail />} />
            <Route path="/team/knowledge" element={<TeamKnowledge />} />
            <Route path="/compare" element={<Compare />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
