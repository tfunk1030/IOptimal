# Plan: Team Telemetry Tool (Garage61/VRS-style for your team)

## Context

**Problem:** Your racing team (50-100 drivers) across multiple classes (GT3, LMP2, LMP3, GTP, Porsche Cup) needs a private Garage61/VRS-like tool. Each member installs a desktop app that automatically captures their iRacing telemetry, syncs to a shared cloud database, and leverages the collective knowledge to produce better setups for every car and track combination.

**What exists today:** IOptimal is a single-user local physics-based setup solver (184 Python files, ~71K LOC). It has:
- Learner system with 100+ field observations, deltas, empirical models (JSON-based)
- FastAPI webapp on localhost:8000, SQLite for run metadata
- 6-step physics solver with 40+ sub-solvers
- .sto file generation for iRacing setup files
- **Only GTP/Hypercar class supported** (BMW calibrated, Ferrari partial, Cadillac/Porsche/Acura exploratory)
- No auth, no file watching, no multi-user, no iRacing API

**Scale challenge:** 50-100 drivers × multiple classes = potentially 20+ different cars, dozens of tracks, and hundreds of telemetry sessions per week. The system must auto-learn car physics from accumulated team data.

**Decisions made:**
- **Server:** Cloud-managed (GCP Cloud Run + Cloud SQL)
- **Auth:** Invite code system
- **Scope:** Full app with UI, watcher, sync, team dashboard, desktop packaging

**What we're building:**
1. Auto-detect and ingest IBT files as they appear (any car, any class)
2. Sync observations to cloud database — team members collectively teach the system new cars
3. Auto-learn car physics models from accumulated telemetry (aero compression, m_eff, motion ratios, ARB stiffness)
4. Team dashboard with activity feed, shared setups, leaderboard, and per-class/division views
5. Package as a downloadable desktop app with system tray

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│  Team Member's PC (Desktop App)             │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  IBT     │→ │ Analyzer │→ │  Local   │  │
│  │ Watcher  │  │ Pipeline │  │ SQLite   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│                      │              │        │
│              ┌───────┴───────┐      │        │
│              │ Observation   │      │        │
│              │ (100+ fields) │      │        │
│              └───────┬───────┘      │        │
│                      ↓              ↓        │
│              ┌──────────────────────┐        │
│              │   Sync Client        │        │
│              │   (background)       │        │
│              └──────────┬───────────┘        │
│                         │                    │
│  ┌──────────────────────┴──────────────┐     │
│  │  Local Web UI (FastAPI on :8000)    │     │
│  │  - Dashboard, runs, knowledge       │     │
│  │  - Team leaderboard, shared setups  │     │
│  └─────────────────────────────────────┘     │
└─────────────────┬───────────────────────────┘
                  │ HTTPS (REST API)
                  ↓
