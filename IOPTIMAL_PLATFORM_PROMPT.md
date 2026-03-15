# iOptimal Platform — Claude Code Implementation Prompt

## What This Is

You are building the **iOptimal Platform** — a web-based team telemetry analysis and setup optimization system for iRacing GTP/Hypercar cars. The platform wraps an existing Python physics solver that reads IBT telemetry files and produces optimal setup recommendations. You are adding: a FastAPI backend, a Supabase-hosted team database, a React dashboard frontend, and a lightweight local file watcher that auto-uploads IBT files from each driver's machine.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  Driver's Windows PC                                     │
│                                                          │
│  iRacing → saves IBT → ~/Documents/iRacing/telemetry/   │
│                              │                           │
│  ┌───────────────────────────▼──────────────────────┐   │
│  │  Local Watcher (Python)                           │   │
│  │  - watchdog monitors telemetry folder             │   │
│  │  - debounce (wait for file write to complete)     │   │
│  │  - POST IBT file to backend API                   │   │
│  │  - display status / last result in system tray    │   │
│  └───────────────────────────┬──────────────────────┘   │
└──────────────────────────────┼──────────────────────────┘
                               │ HTTPS
                               ▼
┌──────────────────────────────────────────────────────────┐
│  Backend Server (FastAPI)                                 │
│                                                          │
│  /api/upload-ibt     ← receives IBT, triggers pipeline   │
│  /api/results/{id}   ← fetch analysis results            │
│  /api/team/knowledge ← query team knowledge base         │
│  /api/team/compare   ← compare drivers on same track     │
│  /api/setups/{id}    ← download .sto file                │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  Existing Solver Pipeline (imported as-is)      │     │
│  │  pipeline.produce.produce()                     │     │
│  │  learner.ingest                                 │     │
│  │  analyzer.*                                     │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  Team Knowledge Layer (new)                     │     │
│  │  - wraps learner/knowledge_store.py             │     │
│  │  - adds driver_id to all observations           │     │
│  │  - individual + team-aggregate models           │     │
│  │  - sync to/from Supabase                        │     │
│  └─────────────────────┬──────────────────────────┘     │
└────────────────────────┼────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Supabase (Hosted Postgres + Auth + Storage)             │
│                                                          │
│  Tables:                                                 │
│    drivers       — user profiles, auth, driver style     │
│    teams         — team groupings                        │
│    sessions      — one row per IBT ingestion             │
│    observations  — structured telemetry snapshots        │
│    deltas        — session-to-session diffs              │
│    models        — empirical corrections (per-driver     │
│                    and team-aggregate)                    │
│    setups        — generated .sto files + metadata       │
│                                                          │
│  Storage:                                                │
│    ibt-files/    — raw IBT uploads (optional)            │
│    sto-files/    — generated setup files                 │
└──────────────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  React Dashboard (Vite + React + Tailwind)               │
│                                                          │
│  Pages:                                                  │
│    /dashboard         — latest session, setup delta      │
│    /sessions          — session history list             │
│    /session/:id       — full analysis report             │
│    /compare           — compare drivers/sessions         │
│    /team/knowledge    — team insights & models           │
│    /settings          — car config, watcher setup        │
│    /setup/:id         — setup detail + download .sto     │
└──────────────────────────────────────────────────────────┘
```

## Critical Constraint: Treat the Solver as a Black Box

The existing Python solver codebase (`solver/`, `pipeline/`, `analyzer/`, `learner/`, `aero_model/`, `car_model/`, `track_model/`, `output/`) is **stable and must not be modified**. Import it, call it, wrap it — but do not refactor or rewrite any existing module.

The key entry points you will call:

```python
# Full pipeline: IBT → solver results
from pipeline.produce import produce
# produce() takes argparse.Namespace with: car, ibt, wing, fuel, lap, sto, json, learn, auto_learn, no_learn, etc.

