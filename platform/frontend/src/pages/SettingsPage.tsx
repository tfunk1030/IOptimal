export function SettingsPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">Settings</h2>
      <section className="rounded border border-slate-700 bg-panel p-4">
        <h3 className="font-semibold">Profile</h3>
        <p className="text-sm text-slate-300">Display name, default car, and account details are managed here.</p>
      </section>
      <section className="rounded border border-slate-700 bg-panel p-4">
        <h3 className="font-semibold">Team</h3>
        <p className="text-sm text-slate-300">
          Create a team, join with invite code, and manage members in this section (backend routes wired for team-scoped data).
        </p>
      </section>
      <section className="rounded border border-slate-700 bg-panel p-4">
        <h3 className="font-semibold">Watcher Setup</h3>
        <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300">
          <li>Install watcher executable from platform release artifacts.</li>
          <li>Set server URL, account login, and default car.</li>
          <li>Point telemetry folder to Documents/iRacing/telemetry.</li>
          <li>Leave watcher running while you race.</li>
        </ol>
      </section>
    </div>
  );
}

