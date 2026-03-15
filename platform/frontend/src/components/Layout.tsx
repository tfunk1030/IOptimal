import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

const links = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/sessions", label: "Sessions" },
  { to: "/compare", label: "Compare" },
  { to: "/team/knowledge", label: "Team Knowledge" },
  { to: "/settings", label: "Settings" }
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 text-slate-100">
      <header className="border-b border-slate-700 bg-black/30 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <Link to="/dashboard" className="font-mono text-lg tracking-wide text-cyan-300">
            iOptimal
          </Link>
          <nav className="flex gap-4 text-sm">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  isActive ? "text-cyan-300" : "text-slate-300 hover:text-white"
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
    </div>
  );
}