# Learner ingestion: IBT → knowledge store
from learner.ingest import _run_analyzer  # returns (track, measured, setup, driver, diagnosis, corners, ibt)
from learner.knowledge_store import KnowledgeStore
from learner.observation import build_observation
from learner.delta_detector import detect_delta
from learner.empirical_models import fit_models
from learner.recall import KnowledgeRecall

# Setup writer
from output.setup_writer import write_sto

# Car model
from car_model.cars import get_car  # get_car("bmw"), get_car("ferrari"), etc.

# IBT parser
from track_model.ibt_parser import IBTFile
```

The `produce()` function currently expects an `argparse.Namespace` object. Create a thin adapter that constructs this namespace from API request parameters:

```python
import argparse

def make_produce_args(car: str, ibt_path: str, wing: float = None,
                      fuel: float = None, lap: int = None,
                      sto_path: str = None, json_path: str = None,
                      learn: bool = True, auto_learn: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        car=car, ibt=ibt_path, wing=wing, fuel=fuel, lap=lap,
        sto=sto_path, json=json_path, learn=learn, auto_learn=auto_learn,
        no_learn=not learn, min_lap_time=108.0, outlier_pct=0.115,
    )
```

## Phase 1: Backend (FastAPI)

### Directory structure

```
platform/
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, CORS, lifespan
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── upload.py         # POST /api/upload-ibt
│   │   ├── results.py        # GET /api/results/{session_id}
│   │   ├── setups.py         # GET /api/setups/{session_id}
│   │   ├── sessions.py       # GET /api/sessions (list, filter)
│   │   ├── team.py           # GET /api/team/knowledge, /api/team/compare
│   │   └── auth.py           # POST /api/auth/register, /login, /me
│   ├── services/
│   │   ├── __init__.py
│   │   ├── solver_service.py # Wraps produce() — adapter layer
│   │   ├── learner_service.py# Wraps learner ingestion + team knowledge
│   │   ├── upload_service.py # File handling, validation, storage
│   │   └── team_service.py   # Team knowledge aggregation, queries
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py        # Pydantic request/response models
│   │   └── database.py       # Supabase client setup
│   ├── workers/
│   │   ├── __init__.py
│   │   └── process_ibt.py    # Background task: run solver pipeline
│   └── config.py             # Environment variables, settings
├── watcher/
│   ├── __init__.py
│   ├── watch.py              # File watcher daemon
│   ├── uploader.py           # HTTP client to POST IBTs
│   ├── config.py             # Watcher settings (folder path, server URL, car)
│   └── tray.py               # System tray icon (pystray)
├── frontend/                 # React app (Vite)
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── lib/
│   │   │   └── supabase.ts   # Supabase JS client
│   │   └── types/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   └── tailwind.config.js
├── requirements.txt
├── .env.example
└── README.md
```

### API Endpoints

#### `POST /api/upload-ibt`
- Accepts: multipart file upload (IBT file) + JSON body with `car` (str), `wing` (float, optional), `driver_id` (str)
- Validates: file is a valid IBT (check magic bytes / header), car is supported
- Stores IBT temporarily on disk (or Supabase Storage if persisting)
- Enqueues background task to run solver
- Returns: `{ session_id: str, status: "processing" }`

#### `GET /api/results/{session_id}`
- Returns: full solver output as JSON — setup recommendations, diagnosis, driver profile, setup delta (current vs recommended), engineering report text
- Status field: `processing`, `complete`, `error`
- Include the raw solver JSON output (the same format `pipeline.produce` writes to `--json`)

#### `GET /api/setups/{session_id}`
- Returns: downloadable .sto file
- Content-Type: application/octet-stream

#### `GET /api/sessions`
- Query params: `driver_id`, `car`, `track`, `limit`, `offset`
- Returns: paginated list of sessions with summary (track, car, lap time, date, driver)

#### `GET /api/team/knowledge`
- Query params: `car`, `track`
- Returns: team-aggregate empirical models, recurring issues, lap time sensitivities, driver-by-driver comparison of key metrics
- Uses `KnowledgeRecall` to query accumulated team data

#### `GET /api/team/compare`
- Query params: `session_ids[]` (2+ session IDs to compare)
- Returns: side-by-side setup diff, driver style diff, performance diff

#### Auth: use Supabase Auth (email/password or magic link). Each request includes a Bearer token. Middleware validates against Supabase.

### Background Processing (`workers/process_ibt.py`)

When an IBT is uploaded:

```python
async def process_ibt(session_id: str, ibt_path: str, car: str,
                      wing: float, driver_id: str, db: SupabaseClient):
    """Background task: run solver pipeline on uploaded IBT."""
    try:
        # 1. Update session status
        db.table("sessions").update({"status": "processing"}).eq("id", session_id).execute()

        # 2. Run solver (synchronous — run in thread pool)
        args = make_produce_args(
            car=car, ibt_path=ibt_path, wing=wing,
            json_path=f"/tmp/results/{session_id}.json",
            sto_path=f"/tmp/results/{session_id}.sto",
            learn=True, auto_learn=True,
        )
        produce(args)

        # 3. Read solver output
        with open(f"/tmp/results/{session_id}.json") as f:
            solver_output = json.load(f)

        # 4. Run learner ingestion
        # ... call _run_analyzer, build_observation, store to Supabase

        # 5. Build team-aggregate models
        # ... fit_models() across all team observations for this car/track

        # 6. Store results in Supabase
        db.table("sessions").update({
            "status": "complete",
            "results": solver_output,
            "sto_path": f"sto-files/{session_id}.sto",
        }).eq("id", session_id).execute()

        # 7. Upload .sto to Supabase Storage
        db.storage.from_("sto-files").upload(f"{session_id}.sto", sto_bytes)

    except Exception as e:
        db.table("sessions").update({
            "status": "error", "error": str(e)
        }).eq("id", session_id).execute()
