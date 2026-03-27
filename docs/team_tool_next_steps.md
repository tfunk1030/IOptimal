# Team Telemetry Tool — Implementation Review & Next Steps

## What Was Built

33 files / ~3,900 lines across 4 new modules + webapp extensions:

| Module | Files | Status | Description |
|--------|-------|--------|-------------|
| `watcher/` | 3 | Working | IBT auto-detection via watchdog, file stability check, car identification from IBT headers, bulk import |
| `teamdb/` | 4 | Working | SQLAlchemy ORM (13 tables incl. SetupRating), sync client with offline queue, server-side aggregator |
| `server/` | 10 | Working | FastAPI REST API, invite-code auth, observation/setup/knowledge endpoints, Dockerfile |
| `desktop/` | 5 | Working | System tray app, config management, service orchestration |
| `webapp/` | 8 modified | Working | Team dashboard, setups, leaderboard, cars, knowledge, settings pages |
| `docs/team_tool_plan.md` | 1 | Complete | Full architecture plan |

---

## Known Bugs — ALL 12 FIXED

All 12 bugs identified in the code review have been resolved (commit `e2933a9`).

### CRITICAL (8) — FIXED

| # | Bug | Fix Applied |
|---|-----|------------|
| 1 | **Config field name mismatches** | Renamed config fields to `sound_enabled`, `browser_open_on_start`; template uses `team_server_url` |
| 2 | **Missing config fields** | Added `invite_code`, `iracing_name`, `push_interval`, `pull_interval` + `team_connected` property |
| 3 | **POST /settings handler missing form params** | Handler now accepts all form fields from `settings.html` |
| 4 | **Server routes import nonexistent `Car` model** | Changed to `CarDefinition` with correct field names (`car_name`, `display_name`) |
| 5 | **Missing `SetupRating` model** | Added `SetupRating` table to `teamdb/models.py` (13 tables total) |
| 6 | **ActivityLog field names wrong** | Fixed `action`→`event_type`, `detail`→`summary` in all routes |
| 7 | **Member field name wrong** | Fixed `joined_at`→`created_at` in schemas, queries, and construction |
| 8 | **SharedSetup rating mismatch** | Fixed to use `rating_sum`/`rating_count` everywhere |

### HIGH (4) — FIXED

| # | Bug | Fix Applied |
|---|-----|------------|
| 9 | **team_dashboard stats not flattened** | `_load_team_data()` now returns flat keys: `total_members`, `total_sessions`, `cars_tracked`, `tracks_covered` |
| 10 | **team_leaderboard missing filter lists** | Added `cars=[]`, `tracks=[]` to leaderboard context |
| 11 | **Pydantic schemas don't match ORM models** | All schemas (`EmpiricalModelOut`, `LeaderboardEntry`, `MemberOut`, `ActivityOut`) match ORM fields |
| 12 | **SQLite dev mode incompatible** | Models use portable type aliases (`_JsonType`, `_UuidType`, `_ArrayTextType`) that auto-detect PostgreSQL vs SQLite |

---

## Developer Next Steps (in order)

### Step 1: ~~Fix the 12 bugs~~ DONE
All 12 bugs fixed in commit `e2933a9`.

### Step 2: Install new Python dependencies
```bash
pip install watchdog pystray Pillow sqlalchemy psycopg2-binary alembic httpx aiosqlite asyncpg
```

