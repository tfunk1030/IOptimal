# Team Telemetry Tool — Implementation Review & Next Steps

## What Was Built

33 files / ~3,900 lines across 4 new modules + webapp extensions:

| Module | Files | Status | Description |
|--------|-------|--------|-------------|
| `watcher/` | 3 | Working | IBT auto-detection via watchdog, file stability check, car identification from IBT headers, bulk import |
| `teamdb/` | 4 | Needs fixes | SQLAlchemy ORM (12 tables), sync client with offline queue, server-side aggregator |
| `server/` | 10 | Needs fixes | FastAPI REST API, invite-code auth, observation/setup/knowledge endpoints, Dockerfile |
| `desktop/` | 5 | Needs fixes | System tray app, config management, service orchestration |
| `webapp/` | 8 modified | Needs fixes | Team dashboard, setups, leaderboard, cars, knowledge, settings pages |
| `docs/team_tool_plan.md` | 1 | Complete | Full architecture plan |

---

## Known Bugs (12 total — must fix before first run)

### CRITICAL (8) — will crash at runtime

| # | Bug | Files | Fix |
|---|-----|-------|-----|
| 1 | **Config field name mismatches** — `settings.html` uses `server_url`, `sound_enabled`, `browser_open_on_start` but `desktop/config.py` has `team_server_url`, `notification_sound`, `open_browser_on_start` | `desktop/config.py`, `webapp/app.py`, `settings.html` | Align field names: rename config fields to match template OR add property aliases |
| 2 | **Missing config fields** — Template references `invite_code`, `iracing_name`, `push_interval`, `pull_interval`, `team_connected` which don't exist in AppConfig | `desktop/config.py` | Add these fields to the dataclass |
| 3 | **POST /settings handler missing form params** — Handler doesn't accept `invite_code`, `iracing_name`, `push_interval`, `pull_interval` from the form | `webapp/app.py` | Add missing Form() parameters to handler |
| 4 | **Server routes import nonexistent `Car` model** — Should be `CarDefinition` | `server/routes/observations.py`, `knowledge.py`, `setups.py` | Change `Car` to `CarDefinition` |
| 5 | **Server routes import nonexistent `SetupRating` model** | `server/routes/setups.py` | Create `SetupRating` table in `teamdb/models.py` |
| 6 | **ActivityLog field names wrong** — Routes use `action`/`detail` but model has `event_type`/`summary` | `server/routes/observations.py`, `setups.py`, `team.py` | Fix field names |
| 7 | **Member field name wrong** — Routes use `joined_at` but model has `created_at` | `server/routes/team.py` | Fix to `created_at` |
| 8 | **SharedSetup rating mismatch** — Routes use `rating` but model has `rating_sum`/`rating_count` | `server/routes/setups.py` | Fix to use `rating_sum`/`rating_count` |

### HIGH (4) — will cause template errors or wrong data

| # | Bug | Files | Fix |
|---|-----|-------|-----|
| 9 | **team_dashboard stats not flattened** — Template expects `total_members`, `total_sessions` etc. but gets nested `stats` dict | `webapp/app.py`, `team_dashboard.html` | Flatten stats dict in `_load_team_data()` |
| 10 | **team_leaderboard missing filter lists** — Template expects `cars` and `tracks` but route doesn't pass them | `webapp/app.py` | Add `cars=[], tracks=[]` to leaderboard context |
| 11 | **Pydantic schemas don't match ORM models** — `MemberOut.joined_at`, `ActivityOut.action/detail`, `EmpiricalModelOut.model_id/model_type`, `LeaderboardEntry.session_id` | `server/routes/*.py` | Update schemas to match model field names |
| 12 | **SQLite dev mode incompatible** — Models use PostgreSQL-specific types (JSONB, ARRAY, UUID) but default DATABASE_URL is SQLite | `server/database.py`, `teamdb/models.py` | Add type adapters or use dialect-agnostic types for dev mode |

---

## Developer Next Steps (in order)

### Step 1: Fix the 12 bugs above
Ask Claude to fix them — all 12 can be done in one pass.

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

All 7 phases are scaffolded in code. Phases 1-5 have working code (pending the 12 bug fixes). Phase 6-7 require manual car data and team testing.