```

### Team Knowledge Layer (`services/team_service.py`)

The existing `KnowledgeStore` writes to local JSON files. For team use, you need to:

1. **Keep the local store as-is** for solver compatibility — `apply_learned_corrections()` reads from `data/learnings/` and we don't modify it.

2. **Add a Supabase sync layer** that mirrors observations, deltas, and models to the database:

```python
class TeamKnowledgeService:
    """Bridges local KnowledgeStore with team Supabase database."""

    def __init__(self, db: SupabaseClient):
        self.db = db
        self.local_store = KnowledgeStore()

    async def ingest_session(self, observation: dict, driver_id: str):
        """Store observation both locally (for solver) and in Supabase (for team)."""
        # Local store (solver needs this)
        self.local_store.store_observation(observation)

        # Supabase (team needs this)
        self.db.table("observations").insert({
            **observation,
            "driver_id": driver_id,
        }).execute()

    async def get_team_knowledge(self, car: str, track: str) -> dict:
        """Query team-wide knowledge for a car/track combo."""
        # All observations for this car/track across all team drivers
        obs = self.db.table("observations") \
            .select("*") \
            .eq("car", car) \
            .ilike("track", f"{track}%") \
            .execute()

        # Individual driver models
        individual = self.db.table("models") \
            .select("*") \
            .eq("car", car) \
            .ilike("track", f"{track}%") \
            .is_("driver_id", "not.null") \
            .execute()

        # Team aggregate model
        aggregate = self.db.table("models") \
            .select("*") \
            .eq("car", car) \
            .ilike("track", f"{track}%") \
            .is_("driver_id", "null") \
            .execute()

        return {
            "session_count": len(obs.data),
            "drivers": self._group_by_driver(obs.data),
            "individual_models": individual.data,
            "team_model": aggregate.data[0] if aggregate.data else None,
            "recurring_issues": self._find_recurring_issues(obs.data),
        }
```

**Key design decision**: when a driver runs the solver, their personal `data/learnings/` drives the corrections. But the Supabase store holds everyone's data. The team aggregate model is a separate empirical model fitted on all drivers' observations — useful for new team members or tracks where a specific driver has limited data. The solver should query team models as fallback when individual data is sparse (< 3 sessions).

## Phase 2: Supabase Schema

### Tables

```sql
-- Drivers (extends Supabase auth.users)
create table public.drivers (
  id uuid references auth.users primary key,
  display_name text not null,
  team_id uuid references public.teams,
  default_car text default 'bmw',
  created_at timestamptz default now()
);

