-- Drivers (extends Supabase auth.users)
create table public.drivers (
  id uuid references auth.users primary key,
  display_name text not null,
  team_id uuid, -- will reference public.teams
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

-- Add foreign key constraint now that teams exists
alter table public.drivers add constraint fk_team_id foreign key (team_id) references public.teams;

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

-- Observations (structured telemetry snapshots)
create table public.observations (
  id uuid primary key default gen_random_uuid(),
  session_id uuid references public.sessions not null,
  driver_id uuid references public.drivers not null,
  car text not null,
  track text not null,
  data jsonb not null,            -- full observation dict
  driver_style jsonb,             -- driver profile
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
  data jsonb not null,            -- delta dict
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

-- Row Level Security
alter table public.sessions enable row level security;
alter table public.observations enable row level security;
alter table public.deltas enable row level security;
alter table public.models enable row level security;
alter table public.drivers enable row level security;
alter table public.teams enable row level security;

create policy "Drivers see their team sessions" on public.sessions
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );

create policy "Drivers see their team observations" on public.observations
  for select using (
    driver_id in (select id from public.drivers where team_id in (select team_id from public.drivers where id = auth.uid()))
  );

create policy "Drivers see their team deltas" on public.deltas
  for select using (
    driver_id in (select id from public.drivers where team_id in (select team_id from public.drivers where id = auth.uid()))
  );

create policy "Drivers see their team models" on public.models
  for select using (
    team_id in (select team_id from public.drivers where id = auth.uid())
  );
