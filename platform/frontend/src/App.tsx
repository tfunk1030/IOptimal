import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { ComparePage } from "./pages/ComparePage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { SessionDetailPage } from "./pages/SessionDetailPage";
import { SessionsPage } from "./pages/SessionsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";
import { TeamKnowledgePage } from "./pages/TeamKnowledgePage";

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("ioptimal-token"));
  const [teamId, setTeamId] = useState<string | null>(null);
  const apiBase = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

  useEffect(() => {
    if (!token) {
      setTeamId(null);
      return;
    }
    localStorage.setItem("ioptimal-token", token);
    void fetch(`${apiBase}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => {
        setTeamId(payload?.team_id ?? null);
      });
  }, [apiBase, token]);

  if (!token) {
    return <LoginPage onLogin={setToken} />;
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage token={token} teamId={teamId} />} />
        <Route path="/sessions" element={<SessionsPage token={token} />} />
        <Route path="/session/:id" element={<SessionDetailPage token={token} />} />
        <Route path="/setup/:id" element={<SetupPage token={token} />} />
        <Route path="/compare" element={<ComparePage token={token} />} />
        <Route path="/team/knowledge" element={<TeamKnowledgePage token={token} />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </Layout>
  );
}

