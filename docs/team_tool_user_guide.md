# IOptimal Team Tool — User Guide

## What Is This?

IOptimal is a private team telemetry tool for iRacing. It runs on your PC, automatically captures your telemetry after every session, analyzes it, and syncs the data to your team's shared database. The more your team drives, the smarter the system gets at building setups for every car and track.

---

## Installation

1. **Download** the IOptimal installer from your team admin
2. **Run** `IOptimal.exe` — it installs to your system tray
3. **First Run Setup:**
   - The app auto-detects your iRacing telemetry directory (`Documents/iRacing/Telemetry/`)
   - Enter the **invite code** your team admin gave you
   - Enter your **iRacing display name**
   - Click "Join Team" — you'll get an API key automatically
   - Click "Save Settings"

---

## Daily Use

**You don't need to do anything.** The app runs silently in your system tray.

When you finish an iRacing session, the app automatically:
1. Detects the new `.ibt` telemetry file
2. Analyzes the session (car, track, lap times, setup, handling diagnosis)
3. Stores the observation locally
4. Syncs to the team server in the background
5. Shows a notification: "Session ingested: BMW @ Sebring, 1:58.3"

If you're offline, sessions queue locally and sync automatically when you reconnect.

---

## Using the Dashboard

Open **http://localhost:8000** in your browser (or click "Open Dashboard" in the tray menu).

### Pages

| Page | What It Shows |
|------|---------------|
| **New Run** | Upload an IBT manually, run the physics solver, get a .sto setup file |
| **Sessions** | Your personal session history |
| **Knowledge** | Your local knowledge base |
| **Team** | Team activity feed, member stats, who's driving what |
| **Team > Setups** | Shared setups from teammates — browse by car/track, download .sto files |
| **Team > Leaderboard** | Fastest laps per car/track across the team |
| **Team > Cars** | All cars the team drives, with auto-learning progress bars |
| **Team > Knowledge** | Team-aggregated physics models (improves as more people drive) |
| **Settings** | Team connection, telemetry directory, sync preferences |

---

## Getting a Setup

### Option A: Generate one from your telemetry
1. Go to **New Run** and upload your latest IBT file
2. Select your car and wing angle
3. Click **Run** — the solver produces a physics-optimized setup
4. Download the `.sto` file
5. Put it in `Documents/iRacing/setups/<car>/`
6. Load it in iRacing's garage

### Option B: Download a teammate's setup
1. Go to **Team > Setups**
2. Filter by car and track
3. Find a setup with a good lap time and positive ratings
4. Click **Download** to get the `.sto` file
5. Load it in iRacing's garage

---

## Sharing a Setup

After a good session:
1. Go to **Sessions** and find your session
2. Click **Share Setup**
3. Add notes (e.g., "great in slow corners, loose under braking")
4. Your teammates can now find and download it from Team > Setups

---

## Tray Menu

Right-click the IOptimal icon in your system tray:

| Menu Item | What It Does |
|-----------|--------------|
| **Open Dashboard** | Opens the web UI in your browser |
| **Watcher > Pause** | Temporarily stop monitoring for new IBTs |
| **Watcher > Bulk Import** | Import all your existing telemetry files |
| **Sync Now** | Force an immediate push/pull with the team server |
| **Settings** | Open the settings page |
| **Quit** | Stop all services and exit |

---

## How Auto-Learning Works

Every session your team drives teaches the system more about each car:

| Sessions | Support Tier | What the System Knows |
|----------|-------------|----------------------|
| 0 | Unsupported | Car name only — metadata stored |
| 5+ | Exploratory | Basic aero compression, rough baselines |
| 15+ | Partial | Ride height models, spring corrections, damper baselines |
| 30+ | Calibrated | Full physics model, validated setup generation |

You can see each car's current tier on the **Team > Cars** page. The more teammates drive a car, the faster it learns.

---

## Supported Car Classes

| Class | Status |
|-------|--------|
| GTP/Hypercar (BMW, Ferrari, Cadillac, Porsche, Acura) | Calibrated/Partial — full physics solver |
| GT3 | Auto-learn from team data |
| LMP2 | Auto-learn from team data |
| LMP3 | Auto-learn from team data |
| Porsche Cup | Auto-learn from team data |

For classes beyond GTP, the system starts with basic metadata and progressively learns physics models as your team accumulates sessions.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Not connected to team" | Check Settings > Team Connection > verify server URL and API key |
| Sessions not auto-detecting | Check Settings > Telemetry > verify directory path matches your iRacing install |
| Unknown car (metadata only) | Your car class hasn't been fully onboarded yet — ask your team admin. The system will still store your telemetry and learn from it. |
| Sync offline | Check internet connection. The app will auto-retry and catch up when reconnected. No data is lost. |
| Port 8000 in use | Change the port in Settings or pass `--port 8001` when starting |
| Dashboard won't load | Make sure the app is running (check system tray). Try restarting IOptimal. |
| Setup file doesn't load in iRacing | Make sure the .sto file is in the correct car's setup folder: `Documents/iRacing/setups/<car name>/` |

---

## FAQ

**Q: Do I need to do anything special after each session?**
No. The app monitors your telemetry folder automatically. Just drive.

**Q: Does it affect my iRacing performance?**
No. The app only reads telemetry files after your session ends. It does not interact with iRacing while you're driving.

**Q: What if I drive a car the team hasn't onboarded yet?**
The system will still detect and store your session metadata (car, track, lap times). Once an admin provides the car's aero maps and specs, all stored sessions retroactively contribute to learning.

**Q: Can I use it offline?**
Yes. All analysis runs locally on your PC. Sessions queue for sync and upload automatically when you reconnect.

**Q: How do I import my old telemetry files?**
Right-click the tray icon > Watcher > Bulk Import. This scans your entire telemetry directory and ingests all existing `.ibt` files.