-- Teams
create table public.teams (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  invite_code text unique not null default substr(gen_random_uuid()::text, 1, 8),
  created_by uuid references public.drivers,
  created_at timestamptz default now()
);

-- Sessions (one per IBT processed)
create table public.sessions (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid references public.drivers not null,
  team_id uuid references public.teams,
  car text not null,
  track text not null,
  track_config text,
  wing_angle float,
  best_lap_time float,
  lap_number int,
  status text default 'processing' check (status in ('processing', 'complete', 'error')),
  results jsonb,                  -- full solver output JSON
  error text,
  ibt_storage_path text,         -- path in Supabase Storage (optional)
  sto_storage_path text,         -- path in Supabase Storage
  created_at timestamptz default now()
);

-- Observations (structured telemetry snapshots — mirrors learner/observation.py output)
create table public.observations (
  id uuid primary key default gen_random_uuid(),
  session_id uuid references public.sessions not null,
  driver_id uuid references public.drivers not null,
  car text not null,
  track text not null,
  data jsonb not null,            -- full observation dict from build_observation()
  driver_style jsonb,             -- driver profile from analyze_driver()
  diagnosis jsonb,                -- diagnosis output
  created_at timestamptz default now()
);

-- Deltas (session-to-session diffs)
create table public.deltas (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid references public.drivers not null,
  car text not null,
  track text not null,
  from_session uuid references public.sessions not null,
  to_session uuid references public.sessions not null,
  data jsonb not null,            -- delta dict from detect_delta()
  causal_confidence float,        -- higher if only one solver step changed
  created_at timestamptz default now()
);

-- Empirical Models (per-driver + team aggregate)
create table public.models (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid references public.drivers,  -- null = team aggregate
  team_id uuid references public.teams,
  car text not null,
  track text not null,
  model_type text not null,       -- 'empirical', 'sensitivity', etc.
  data jsonb not null,            -- fitted model parameters
  session_count int not null,
  updated_at timestamptz default now(),
  unique(driver_id, car, track, model_type)
);

-- Row Level Security: drivers can only see their own team's data
alter table public.sessions enable row level security;
alter table public.observations enable row level security;
alter table public.deltas enable row level security;
alter table public.models enable row level security;

create policy "Team members see team sessions" on public.sessions
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );
-- Repeat similar policies for observations, deltas, models
-- Insert policies: drivers can only insert their own data
```

### Storage Buckets
- `ibt-files` — raw IBT uploads (private, team-scoped)
- `sto-files` — generated setup files (private, team-scoped)

## Phase 3: Local File Watcher

A small standalone Python app that runs on each driver's Windows PC.

### `watcher/watch.py`

```python
"""IBT file watcher — monitors iRacing telemetry folder and auto-uploads new files."""

import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

IRACING_TELEMETRY_DIR = Path.home() / "Documents" / "iRacing" / "telemetry"
DEBOUNCE_SECONDS = 5  # wait for iRacing to finish writing

class IBTHandler(FileSystemEventHandler):
    def __init__(self, uploader, car: str):
        self.uploader = uploader
        self.car = car
        self._pending = {}

    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".ibt"):
            return
        self._pending[event.src_path] = time.time()

    def on_modified(self, event):
        if event.src_path in self._pending:
            self._pending[event.src_path] = time.time()

    def check_ready(self):
        """Called periodically — upload files that haven't been modified for DEBOUNCE_SECONDS."""
        now = time.time()
        ready = [p for p, t in self._pending.items() if now - t > DEBOUNCE_SECONDS]
        for path in ready:
            del self._pending[path]
            self.uploader.upload(path, self.car)

def start_watcher(uploader, car: str, folder: Path = None):
    folder = folder or IRACING_TELEMETRY_DIR
    handler = IBTHandler(uploader, car)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
            handler.check_ready()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