┌─────────────────────────────────────────────┐
│  Cloud Server (GCP Cloud Run + Cloud SQL)   │
│                                             │
│  ┌──────────────────────────────────┐       │
│  │  FastAPI REST API                │       │
│  │  - POST /api/observations        │       │
│  │  - GET  /api/knowledge/{car}/{t} │       │
│  │  - POST /api/setups/share        │       │
│  │  - GET  /api/team/activity       │       │
│  └──────────────┬───────────────────┘       │
│                 ↓                            │
│  ┌──────────────────────────────────┐       │
│  │  PostgreSQL                      │       │
│  │  - observations, deltas, models  │       │
│  │  - users, teams, shared_setups   │       │
│  │  - activity_log                  │       │
│  └──────────────────────────────────┘       │
└─────────────────────────────────────────────┘
```

---

## Multi-Class Car Support Strategy

**Current state:** IOptimal only has CarModel definitions for 5 GTP/Hypercar cars. GT3, LMP2, LMP3, and Porsche Cup have zero support.

**Adding a new car requires two categories of data:**

### Hard dependencies (must be provided upfront, ~2-4 hours per car):
1. **Aero maps** — Balance/L/D tables by wing angle & ride height (from iRacing telemetry data or community sources)
2. **Mass & geometry** — Car mass, weight distribution, wheelbase, steering ratio (from iRacing specs)
3. **Setup writer IDs** — iRacing's `CarSetup_*` XML parameter IDs for .sto generation
4. **Garage ranges** — Legal min/max/step for each parameter

### Auto-learnable from telemetry (the team teaches the system):
| Parameter | Sessions needed | What the system learns |
|-----------|----------------|----------------------|
| Aero compression | 1-2 | Static→dynamic RH offset at speed |
| Heave m_eff | 2-4 | Effective sprung mass for RH prediction |
| Pushrod geometry | 3-5 | Pushrod offset → ride height sensitivity |
| ARB stiffness | 5-10 | Roll resistance per ARB setting |
| Corner spring MR | 5-10 | Motion ratio (spring→wheel rate) |
| Ride height model | 15-30 | Multi-variable static RH regression |
| Damper baselines | 1-2 | Reference click values |
| Tyre load sensitivity | 10+ | Grip vs vertical load curve |

### Platform families (reduce work):
- **Dallara LMDh** (BMW, Cadillac, Acura) share spring constants, ARB hardware, motion ratios, track widths
- **Multimatic LMDh** (Porsche) shares some LMDh properties but different ARB/spring hardware
- GT3 cars share common tire compounds and similar mass ranges — can bootstrap from a "generic GT3" baseline

### Car onboarding workflow (per new car):
1. **Admin uploads aero maps + basic specs** (hard dependency, one-time)
2. **System creates skeleton CarModel** with estimated defaults from nearest platform family
3. **Team members drive sessions** → IBT files auto-ingest → observations accumulate
4. **Server-side aggregator** auto-fits empirical models as sessions hit thresholds (5, 10, 30+ sessions)
5. **Support tier auto-promotes:** `unsupported` → `exploratory` (5 sessions) → `partial` (15 sessions) → `calibrated` (30+ sessions with stable holdout)
6. **Setup quality improves** automatically as models refine — no manual tuning needed

### Estimated car counts by class:
| Class | Cars in iRacing | Effort per car |
|-------|----------------|---------------|
| GTP/Hypercar | 5 (already defined) | Done/partial |
| GT3 | ~15 | 2-4 hrs hard deps + auto-learn |
| LMP2 | 1-2 | 2-4 hrs + auto-learn |
| LMP3 | 1 | 2-4 hrs + auto-learn |
| Porsche Cup | 1 | 2-4 hrs + auto-learn |

**Total initial setup work:** ~40-80 hours for hard dependencies across all cars, then the system auto-learns the rest from your team's collective driving.

---

## Component Design

### 1. IBT File Watcher (`watcher/`)

**New module.** Monitors the iRacing telemetry directory for new `.ibt` files and auto-ingests them.

**Key files to create:**
- `watcher/__init__.py`
- `watcher/monitor.py` - Filesystem watcher using `watchdog` library
- `watcher/service.py` - Background service that coordinates watch → ingest → sync

**How it works:**
1. On startup, scan `Documents/iRacing/Telemetry/` for any un-ingested IBT files
2. Start `watchdog.Observer` watching for new `.ibt` file creation events
3. When a new IBT is detected, wait for it to finish writing (file size stabilizes)
4. Run `learner/ingest.py` pipeline on it automatically
5. Queue the resulting Observation for sync to team server
6. Show a system tray notification: "Session ingested: BMW @ Sebring, 1:58.3"

**iRacing telemetry directory detection:**
- Windows: `%USERPROFILE%\Documents\iRacing\Telemetry\`
- Can be overridden in config

**Dependency:** `watchdog` (pip install watchdog)

**Reuses:** `learner/ingest.py`, `analyzer/extract.py`, `learner/observation.py` - all unchanged

### 2. Team Database & Sync (`teamdb/`)

**New module.** Handles local→remote sync of observations and knowledge retrieval.

**Key files to create:**
- `teamdb/__init__.py`
- `teamdb/models.py` - SQLAlchemy models for team database
- `teamdb/sync_client.py` - Client that pushes observations and pulls knowledge
- `teamdb/sync_server.py` - Server-side API for receiving and aggregating data
- `teamdb/aggregator.py` - Rebuilds empirical models from all team observations

**Database schema (PostgreSQL on server):**

```sql
-- ═══════════════════════════════════════════════════════
-- TEAM & AUTH
-- ═══════════════════════════════════════════════════════

