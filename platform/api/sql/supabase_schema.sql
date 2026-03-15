-- iOptimal platform schema for team telemetry + setup workflows.
-- Execute in Supabase SQL editor.

create extension if not exists "pgcrypto";

create table if not exists public.teams (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  invite_code text unique not null default substr(gen_random_uuid()::text, 1, 8),
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);

create table if not exists public.drivers (
  id uuid primary key references auth.users(id),
  display_name text not null,
  team_id uuid references public.teams(id),
  default_car text not null default 'bmw',
  created_at timestamptz not null default now()
);

create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references public.drivers(id),
  team_id uuid not null references public.teams(id),
  car text not null,
  track text,
  track_config text,
  wing_angle double precision,
  best_lap_time double precision,
  lap_number int,
  driver_style text,
  status text not null default 'processing' check (status in ('processing', 'complete', 'error')),
  results jsonb,
  report_text text,
  learner_summary jsonb,
  error text,
  ibt_storage_path text,
  sto_storage_path text,
  created_at timestamptz not null default now()
);

create table if not exists public.observations (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.sessions(id),
  driver_id uuid not null references public.drivers(id),
  team_id uuid not null references public.teams(id),
  car text not null,
  track text not null,
  data jsonb not null,
  driver_style jsonb,
  diagnosis jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.deltas (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references public.drivers(id),
  team_id uuid not null references public.teams(id),
  car text not null,
  track text not null,
  from_session uuid not null references public.sessions(id),
  to_session uuid not null references public.sessions(id),
  data jsonb not null,
  causal_confidence double precision,
  created_at timestamptz not null default now()
);

create table if not exists public.models (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid references public.drivers(id),
  team_id uuid not null references public.teams(id),
  car text not null,
  track text not null,
  model_type text not null,
  data jsonb not null,
  session_count int not null default 0,
  updated_at timestamptz not null default now()
);

create unique index if not exists models_driver_scope_unique
  on public.models (coalesce(driver_id, '00000000-0000-0000-0000-000000000000'::uuid), team_id, car, track, model_type);

alter table public.sessions enable row level security;
alter table public.observations enable row level security;
alter table public.deltas enable row level security;
alter table public.models enable row level security;
alter table public.drivers enable row level security;
alter table public.teams enable row level security;

create policy if not exists sessions_team_select on public.sessions
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );

create policy if not exists sessions_team_insert on public.sessions
  for insert with check (
    driver_id = auth.uid()
    and team_id in (select team_id from public.drivers where id = auth.uid())
  );

create policy if not exists observations_team_select on public.observations
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );

create policy if not exists deltas_team_select on public.deltas
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );

create policy if not exists models_team_select on public.models
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );

-- Storage buckets (create in dashboard):
-- 1) ibt-files (private)
-- 2) sto-files (private)