```

### `watcher/uploader.py`

```python
"""HTTP client to upload IBT files to the iOptimal backend."""

import httpx
from pathlib import Path

class IBTUploader:
    def __init__(self, server_url: str, auth_token: str):
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self.client = httpx.Client(timeout=120)  # IBTs can be large

    def upload(self, ibt_path: str, car: str, wing: float = None):
        path = Path(ibt_path)
        with open(path, "rb") as f:
            response = self.client.post(
                f"{self.server_url}/api/upload-ibt",
                files={"file": (path.name, f, "application/octet-stream")},
                data={"car": car, "wing": str(wing) if wing else ""},
                headers={"Authorization": f"Bearer {self.auth_token}"},
            )
        response.raise_for_status()
        result = response.json()
        print(f"[iOptimal] Uploaded {path.name} → session {result['session_id']}")
        return result
```

### `watcher/tray.py`

Use `pystray` for a system tray icon on Windows:
- Green icon = watching, connected
- Yellow icon = processing IBT
- Red icon = error / disconnected
- Right-click menu: "Open Dashboard" (opens browser), "Settings", "Pause", "Quit"
- Tooltip shows: last processed file, result summary ("BMW @ Sebring: +2mm rear RH, soften F ARB")

### Watcher Packaging
Package with PyInstaller as a single .exe. Include a simple first-run config dialog (server URL, login, default car, telemetry folder path). Store config in `%APPDATA%/iOptimal/config.json`.

## Phase 4: React Dashboard

### Tech Stack
- Vite + React 18 + TypeScript
- Tailwind CSS for styling
- Supabase JS client for auth + real-time subscriptions
- Recharts for data visualization
- React Router for navigation

### Key Pages

#### Dashboard (`/dashboard`)
- Latest session card: track, car, lap time, processing status
- Setup delta table: parameter | current | recommended | change
- Highlight changes by magnitude (big changes = red/bold, small = green)
- Real-time: subscribe to Supabase sessions table, auto-update when processing completes

#### Session Detail (`/session/:id`)
- Full engineering report (rendered from the solver's text report)
- Driver style profile visualization
- Handling diagnosis list (prioritized)
- Corner-by-corner breakdown (table + mini charts)
- Setup comparison (current vs recommended, two columns)
- Download .sto button

#### Sessions List (`/sessions`)
- Table: date, track, car, driver, lap time, status
- Filter by: car, track, driver, date range
- Sort by: date, lap time
- Click to open session detail

#### Team Knowledge (`/team/knowledge`)
- Select car + track
- Show: total sessions ingested, number of drivers
- Empirical model visualization (corrections from physics baseline)
- Recurring issues across the team
- Driver comparison radar chart (smoothness, consistency, aggression, trail braking)
- Lap time sensitivity chart (which parameters had biggest effect)

#### Compare (`/compare`)
- Select 2+ sessions to compare
- Side-by-side setup tables
- Driver style diff
- Overlay charts (ride height distributions, shock velocity histograms)

#### Settings (`/settings`)
- Profile: display name, default car
- Team: create team, join with invite code, manage members
- Watcher: instructions for installing + configuring the local watcher
- Auth: password change, sessions

### Design Requirements
- Dark theme default (sim racers sit in dark rooms)
- Clean, data-dense layouts — this is an engineering tool, not a consumer app
- Monospace font for numerical data
- Color coding: green = improvement, red = regression, yellow = significant change
- Responsive but desktop-primary (most users will be at their racing PC)

## Phase 5: Real-Time Features

Use Supabase Realtime subscriptions to push updates to the dashboard:

```typescript
// Subscribe to new sessions for this team
const channel = supabase
  .channel('team-sessions')
  .on('postgres_changes',
    { event: 'UPDATE', schema: 'public', table: 'sessions',
      filter: `team_id=eq.${teamId}` },
    (payload) => {
      if (payload.new.status === 'complete') {
        // Show notification: "Taylor's BMW @ Sebring session processed"
        // Auto-refresh dashboard data
      }
    }
  )
  .subscribe()