### Step 3: Set up GCP infrastructure (~1-2 hours)
```bash
# 1. Create GCP project
gcloud projects create ioptimal-team

# 2. Enable required APIs
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com

# 3. Create Cloud SQL PostgreSQL instance
gcloud sql instances create ioptimal-db \
  --database-version=POSTGRES_15 \
  --tier=db-g1-small \
  --region=us-central1 \
  --root-password=YOUR_DB_PASSWORD

# 4. Create database
gcloud sql databases create ioptimal --instance=ioptimal-db

# 5. Store connection string in Secret Manager
echo -n "postgresql+asyncpg://postgres:PASSWORD@/ioptimal?host=/cloudsql/PROJECT:REGION:ioptimal-db" | \
  gcloud secrets create database-url --data-file=-

# 6. Build and deploy server
cd server/
gcloud run deploy ioptimal-server \
  --source=. \
  --allow-unauthenticated \
  --add-cloudsql-instances=PROJECT:REGION:ioptimal-db \
  --set-secrets=DATABASE_URL=database-url:latest \
  --region=us-central1

# 7. Note the service URL (e.g., https://ioptimal-server-xxx-uc.a.run.app)
```

### Step 4: Create your team
```bash
curl -X POST https://YOUR-SERVER-URL/api/team/create \
  -H "Content-Type: application/json" \
  -d '{"name": "Your Team Name"}'

# Response: {"team_id": "...", "invite_code": "abc12def", "admin_api_key": "..."}
# SAVE the invite_code and admin_api_key!
```

### Step 5: Onboard cars (~2-4 hours per new car class)

For each car class your team races (GT3, LMP2, etc.):
1. **Gather aero maps** — Excel files with DF balance and L/D by ride height and wing angle
2. **Get mass/geometry** — Car mass, weight distribution, wheelbase from iRacing specs
3. **Map setup writer IDs** — iRacing's `CarSetup_*` XML parameter names (inspect a .sto file)
4. **Define garage ranges** — Legal min/max/step per parameter
5. **Create CarModel** in `car_model/cars.py`

GTP cars (BMW, Ferrari, etc.) are already done.

Estimated car counts:
| Class | Cars in iRacing | Effort per car |
|-------|----------------|---------------|
| GTP/Hypercar | 5 (already defined) | Done/partial |
| GT3 | ~15 | 2-4 hrs hard deps + auto-learn |
| LMP2 | 1-2 | 2-4 hrs + auto-learn |
| LMP3 | 1 | 2-4 hrs + auto-learn |
| Porsche Cup | 1 | 2-4 hrs + auto-learn |

**Total initial setup work:** ~40-80 hours for hard dependencies across all cars, then the system auto-learns the rest from your team's collective driving.

### Step 6: Package the desktop app (~4-8 hours first time)
```bash
pip install pyinstaller
pyinstaller --name IOptimal \
  --windowed \
  --add-data "data/aeromaps_parsed:data/aeromaps_parsed" \
  --add-data "data/cars:data/cars" \
  --add-data "data/tracks:data/tracks" \
  --add-data "webapp/templates:webapp/templates" \
  --add-data "webapp/static:webapp/static" \
  desktop/app.py
```
This produces `dist/IOptimal/IOptimal.exe` — zip it for distribution.

---

## Monthly Cost Estimate (50-100 team members)

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| GCP Cloud Run | $15-40 | Auto-scales, higher traffic than small team |
| GCP Cloud SQL (db-g1-small) | $25-50 | Small instance, handles hundreds of thousands of rows |
| GCP Secret Manager | ~$0 | Free tier |
| GCP Cloud Storage (optional) | $1-5 | For aero map uploads, IBT archival |
| **Total** | **~$40-95/mo** | ~$0.50-1.00 per member per month |

---

## Phased Roadmap

| Phase | Scope | Timeline |
|-------|-------|----------|
| 1 | IBT File Watcher + Car Auto-Detection | Week 1-2 |
| 2 | Team Server + Cloud Database | Week 2-4 |
| 3 | Sync Client (push/pull) | Week 4-5 |
| 4 | Team Web UI (dashboard, setups, leaderboard) | Week 5-7 |
| 5 | Desktop Packaging (PyInstaller + tray) | Week 7-8 |
| 6 | Multi-Class Car Onboarding | Week 8-10 |
| 7 | Polish + Team Beta | Week 10-12 |

All 7 phases are scaffolded in code. Phases 1-5 have working code (all 12 bugs fixed). Phase 6-7 require manual car data and team testing.
