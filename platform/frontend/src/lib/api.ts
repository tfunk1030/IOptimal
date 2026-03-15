import type { SessionResult, SessionSummary, TeamKnowledge } from "../types/api";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

function authHeader(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

export async function fetchSessions(
  token: string | null,
  query?: Record<string, string | number | undefined>,
): Promise<{ total: number; items: SessionSummary[] }> {
  const params = new URLSearchParams();
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const response = await fetch(`${API_BASE}/api/sessions?${params.toString()}`, {
    headers: {
      "Content-Type": "application/json",
      ...authHeader(token)
    }
  });
  return handle(response);
}

export async function fetchResult(token: string | null, sessionId: string): Promise<SessionResult> {
  const response = await fetch(`${API_BASE}/api/results/${sessionId}`, {
    headers: {
      "Content-Type": "application/json",
      ...authHeader(token)
    }
  });
  return handle(response);
}

export async function fetchTeamKnowledge(
  token: string | null,
  car: string,
  track: string,
): Promise<TeamKnowledge> {
  const params = new URLSearchParams({ car, track });
  const response = await fetch(`${API_BASE}/api/team/knowledge?${params.toString()}`, {
    headers: {
      "Content-Type": "application/json",
      ...authHeader(token)
    }
  });
  return handle(response);
}