```

When a teammate finishes a session and their watcher uploads the IBT, every team member's dashboard updates in real-time. This is the magic moment — you exit iRacing and your teammate's results are already on your screen.

## Environment & Dependencies

### Backend (`requirements.txt`)
```
fastapi>=0.109
uvicorn[standard]>=0.27
python-multipart>=0.0.6
supabase>=2.0
httpx>=0.26
watchdog>=3.0
pystray>=0.19
Pillow>=10.0  # required by pystray
# Existing solver deps (already in project):
numpy
scipy
openpyxl
```

### Frontend (`package.json` key deps)
```json
{
  "dependencies": {
    "react": "^18.3",
    "react-dom": "^18.3",
    "react-router-dom": "^6.22",
    "@supabase/supabase-js": "^2.42",
    "recharts": "^2.12",
    "tailwindcss": "^3.4",
    "@headlessui/react": "^2.0",
    "lucide-react": "^0.344"
  }
}
```

### Environment Variables (`.env`)
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key  # backend only
IOPTIMAL_API_PORT=8000
IOPTIMAL_SOLVER_PATH=../  # path to solver codebase root
```

## Implementation Order

1. **Supabase project setup** — create project, run schema SQL, configure auth, create storage buckets
2. **FastAPI skeleton** — main.py, config, Supabase client, health check endpoint
3. **Upload + processing pipeline** — POST /upload-ibt → background task → produce() → store results
4. **Results API** — GET endpoints for sessions, results, setups
5. **Auth** — Supabase Auth integration, JWT middleware, driver registration
6. **React app scaffold** — Vite, routing, Supabase client, auth flow
7. **Dashboard page** — latest session, setup delta display
8. **Session detail + sessions list** — full report rendering
9. **Team knowledge service** — sync observations to Supabase, aggregate models
10. **Team pages** — knowledge browser, compare view, driver radar charts
11. **Local watcher** — watch.py + uploader.py + tray.py + PyInstaller packaging
12. **Real-time** — Supabase subscriptions for live dashboard updates
13. **Team management** — create/join teams, invite codes, RLS policies

## Non-Obvious Implementation Details

### IBT files are big
IBT files from a full session can be 50-200MB. The upload endpoint must handle multipart streaming. Consider: (a) only uploading the best N laps' worth of data instead of the whole file, or (b) running the solver locally in the watcher and only uploading the structured results (much smaller). Option (b) is the smarter default — the watcher has the solver locally, processes the IBT, and uploads just the JSON results + .sto file to the backend. This avoids transferring huge files and keeps the server lightweight.

### Solver is synchronous and CPU-bound
`produce()` runs in ~2-10 seconds depending on IBT size. In FastAPI, run it in a thread pool executor (`asyncio.to_thread()` or `run_in_executor()`) to avoid blocking the event loop.

### Driver style divergence is a feature
When two drivers produce conflicting data (one shows oversteer, the other neutral at the same corner), this is a driver style difference. Tag observations with the driver's style profile. The team knowledge query should surface these divergences explicitly, not average them away. The existing `driver_style.py` already classifies style — use that classification as a partition key when aggregating.

### Car detection from IBT
The IBT session info YAML contains the car name. You can auto-detect the car from the IBT instead of requiring the user to specify it. Use `IBTFile.session_info()` to extract it. Same for track name and wing angle (via `CurrentSetup.from_ibt()`).

### Backward compatibility with CLI workflow
Keep the existing CLI commands working (`python -m pipeline.produce`, `python -m analyzer`, etc.). The platform wraps them — it doesn't replace them. A driver who doesn't want the web UI can still use the CLI directly.

### File locking warning
The existing `KnowledgeStore` uses no file locking. If the backend and a CLI user both write to `data/learnings/` simultaneously, data can corrupt. For the platform, the Supabase database is the source of truth. Local `data/learnings/` should be treated as a read cache that the solver queries. Only the backend process should write to it (syncing from Supabase on startup or before solver runs).