CREATE TABLE teams (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    invite_code TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE members (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    iracing_name TEXT NOT NULL,
    iracing_member_id INTEGER,
    api_key_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of UUID4 API key
    role TEXT DEFAULT 'member',         -- 'admin' | 'engineer' | 'member'
    primary_class TEXT,                 -- 'gtp' | 'gt3' | 'lmp2' | 'lmp3' | 'cup'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Divisions/sub-teams within a team (e.g., "GT3 Squad", "Prototype Division")
CREATE TABLE divisions (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    name TEXT NOT NULL,
    car_class TEXT NOT NULL,            -- 'gtp' | 'gt3' | 'lmp2' | 'lmp3' | 'cup'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE division_members (
    division_id UUID REFERENCES divisions(id),
    member_id UUID REFERENCES members(id),
    PRIMARY KEY (division_id, member_id)
);

-- ═══════════════════════════════════════════════════════
-- CAR REGISTRY (auto-populated as team drives new cars)
-- ═══════════════════════════════════════════════════════

CREATE TABLE car_definitions (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    car_name TEXT NOT NULL,             -- e.g., "bmw_m_hybrid_v8"
    car_class TEXT NOT NULL,            -- 'gtp' | 'gt3' | 'lmp2' | 'lmp3' | 'cup'
    display_name TEXT NOT NULL,         -- "BMW M Hybrid V8"
    has_aero_maps BOOLEAN DEFAULT FALSE,
    has_car_model BOOLEAN DEFAULT FALSE,
    has_setup_writer BOOLEAN DEFAULT FALSE,
    support_tier TEXT DEFAULT 'unsupported', -- 'unsupported' | 'exploratory' | 'partial' | 'calibrated'
    observation_count INTEGER DEFAULT 0,
    car_model_json JSONB,              -- serialized CarModel overrides
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, car_name)
);

-- ═══════════════════════════════════════════════════════
-- CORE KNOWLEDGE (mirrors learner/ JSON structure)
-- ═══════════════════════════════════════════════════════

CREATE TABLE observations (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    member_id UUID REFERENCES members(id),
    session_id TEXT NOT NULL,
    car TEXT NOT NULL,
    car_class TEXT NOT NULL,
    track TEXT NOT NULL,
    best_lap_time_s FLOAT,
    lap_count INTEGER,
    observation_json JSONB NOT NULL,    -- full Observation.to_dict()
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, session_id)
);

-- Indexes for common queries
CREATE INDEX idx_obs_car_track ON observations(team_id, car, track);
CREATE INDEX idx_obs_car_class ON observations(team_id, car_class);
CREATE INDEX idx_obs_member ON observations(team_id, member_id);
CREATE INDEX idx_obs_created ON observations(team_id, created_at DESC);

CREATE TABLE deltas (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    member_id UUID REFERENCES members(id),
    car TEXT NOT NULL,
    track TEXT NOT NULL,
    setup_changes_count INTEGER,        -- for experiment gating (1-2 = high confidence)
    delta_json JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE empirical_models (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    car TEXT NOT NULL,
    track TEXT NOT NULL,
    model_json JSONB NOT NULL,          -- fitted models from aggregator
    observation_count INTEGER,
    support_tier TEXT DEFAULT 'exploratory',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, car, track)
);

-- Cross-car global models (platform-level knowledge)
CREATE TABLE global_car_models (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    car TEXT NOT NULL,
    model_json JSONB NOT NULL,          -- GlobalCarModel from cross_track.py
    tracks_included TEXT[],
    total_sessions INTEGER,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, car)
);

-- ═══════════════════════════════════════════════════════
-- SETUP SHARING & VERSIONING
-- ═══════════════════════════════════════════════════════

