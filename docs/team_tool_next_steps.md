# Team Telemetry Tool — Implementation Review & Deployment Status

## What Was Built

33 files / ~3,900 lines across 4 new modules + webapp extensions:

| Module | Files | Status | Description |
|--------|-------|--------|-------------|
| `watcher/` | 3 | Working | IBT auto-detection via watchdog, file stability check, car identification from IBT headers, bulk import |
| `teamdb/` | 4 | Working | SQLAlchemy ORM (13 tables incl. SetupRating), sync client with offline queue, server-side aggregator |
| `server/` | 10 | Deployed | FastAPI REST API, invite-code auth, observation/setup/knowledge endpoints, Cloud Run |
| `desktop/` | 5 | Packaged | System tray app, config management, service orchestration, PyInstaller .exe |
| `webapp/` | 8 modified | Working | Team dashboard, setups, leaderboard, cars, knowledge, settings pages |
| `docs/team_tool_plan.md` | 1 | Complete | Full architecture plan |

---

## Known Bugs — ALL 18 FIXED

Original 12 bugs fixed in commit `e2933a9`. Additional 6 bugs found and fixed during deployment.

### CRITICAL (8) — FIXED (original)

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

### HIGH (4) — FIXED (original)

| # | Bug | Fix Applied |
|---|-----|------------|
| 9 | **team_dashboard stats not flattened** | `_load_team_data()` now returns flat keys: `total_members`, `total_sessions`, `cars_tracked`, `tracks_covered` |
| 10 | **team_leaderboard missing filter lists** | Added `cars=[]`, `tracks=[]` to leaderboard context |
| 11 | **Pydantic schemas don't match ORM models** | All schemas (`EmpiricalModelOut`, `LeaderboardEntry`, `MemberOut`, `ActivityOut`) match ORM fields |
| 12 | **SQLite dev mode incompatible** | Models use portable type aliases (`_JsonType`, `_UuidType`, `_ArrayTextType`) that auto-detect PostgreSQL vs SQLite |

### DEPLOYMENT BUGS (6) — FIXED

| # | Bug | Fix Applied |
|---|-----|------------|
| 13 | **Config accessor mismatch** | `desktop/app.py:212` used `open_browser_on_start` instead of `browser_open_on_start` |
| 14 | **Sync client field mismatch** | `/api/stats` returned `CarStat.name` but sync client expected `car_name`; added `tracks` list per car |
| 15 | **Dockerfile missing teamdb** | Moved Dockerfile to project root; COPYs both `server/` and `teamdb/`; added `.dockerignore` |
| 16 | **Timezone-naive datetimes** | asyncpg rejects mixing tz-aware and tz-naive; added `DateTime(timezone=True)` to all timestamp columns |
| 17 | **UUID→str coercion** | PostgreSQL returns UUID objects; Pydantic schemas expect `str`; added `str()` wrappers in all route handlers |
| 18 | **Missing httpx in server requirements** | Added `httpx>=0.24` to `server/requirements-server.txt` |

---

## Deployment Reference

### Live Infrastructure (2026-03-27)

| Resource | Value |
|----------|-------|
| GCP Project | `ioptimal` |
| Cloud SQL Instance | `ioptimal-db` (PostgreSQL 15, db-f1-micro, us-central1) |
| Cloud SQL IP | `34.132.89.214` |
| Cloud SQL Connection | `ioptimal:us-central1:ioptimal-db` |
| Cloud Run Service | `ioptimal-server` (revision 4, us-central1) |
| **Service URL** | **`https://ioptimal-server-27191526338.us-central1.run.app`** |
| Secret Manager | `database-url` (DATABASE_URL connection string) |

### Team: SOELPEC Precision Racing

| Field | Value |
|-------|-------|
| Team ID | `a26bd08fbf22485885b59affea2046f0` |
| **Invite Code** | **`5a1c520b`** (share with teammates) |
| Admin API Key | `4e41657f997646769d49a05d2f7b6bba` (keep secret) |

### Desktop App

| Item | Path |
|------|------|
| Executable | `dist/IOptimal/IOptimal.exe` |
| Total Size | ~177 MB |
| Entry Point | `desktop/app.py` |

---

## Developer Steps — Status

### Step 1: ~~Fix the 12 bugs~~ DONE
All 12 bugs fixed in commit `e2933a9`.

