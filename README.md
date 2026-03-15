# iOptimal Platform

Web platform + local watcher around the existing iOptimal solver stack.

## What Is Included

- `platform/api`: FastAPI backend with upload, sessions, results, setups, team knowledge, compare, and auth routes.
- `platform/watcher`: Windows-first local watcher that monitors `.ibt`, runs local `produce()`, syncs team learnings, and uploads raw + artifacts.
- `platform/frontend`: Vite + React + Tailwind dashboard with requested route structure.
- `platform/api/sql/supabase_schema.sql`: Supabase schema + baseline RLS policies.

Existing solver/analyzer/learner modules are used as black-box imports and are not refactored.

## Quick Start

1. Install Python deps:

```powershell
pip install -r requirements.txt
```

2. Copy environment template:

```powershell
copy .env.example .env
```

3. Run backend from the `platform` directory:

```powershell
cd platform
uvicorn api.main:app --reload --port 8000
```

4. Run frontend:

```powershell
cd platform/frontend
npm install
npm run dev
```

5. Run watcher (optional):

```powershell
cd platform
python -m watcher.main --email you@example.com --password your-password
```

## Supabase Setup

1. Create project + buckets:
- `ibt-files` (private)
- `sto-files` (private)

2. Run:
- `platform/api/sql/supabase_schema.sql`

3. Set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` in `.env`.

## API Endpoints

- `POST /api/upload-ibt`
- `GET /api/results/{session_id}`
- `GET /api/setups/{session_id}`
- `GET /api/sessions`
- `GET /api/team/knowledge`
- `GET /api/team/sync-learnings`
- `GET /api/team/compare`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`

## Dev Auth Behavior

- Remote callers: Supabase JWT required.
- Localhost callers: open dev mode is allowed when `IOPTIMAL_DEV_LOCAL_OPEN_AUTH=true`.