CREATE TABLE shared_setups (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    member_id UUID REFERENCES members(id),
    car TEXT NOT NULL,
    car_class TEXT NOT NULL,
    track TEXT NOT NULL,
    scenario TEXT,                       -- 'race' | 'quali' | 'sprint' | 'single_lap_safe'
    sto_content TEXT,                    -- raw .sto XML
    setup_json JSONB,                   -- parsed setup parameters for comparison
    notes TEXT,
    lap_time_s FLOAT,
    rating_sum INTEGER DEFAULT 0,       -- team members can upvote
    rating_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_setups_car_track ON shared_setups(team_id, car, track);

-- ═══════════════════════════════════════════════════════
-- ACTIVITY & ANALYTICS
-- ═══════════════════════════════════════════════════════

CREATE TABLE activity_log (
    id BIGSERIAL PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    member_id UUID REFERENCES members(id),
    event_type TEXT NOT NULL,           -- 'session_ingested' | 'setup_shared' | 'model_updated' | 'car_promoted' | 'member_joined'
    car TEXT,
    car_class TEXT,
    track TEXT,
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_activity_team ON activity_log(team_id, created_at DESC);
CREATE INDEX idx_activity_class ON activity_log(team_id, car_class, created_at DESC);

-- Leaderboard cache (rebuilt periodically by aggregator)
CREATE TABLE leaderboard (
    id UUID PRIMARY KEY,
    team_id UUID REFERENCES teams(id),
    car TEXT NOT NULL,
    track TEXT NOT NULL,
    member_id UUID REFERENCES members(id),
    best_lap_time_s FLOAT NOT NULL,
    session_date TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, car, track, member_id)
);
```

**Sync flow:**
1. Client ingests IBT locally → produces Observation
2. Client POSTs `observation.to_dict()` to server `/api/observations`
3. Server stores in PostgreSQL, runs delta detection against team's prior observations
4. Server periodically re-fits empirical models using ALL team observations
5. Client GETs updated models from `/api/knowledge/{car}/{track}`
6. Client's solver uses team-aggregated corrections (via existing `learner/recall.py`)

**Offline handling:**
- Observations queue locally in SQLite if server unreachable
- Sync retries with exponential backoff (5s, 30s, 2min, 10min)
- Full sync on reconnect

### 3. Team Server (`server/`) — Cloud Managed on GCP

**New module.** Standalone FastAPI server deployed to GCP Cloud Run with Cloud SQL PostgreSQL.

**Key files to create:**
- `server/__init__.py`
- `server/app.py` - FastAPI server with API routes
- `server/auth.py` - API key authentication middleware
- `server/routes/` - API route handlers (observations, knowledge, setups, team mgmt)
- `server/Dockerfile` - Container image for Cloud Run
- `server/cloudbuild.yaml` - GCP Cloud Build config for CI/CD
- `server/alembic/` - Database migration scripts

**API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/team/create` | Create team (returns invite code) |
| POST | `/api/team/join` | Join team with invite code (returns API key) |
| GET | `/api/team/members` | List team members |
| GET | `/api/team/activity` | Activity feed (paginated) |
| POST | `/api/observations` | Upload observation (JSONB) |
| GET | `/api/observations/{car}/{track}` | Get team observations (paginated) |
| GET | `/api/knowledge/{car}/{track}` | Get aggregated models + corrections |
| POST | `/api/setups/share` | Share a setup (.sto + notes) |
| GET | `/api/setups/{car}/{track}` | Browse shared setups |
| GET | `/api/stats` | Team stats (sessions, cars, tracks, members) |
| GET | `/api/leaderboard/{car}/{track}` | Fastest laps by member |

**Auth:** API key in `Authorization: Bearer <key>` header. Key issued on team join. Keys are UUID4 stored hashed (SHA-256) in DB.

**Cloud deployment stack:**
- **GCP Cloud Run** — Serverless container hosting, scales to zero when idle (~$5-15/mo for a small team)
- **GCP Cloud SQL (PostgreSQL 15)** — Managed database with automatic backups (~$10-20/mo for smallest tier)
- **GCP Secret Manager** — Store DB connection string, admin keys
- **Docker** — Container image built from `server/Dockerfile`
- **Cloud Build** — Auto-deploy on push to `main` (optional)

**Deployment command:**
```bash
# One-time setup
gcloud sql instances create ioptimal-team --database-version=POSTGRES_15 --tier=db-f1-micro --region=us-central1
gcloud run deploy ioptimal-server --source=server/ --allow-unauthenticated --set-env-vars="DATABASE_URL=..."

# Or via Docker
docker build -t ioptimal-server server/
docker run -e DATABASE_URL="..." -p 8080:8080 ioptimal-server
```

### 4. Extended Web UI

**Modify existing `webapp/`.** Add team-aware pages to the local web UI.

**New templates:**
- `team_dashboard.html` - Team activity feed, member stats, recent sessions
- `team_setups.html` - Browse and download shared team setups
- `team_knowledge.html` - Team-aggregated knowledge (observations from all members)
- `settings.html` - Configure team connection, API key, telemetry directory

**New routes in `webapp/app.py`:**
- `GET /team` - Team dashboard
- `GET /team/setups` - Shared setups browser
- `GET /team/knowledge` - Team knowledge explorer
- `GET /settings` - App settings page
- `POST /settings` - Save settings

**Existing routes stay the same** - personal runs, analysis, solver all work locally as before. Team features are additive.

### 5. Desktop App Packaging

**Recommended approach: System tray app + browser UI**

This is the simplest path that reuses your existing FastAPI webapp:

1. **System tray icon** using `pystray` library (cross-platform)
   - Shows IOptimal icon in system tray
   - Menu: "Open Dashboard", "Pause Watcher", "Sync Now", "Settings", "Quit"
   - Notifications for new sessions ingested

2. **Background services** on startup:
   - Start IBT file watcher
   - Start sync client
   - Start FastAPI server on localhost:8000
   - Open browser to `http://localhost:8000`

3. **Packaging with PyInstaller:**
   - Bundle everything into single `.exe` (Windows)
   - Include numpy, scipy, fastapi, uvicorn, watchdog, pystray
   - Ship with `data/aeromaps_parsed/`, `data/cars/`, `data/tracks/` bundled
   - Config file at `%APPDATA%/IOptimal/config.json`

**Key file to create:**
- `desktop/__init__.py`
- `desktop/app.py` - Main entry point (starts tray + services)
- `desktop/tray.py` - System tray icon and menu
- `desktop/config.py` - User configuration (team URL, API key, telemetry dir)
- `ioptimal.spec` - PyInstaller spec file

**Config file (`config.json`):**
```json
{
    "team_server_url": "https://your-team-server.com",
    "api_key": "member-api-key-here",
    "telemetry_dir": "C:\\Users\\You\\Documents\\iRacing\\Telemetry",
    "auto_ingest": true,
    "auto_sync": true,
    "car_filter": ["bmw"],
    "notification_sound": true
}
```

### 6. New Dependencies

```
# Add to requirements.txt
watchdog>=4.0          # Filesystem monitoring
pystray>=0.19         # System tray icon
Pillow>=10.0          # Required by pystray for icon
sqlalchemy>=2.0       # ORM for team database
psycopg2-binary>=2.9  # PostgreSQL driver (server only)
httpx>=0.27           # Already present - async HTTP client for sync
alembic>=1.13         # Database migrations
pyinstaller>=6.0      # Packaging (dev dependency)
```

---

## What Stays the Same (No Changes)

These modules are **untouched** — the team layer wraps around them:

- `solver/` - All 40+ solvers, objective function, legal search
- `aero_model/` - Aero response surfaces
- `car_model/` - Vehicle physical models
- `track_model/` - Track profiles and IBT parser
- `analyzer/` - Telemetry extraction, diagnosis, driver style, segmentation
- `pipeline/` - Setup production pipeline
- `output/` - .sto file generation
- `validation/` - Objective validation
- `learner/observation.py` - Observation data class
- `learner/empirical_models.py` - Model fitting algorithms
- `learner/delta_detector.py` - Delta detection logic

## What Gets Modified

- `learner/knowledge_store.py` - Add optional database backend (PostgreSQL) alongside JSON
- `learner/recall.py` - Add team knowledge source (query server when available, fall back to local)
- `learner/ingest.py` - Add hook to queue observation for sync after local storage
- `webapp/app.py` - Add team routes and settings page
- `webapp/settings.py` - Add team config fields
- `webapp/templates/base.html` - Add team nav items

---

## Phased Implementation Plan

### Phase 1: IBT File Watcher + Car Auto-Detection (Week 1-2)
- Create `watcher/` module with watchdog-based monitor
- Auto-detect iRacing telemetry directory (`Documents/iRacing/Telemetry/`)
- On new IBT: wait for write completion → detect car from IBT header → ingest via `learner/ingest.py`
- Auto-register unknown cars: parse car name from IBT, create skeleton entry in local DB
- For known cars (GTP): full physics pipeline. For unknown cars: extract raw telemetry + setup only (observations still valuable)
- Bulk import mode: scan for all existing IBTs on first install
- **Test:** Start watcher, copy IBT files (BMW GTP + GT3 car), verify both get observations, BMW gets full solve, GT3 gets raw observation stored

### Phase 2: Team Server & Cloud Database (Week 2-4)
- Create `server/` module with FastAPI + SQLAlchemy + PostgreSQL
- Define SQLAlchemy models matching full schema (teams, members, divisions, car_definitions, observations, etc.)
- Implement core API: create team, join with invite code, upload observations, get knowledge
- API key auth middleware (SHA-256 hashed keys)
- Car registry endpoint: auto-create car entries as team members drive new cars
- Server-side aggregator: periodically re-fit empirical models per car/track, update support tiers
- Dockerfile + Cloud Run deployment config + Alembic migrations
- **Test:** Deploy to Cloud Run, create team, join, POST observation via curl, verify car auto-registered and observation persisted

### Phase 3: Sync Client (Week 4-5)
- Create `teamdb/sync_client.py` — background thread for push/pull
- Push: queue observations locally in SQLite → batch-push to server on interval (30s)
- Pull: fetch updated empirical models + team knowledge on startup and every 5 min
- Offline queue: observations persist locally if server unreachable, retry with exponential backoff
- Modify `learner/recall.py` to prefer team knowledge when available, fall back to local
- Car model updates: pull refined CarModel overrides from server as auto-learning improves them
- **Test:** Ingest IBT offline → go online → verify observation syncs → verify other team member sees it

### Phase 4: Team Web UI (Week 5-7)
- **Team dashboard** (`/team`): activity feed, member stats, car coverage heatmap, recent sessions
- **Division views** (`/team/divisions/{class}`): per-class activity, knowledge, leaderboard
- **Shared setups** (`/team/setups`): browse by car+track, download .sto, upvote, add notes
- **Team knowledge** (`/team/knowledge`): aggregated models per car/track, support tier badges, observation counts
- **Leaderboard** (`/team/leaderboard`): fastest laps by car+track, session history per member
- **Car status page** (`/team/cars`): all cars the team drives, support tier, auto-learning progress
- **Settings** (`/settings`): team URL, API key, telemetry directory, notification preferences, division assignment
- **Test:** Open localhost:8000/team, see multi-class activity, filter by division, download GT3 shared setup

### Phase 5: Desktop Packaging (Week 7-8)
- System tray app with pystray (Windows + Mac)
- PyInstaller bundling into single `.exe` installer
- First-run wizard: detect telemetry dir → enter team invite code → choose divisions → bulk import existing IBTs
- Auto-update mechanism (check server for new client version on startup)
- **Test:** Install on clean Windows machine, join team, run iRacing session, verify auto-ingest and sync

### Phase 6: Multi-Class Car Onboarding (Week 8-10)
- Build skeleton CarModel definitions for GT3 cars (mass, geometry, garage ranges from iRacing specs)
- Build skeleton CarModel definitions for LMP2, LMP3, Porsche Cup
- Create admin tool for uploading aero maps per car
- Create admin tool for setup writer parameter ID mapping per car
- Implement auto-promotion: server monitors observation count and model stability → auto-upgrades support tier
- **Test:** Drive GT3 car → observations ingest → after 10+ sessions, verify empirical models start forming → after 30+ sessions, verify setup solver produces reasonable output

### Phase 7: Polish & Team Beta (Week 10-12)
- Notification system (new setups shared, car promoted, model updated)
- Cross-class knowledge transfer (if same driver drives GT3 and GTP at same track, share track profile)
- Team analytics dashboard (sessions per week trend, knowledge growth, fastest lap trends)
- Load testing (simulate 100 concurrent syncs)
- Real team beta with 10-20 drivers, iterate on feedback

---

## Verification Plan

1. **Unit tests:** Each new module gets tests in `tests/`
2. **Integration test (single class):** IBT drop → watcher picks up → ingest → sync to server → other client pulls → solver uses team knowledge
3. **Integration test (multi-class):** GT3 IBT + GTP IBT from different members → both sync → server aggregates separately → car support tiers update
4. **Scale test:** Simulate 50 members uploading 10 observations each → verify server handles 500 observations → models rebuild in <60s
5. **Offline test:** Ingest 5 IBTs while disconnected → reconnect → verify all 5 sync without data loss
6. **Desktop test:** Install on clean Windows machine, join team, run iRacing session, verify end-to-end flow

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Server hosting | GCP Cloud Run + Cloud SQL | Auto-scales, managed PostgreSQL, handles 50-100 members easily |
| Server DB | PostgreSQL 15 (Cloud SQL) | JSONB for observations, concurrent writes, managed backups, good query perf at this scale |
| Local DB | SQLite (existing) | Already works, lightweight, offline-capable |
| Sync model | Push observations, pull models | Simple, works offline, avoids conflict resolution on raw data |
| Desktop packaging | PyInstaller + pystray | Simplest path, reuses existing web UI, no Electron overhead |
| Auth | Invite code + API keys | Simple join flow, stateless API auth, no password management |
| Model aggregation | Server-side only | One source of truth, avoids N-way merge conflicts |
| IBT detection | watchdog library | Battle-tested, cross-platform filesystem events |
| Multi-class | Auto-register unknown cars | Team can drive any car; system auto-learns over time |
| Divisions | Optional sub-teams by class | Keeps UI organized for large teams |

---

## Cost Estimate (50-100 team members)

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| GCP Cloud Run | $15-40 | Auto-scales, higher traffic than small team |
| GCP Cloud SQL (db-g1-small) | $25-50 | Small instance, handles hundreds of thousands of rows |
| GCP Secret Manager | ~$0 | Free tier |
| GCP Cloud Storage (optional) | $1-5 | For aero map uploads, IBT archival |
| **Total** | **~$40-95/mo** | ~$0.50-1.00 per member per month |

---

## New Files Summary

```
watcher/
├── __init__.py
├── monitor.py          # watchdog filesystem observer
└── service.py          # Coordinates watch → ingest → sync queue

teamdb/
├── __init__.py
├── models.py           # SQLAlchemy ORM models
├── sync_client.py      # Background sync (push observations, pull models)
└── aggregator.py       # Server-side: rebuild models from all team data

server/
├── __init__.py
├── app.py              # FastAPI server application
├── auth.py             # API key middleware
├── routes/
│   ├── team.py         # Team create/join/members
│   ├── observations.py # Upload/query observations
│   ├── knowledge.py    # Aggregated models endpoint
│   ├── setups.py       # Setup sharing
│   └── activity.py     # Activity feed + leaderboard
├── Dockerfile
├── cloudbuild.yaml
└── alembic/            # DB migrations

desktop/
├── __init__.py
├── app.py              # Main entry point (tray + services)
├── tray.py             # System tray icon (pystray)
├── config.py           # User config (team URL, API key, etc.)
└── first_run.py        # First-run setup wizard

webapp/templates/
├── team_dashboard.html  # (new) Team activity feed + stats
├── team_division.html   # (new) Per-class division view
├── team_setups.html     # (new) Shared setups browser
├── team_knowledge.html  # (new) Team knowledge explorer
├── team_leaderboard.html # (new) Fastest laps
├── team_cars.html       # (new) Car registry + support tiers
└── settings.html        # (new) App configuration + first-run

ioptimal.spec            # PyInstaller packaging spec
```

## Existing Code Reuse (no changes needed)

| Module | What it does | How team tool uses it |
|--------|-------------|----------------------|
| `solver/` (40+ files) | Physics-based setup solver | Runs locally on each member's PC, same as today |
| `aero_model/` | Aero response surfaces | Loaded locally, shared aero maps via server |
| `car_model/cars.py` | CarModel definitions | Extended with auto-learned overrides from server |
| `track_model/` | Track profiles + IBT parser | Builds profiles locally from each member's IBTs |
| `analyzer/` | Telemetry extraction | Runs locally on each IBT, produces observations |
| `pipeline/produce.py` | IBT→.sto full pipeline | Runs locally, now with team knowledge injected |
| `output/setup_writer.py` | .sto XML generation | Per-car parameter IDs, extended for new classes |
| `learner/observation.py` | Observation dataclass | Unchanged — 100+ fields, serialized to JSONB |
| `learner/empirical_models.py` | Model fitting | Used server-side by aggregator |
| `learner/delta_detector.py` | Session comparison | Used server-side for team-wide deltas |
| `learner/cross_track.py` | Cross-track models | Used server-side for global car models |