### Step 2: ~~Install dependencies~~ DONE
```bash
pip install watchdog pystray Pillow sqlalchemy aiosqlite httpx pyinstaller
```
Requirements files: `requirements-desktop.txt` (full app), `server/requirements-server.txt` (server only).

### Step 3: ~~GCP infrastructure~~ DONE
- Project: `ioptimal` (billing enabled)
- APIs: Cloud Run, Cloud SQL, Secret Manager, Cloud Build, Artifact Registry
- Cloud SQL: PostgreSQL 15, db-f1-micro (~$7/mo), us-central1
- Secret: `database-url` with asyncpg connection string
- IAM: Compute service account has secretAccessor + cloudsql.client roles

### Step 4: ~~Create team~~ DONE
Team "SOELPEC Precision Racing" created. Invite code: `5a1c520b`.

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

### Step 6: ~~Package desktop app~~ DONE
Built with PyInstaller: `dist/IOptimal/IOptimal.exe` (177 MB).
```bash
pyinstaller --name IOptimal --windowed --noconfirm \
  --add-data "data/aeromaps_parsed;data/aeromaps_parsed" \
  --add-data "data/cars;data/cars" \
  --add-data "data/tracks;data/tracks" \
  --add-data "webapp/templates;webapp/templates" \
  --add-data "webapp/static;webapp/static" \
  --add-data "skill;skill" \
  --hidden-import pystray._win32 \
  --hidden-import PIL \
  --hidden-import uvicorn.logging \
  --hidden-import "uvicorn.protocols.http" \
  --hidden-import "uvicorn.protocols.http.auto" \
  --hidden-import "uvicorn.protocols.http.h11_impl" \
  --hidden-import "uvicorn.protocols.http.httptools_impl" \
  --hidden-import "uvicorn.protocols.websockets" \
  --hidden-import "uvicorn.protocols.websockets.auto" \
  --hidden-import "uvicorn.lifespan" \
  --hidden-import "uvicorn.lifespan.on" \
  --hidden-import "uvicorn.lifespan.off" \
  --hidden-import aiosqlite \
  --hidden-import "sqlalchemy.dialects.sqlite" \
  --hidden-import "sqlalchemy.dialects.sqlite.aiosqlite" \
  --collect-submodules watchdog \
  --collect-submodules pystray \
  desktop/app.py
```

---

## Monthly Cost Estimate (50-100 team members)

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| GCP Cloud Run | $15-40 | Auto-scales, higher traffic than small team |
| GCP Cloud SQL (db-f1-micro) | ~$7-10 | Micro instance, sufficient for initial use |
| GCP Secret Manager | ~$0 | Free tier |
| GCP Cloud Storage (optional) | $1-5 | For aero map uploads, IBT archival |
| **Total** | **~$25-55/mo** | ~$0.25-0.55 per member per month |

---

## Phased Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | IBT File Watcher + Car Auto-Detection | DONE |
| 2 | Team Server + Cloud Database | DEPLOYED |
| 3 | Sync Client (push/pull) | DONE |
| 4 | Team Web UI (dashboard, setups, leaderboard) | DONE |
| 5 | Desktop Packaging (PyInstaller + tray) | DONE |
| 6 | Multi-Class Car Onboarding | Pending (manual car data) |
| 7 | Polish + Team Beta | Pending (team testing) |

Phases 1-5 are complete and deployed. Phase 6-7 require manual car data and team testing.

---

## API Quick Reference

Base URL: `https://ioptimal-server-27191526338.us-central1.run.app`

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/health` | None | Health check |
| POST | `/api/team/create` | None | Create team → team_id, invite_code, admin_api_key |
| POST | `/api/team/join` | None | Join via invite_code → member_id, api_key |
| GET | `/api/team/members` | Bearer | List team members |
| GET | `/api/team/activity` | Bearer | Activity log |
| POST | `/api/observations` | Bearer | Upload observation |
| GET | `/api/observations/{car}/{track}` | Bearer | Query observations |
| GET | `/api/stats` | Bearer | Team statistics |
| GET | `/api/knowledge/{car}/{track}` | Bearer | Empirical models |
| POST | `/api/setups/share` | Bearer | Share setup |
| GET | `/api/setups/{car}/{track}` | Bearer | List shared setups |
| POST | `/api/setups/{id}/rate` | Bearer | Rate setup |
| GET | `/api/leaderboard/{car}/{track}` | Bearer | Leaderboard |
