import { useState } from "react";
import type { FormEvent } from "react";

export function LoginPage({ onLogin }: { onLogin: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const apiBase = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    const response = await fetch(`${apiBase}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });
    if (!response.ok) {
      setError(await response.text());
      return;
    }
    const payload = await response.json();
    onLogin(payload.access_token);
  }

  return (
    <div className="mx-auto mt-20 max-w-md rounded border border-slate-700 bg-panel p-6">
      <h2 className="mb-4 text-lg font-semibold">Sign In</h2>
      <form className="space-y-3" onSubmit={(e) => void submit(e)}>
        <input
          className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2"
          placeholder="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button className="w-full rounded bg-cyan-600 px-4 py-2 font-semibold text-slate-900" type="submit">
          Login
        </button>
      </form>
      {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
    </div>
  );
}
