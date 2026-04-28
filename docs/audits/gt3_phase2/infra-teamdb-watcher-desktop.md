# GT3 Phase 2 Audit — Team DB, Sync, Watcher, Desktop, Server

## Scope

Audit covers the team/server/automation stack for GT3 readiness:

- `teamdb/models.py` — SQLAlchemy 2.0 ORM
- `teamdb/sync_client.py` — push/pull sync with offline SQLite queue
- `teamdb/aggregator.py` — server-side empirical model fitting
- `watcher/monitor.py` — IBT auto-detection
- `watcher/service.py` — orchestration (CarPath → canonical mapping)
- `desktop/app.py`, `desktop/config.py`, `desktop/tray.py` — desktop app
- `server/app.py` + `server/routes/*` — FastAPI server
- `car_model/registry.py` — referenced by `watcher/service.py`

Out of scope: solver chain (covered by other audits), aero parser, output writer.

## Summary table

| # | Severity | Area | Title |
|---|---|---|---|
| F1 | BLOCKER | teamdb/models.py | `CarDefinition` lacks `iracing_car_path`, `bop_version`, `suspension_arch` columns |
| F2 | BLOCKER | teamdb/models.py | `Observation` lacks `suspension_arch` discriminator → cross-arch contamination in aggregator |
| F3 | BLOCKER | teamdb/aggregator.py | `aggregate_observations()` does not partition by `suspension_arch` — pools GTP+GT3 into one model |
| F4 | BLOCKER | car_model/registry.py | `_CAR_REGISTRY` has no GT3 entries — `resolve_car()` returns `None` for every GT3 IBT, so watcher silently drops them as "unknown car" |
| F5 | BLOCKER | watcher/service.py | Car detection uses `car_screen_name` only; ignores `iracing_car_path`. CarPath is the only stable IBT identifier (ScreenName drifts with locale + EVO suffixes) |
| F6 | BLOCKER | server/routes/observations.py | `POST /api/observations` accepts any payload — no `suspension_arch` field, no architecture validation, will accept GT3 obs with `car_class="GTP"` and store them indistinguishably |
| F7 | DEGRADED | server/routes/knowledge.py | `/api/stats` and `/api/knowledge` group by `(car, track)` only; sibling GT3 BoP versions on the same track will overwrite each other in `EmpiricalModel` |
| F8 | DEGRADED | teamdb/models.py | `Observation` has no `bop_version`, `suspension_arch`, or `aggregation_key` — when iRacing pushes a BoP patch, fresh + stale observations co-mingle in fits |
| F9 | DEGRADED | server/database.py | Schema migration path is `Base.metadata.create_all` — adding the new columns will not back-fill existing rows or alter live tables. No Alembic configured. |
| F10 | DEGRADED | teamdb/aggregator.py:104 | `track_key = track.lower().split()[0]` shadows the `track_key()` helper from `car_model/registry.py` — fragile for multi-word GT3 tracks (`"red bull ring"`, `"weathertech raceway laguna seca"`) |
| F11 | DEGRADED | teamdb/aggregator.py:114 | `compute_support_tier()` thresholds (5/15/30) are GTP-tuned — GT3's lack of Step 2 means coverage is reached faster; thresholds should differ per architecture |
| F12 | DEGRADED | teamdb/sync_client.py:282 | `pulled_models` PK is `(car, track)` only — same architecture-collision problem as F7 in the local cache |
| F13 | DEGRADED | desktop/config.py | `car_filter: list[str]` UX undefined for class-level filtering ("show all GT3"); no `class_filter` field |
| F14 | DEGRADED | watcher/monitor.py:108 | `_wait_until_stable` 300 s deadline + 3 s stable window has no special handling for short GT3 practice IBTs |
| F15 | COSMETIC | desktop/app.py | Hardcoded GTP-style support claim absent (good); but no first-run UI to inform user "GT3 is exploratory" — desktop app surfaces no support-tier warning |
| F16 | COSMETIC | tests | No tests for `teamdb/aggregator.py`, no tests for watcher car-detection, no GT3 fixtures for any of the touched modules |
| F17 | COSMETIC | teamdb/models.py:200 | `car_model_json` is per-team — every team re-uploads the canonical car model; should be a global resource, not team-scoped |

## Findings

### F1 — `CarDefinition` schema lacks GT3 metadata columns [BLOCKER]

`teamdb/models.py:178-209` — `CarDefinition` carries only `car_name`, `car_class`, `display_name`, three boolean capability flags, `support_tier`, `observation_count`, and `car_model_json`. None of `iracing_car_path`, `bop_version`, `suspension_arch` are present, despite all three being load-bearing fields on the in-memory `CarModel` (`car_model/cars.py:1728,1736,1743`).

Consequences:

1. Server cannot distinguish two GT3 cars with identical `car_name="bmw_m4_gt3"` but different BoP patches.
2. `resolve_car_from_ibt` on the desktop side cannot fall back to the server's car registry to identify a GT3 IBT it has never seen — the server has no `iracing_car_path` to match against.
3. Cross-architecture aggregation (F3) has no DB-level guardrail.

Required new columns:

```python
# teamdb/models.py — CarDefinition
iracing_car_path: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
bop_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
suspension_arch: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
# values: "gtp_heave_third_torsion_front" | "gtp_heave_third_roll_front" | "gt3_coil_4wheel"
```

Indexes:

```python
__table_args__ = (
    UniqueConstraint("team_id", "car_name", name="uq_car_definitions_team_car"),
    Index("ix_car_definitions_iracing_path", "iracing_car_path"),
    Index("ix_car_definitions_arch", "suspension_arch"),
)
```

### F2 — `Observation` lacks `suspension_arch` discriminator [BLOCKER]

`teamdb/models.py:216-248` — `Observation` has `car`, `car_class`, `track`, but no `suspension_arch`. A row stores `car="bmw"` (GTP) and a row stores `car="bmw_m4_gt3"` — distinguishable today only by the canonical name string. The moment two cars share a canonical (or a misconfigured client uploads the wrong car string), the aggregator pools them silently.

Required new columns:

```python
# teamdb/models.py — Observation
suspension_arch: Mapped[str] = mapped_column(String(48), nullable=False, default="gtp_heave_third_torsion_front")
bop_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
iracing_car_path: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
```

Indexes — extend the existing `(team_id, car, track)` index:

```python
Index("ix_observations_team_arch_track", "team_id", "suspension_arch", "track"),
Index("ix_observations_team_car_arch_track", "team_id", "car", "suspension_arch", "track"),
```

`Delta.delta_json`, `EmpiricalModel`, and `SharedSetup` should also carry `suspension_arch` for the same reason — a `SharedSetup.sto_content` for `bmw_m4_gt3` MUST NOT be applied to the GTP `bmw`.

### F3 — Aggregator pools GTP and GT3 observations [BLOCKER]

`teamdb/aggregator.py:49-121` — `aggregate_observations(observations, car, track)` accepts a flat list and feeds every observation into `learner.empirical_models.fit_models()`. There is no architecture filter. The caller (presumably a server-side trigger after observation upload) is expected to pre-filter by `car`, but `car="bmw"` and `car="bmw_m4_gt3"` are different strings, so this happens to work today only because GT3 cars use distinct canonicals.

The hazard is two-fold:
1. A misconfigured client could upload a GT3 observation with `car="bmw"` (e.g., a manual override or an old desktop build); aggregator would silently fold it into GTP.
2. The fitter `learner.empirical_models.fit_models()` regression features assume GTP physics (heave + third); a GT3 observation will have null heave/third channels, which the regression will treat as 0 — wrong.

Fix shape:

```python
def aggregate_observations(observations, car, track, *, suspension_arch: str | None = None):
    if suspension_arch is None:
        archs = {o.get("suspension_arch") for o in observations}
        if len(archs) > 1:
            raise ValueError(f"Mixed suspension_arch values: {archs}")
        suspension_arch = archs.pop() if archs else "gtp_heave_third_torsion_front"
    # … filter to the requested arch only …
    filtered = [o for o in observations if o.get("suspension_arch") == suspension_arch]
    # dispatch to arch-specific fitter
    if suspension_arch == "gt3_coil_4wheel":
        return _aggregate_gt3(filtered, car, track)
    return _aggregate_gtp(filtered, car, track)
```

`server/routes/knowledge.py` should pass `suspension_arch` through to the aggregator and key `EmpiricalModel` rows on `(team_id, car, track, suspension_arch, bop_version)` instead of `(team_id, car, track)`.

### F4 — `car_model/registry.py` has no GT3 entries [BLOCKER]

`car_model/registry.py:54-60` — `_CAR_REGISTRY` lists only the 5 GTP cars. `car_model/cars.py:3196-3577` defines `BMW_M4_GT3`, `ASTON_MARTIN_VANTAGE_GT3`, `PORSCHE_992_GT3R` with `iracing_car_path` populated, but the registry has no awareness of them. `resolve_car("BMW M4 GT3 EVO")` returns `None`, so `watcher/service.py:156` sets `car_canonical = None` and `_handle_new_ibt` falls through the unknown-car branch.

Two fixes are needed in tandem:

1. Add GT3 entries to `_CAR_REGISTRY`. The registry currently uses `aero_folder` keyed on canonical (e.g., `"bmw"`). For GT3 we need an extra column or to overload `aero_folder` to the canonical (it already matches for GT3).
2. Add an `_BY_IRACING_PATH` index so `resolve_car_from_ibt` can prefer `CarPath` over `CarScreenName` (F5).

```python
# car_model/registry.py
@dataclass(frozen=True)
class CarIdentity:
    canonical: str
    display_name: str
    screen_name: str
    sto_id: str
    aero_folder: str
    iracing_car_path: str = ""  # NEW — for GT3 IBT resolution
    suspension_arch: str = "gtp_heave_third_torsion_front"  # NEW

_CAR_REGISTRY: list[CarIdentity] = [
    # … existing GTP rows, all with suspension_arch="gtp_…" …
    CarIdentity("bmw_m4_gt3", "BMW M4 GT3 EVO", "BMW M4 GT3 EVO", "bmwm4gt3",
                "bmw_m4_gt3", iracing_car_path="bmwm4gt3",
                suspension_arch="gt3_coil_4wheel"),
    CarIdentity("aston_martin_vantage_gt3", "Aston Martin Vantage GT3 EVO",
                "Aston Martin Vantage GT3 EVO", "amvantageevogt3",
                "aston_martin_vantage_gt3", iracing_car_path="amvantageevogt3",
                suspension_arch="gt3_coil_4wheel"),
    CarIdentity("porsche_992_gt3r", "Porsche 911 GT3 R (992)",
                "Porsche 911 GT3 R", "porsche992rgt3",
                "porsche_992_gt3r", iracing_car_path="porsche992rgt3",
                suspension_arch="gt3_coil_4wheel"),
    # Stubs for the remaining 7 cars per docs/gt3_per_car_spec.md:
    # acuransxgt3evo22, audir8gt3evo2, corvettez06gt3r, lambohuracangt3evo,
    # mclaren720sgt3evo, mercedesamggt3evo, mustanggt3
]
```

Verification — the canonical CarPath strings need IBT confirmation. Per the GT3 docs in this repo, `bmwm4gt3`, `amvantageevogt3`, and `porsche992rgt3` are confirmed from real session_info dumps. The remaining 7 are PENDING — flag them as such in the registry (e.g., `iracing_car_path="acuransxgt3evo22 PENDING_IBT"`) until a real IBT is captured, OR treat them as best-guess and refine on first detection.

### F5 — `watcher/service.py` ignores CarPath [BLOCKER]

`watcher/service.py:42-56,156` — `_detect_car_and_track()` returns only `car_info["car"]` (CarScreenName) and feeds it into `resolve_car()`. `IBTFile.car_info()` exposes `CarPath` from `DriverInfo.Drivers[me].CarPath` but `_detect_car_and_track` does not extract it.

CarPath is the stable identifier (it does not localize, does not get an EVO suffix when iRacing renames the screen name). Watcher should prefer it.

```python
# watcher/service.py
def _detect_car_and_track(ibt_path: Path) -> tuple[str, str, str, str]:
    from track_model.ibt_parser import IBTFile
    ibt = IBTFile(str(ibt_path))
    car = ibt.car_info()
    track = ibt.track_info()
    return (
        car.get("car", "Unknown"),         # screen name (display)
        car.get("car_path", ""),           # NEW: stable iRacing tag
        track.get("track_name", "Unknown"),
        car.get("driver", "Unknown"),
    )

# … in _handle_new_ibt:
car_screen, car_path, track_name, driver_name = _detect_car_and_track(ibt_path)
identity = resolve_car_by_path(car_path) or resolve_car(car_screen)
```

Add a sibling resolver in the registry:

```python
def resolve_car_by_path(car_path: str) -> CarIdentity | None:
    return _BY_IRACING_PATH.get(car_path.lower()) if car_path else None
```

(This requires `IBTFile.car_info()` to expose `car_path`; verify `track_model/ibt_parser.py` does so. If it doesn't, that's a separate fix — out of scope here but flag it.)

### F6 — `POST /api/observations` accepts any architecture [BLOCKER]

`server/routes/observations.py:25-116` — `ObservationCreateRequest` has `session_id`, `car`, `car_class`, `track`, `best_lap_time_s`, `lap_count`, `observation_json`. Missing `suspension_arch`, `bop_version`, `iracing_car_path`. Server accepts the upload, persists the row, fires `aggregate_observations()` (eventually) — the misclassified observation has already polluted aggregation by then.

Required schema additions:

```python
class ObservationCreateRequest(BaseModel):
    session_id: str
    car: str
    car_class: Optional[str] = None
    suspension_arch: str  # REQUIRED — see F2
    bop_version: Optional[str] = None
    iracing_car_path: Optional[str] = None
    track: str
    best_lap_time_s: Optional[float] = None
    lap_count: Optional[int] = None
    observation_json: dict[str, Any]
```

Server-side validation: when `CarDefinition` exists for `(team_id, car_name=body.car)`, assert `body.suspension_arch == car_def.suspension_arch`. Reject 400 if mismatch (data-corruption signal).

### F7 — Empirical models keyed only on `(car, track)` [DEGRADED]

`teamdb/models.py:284-308` — `EmpiricalModel.__table_args__` has `UniqueConstraint("team_id", "car", "track")`. When iRacing patches GT3 BoP mid-season (`2026s2_p3` → `2026s2_p4`), fresh observations should NOT update the same model row; they should produce a new model that supersedes the old one (or co-exists for back-test).

Schema change:

```python
__table_args__ = (
    UniqueConstraint("team_id", "car", "track", "bop_version",
                     name="uq_empirical_models_team_car_track_bop"),
)
# also add:
suspension_arch: Mapped[str] = mapped_column(String(48), nullable=False)
bop_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
```

Same applies to `GlobalCarModel.__table_args__` (`teamdb/models.py:317-319`) and `Leaderboard.__table_args__` (`teamdb/models.py:437-442`) — keying on bare `car` will conflate two BoP versions of the same GT3 car.

### F8 — Observation rows have no aggregation key [DEGRADED]

Same root cause as F2/F7. Once `suspension_arch` and `bop_version` are added, the natural aggregation key for any model is `(team_id, car, track, suspension_arch, bop_version)`. Without `bop_version` on `Observation`, the aggregator cannot honor the new `EmpiricalModel` uniqueness key.

### F9 — No migration plan; `create_all` does not alter [DEGRADED]

`server/database.py:26-29` — `init_db()` calls `Base.metadata.create_all`. SQLAlchemy's `create_all` only creates missing tables; it does not add columns to existing tables, does not back-fill defaults, does not rebuild indexes.

Migration strategy options:

**Option A — adopt Alembic (recommended for production):**
1. `pip install alembic` (add to `server/requirements-server.txt`).
2. `alembic init alembic` at repo root; configure `alembic.ini` to point at `server.database.engine`.
3. `alembic revision --autogenerate -m "gt3_phase2_arch_columns"` — produces a migration that adds the new nullable columns to `car_definitions`, `observations`, `deltas`, `empirical_models`, `global_car_models`, `shared_setups`, `leaderboard`.
4. Back-fill: `UPDATE observations SET suspension_arch = 'gtp_heave_third_torsion_front' WHERE suspension_arch IS NULL;` then `ALTER COLUMN … SET NOT NULL`.
5. New uniqueness constraints: drop+recreate at table level.

**Option B — raw SQL on Cloud Run (lighter, no new dep):**
1. Author `migrations/0001_gt3_phase2.sql`:
   ```sql
   -- Add new optional columns
   ALTER TABLE car_definitions ADD COLUMN iracing_car_path VARCHAR(64);
   ALTER TABLE car_definitions ADD COLUMN bop_version VARCHAR(32);
   ALTER TABLE car_definitions ADD COLUMN suspension_arch VARCHAR(48);

   ALTER TABLE observations ADD COLUMN suspension_arch VARCHAR(48);
   ALTER TABLE observations ADD COLUMN bop_version VARCHAR(32);
   ALTER TABLE observations ADD COLUMN iracing_car_path VARCHAR(64);

   ALTER TABLE deltas ADD COLUMN suspension_arch VARCHAR(48);
   ALTER TABLE empirical_models ADD COLUMN suspension_arch VARCHAR(48);
   ALTER TABLE empirical_models ADD COLUMN bop_version VARCHAR(32);
   ALTER TABLE global_car_models ADD COLUMN suspension_arch VARCHAR(48);
   ALTER TABLE shared_setups ADD COLUMN suspension_arch VARCHAR(48);
   ALTER TABLE shared_setups ADD COLUMN bop_version VARCHAR(32);
   ALTER TABLE leaderboard ADD COLUMN bop_version VARCHAR(32);

   -- Back-fill GTP rows
   UPDATE observations SET suspension_arch = 'gtp_heave_third_torsion_front' WHERE suspension_arch IS NULL;
   UPDATE deltas SET suspension_arch = 'gtp_heave_third_torsion_front' WHERE suspension_arch IS NULL;
   UPDATE empirical_models SET suspension_arch = 'gtp_heave_third_torsion_front' WHERE suspension_arch IS NULL;

   -- Set NOT NULL after backfill
   ALTER TABLE observations ALTER COLUMN suspension_arch SET NOT NULL;
   ALTER TABLE empirical_models ALTER COLUMN suspension_arch SET NOT NULL;

   -- Replace uniqueness constraints
   ALTER TABLE empirical_models DROP CONSTRAINT uq_empirical_models_team_car_track;
   ALTER TABLE empirical_models ADD CONSTRAINT uq_empirical_models_team_car_track_bop
       UNIQUE (team_id, car, track, bop_version);

   -- New indexes
   CREATE INDEX ix_observations_team_arch_track ON observations (team_id, suspension_arch, track);
   CREATE INDEX ix_car_definitions_iracing_path ON car_definitions (iracing_car_path);
   ```
2. Run via `psql` against Cloud SQL before the new server image is rolled out.

Option A is preferred for long-term schema stewardship; Option B is acceptable as a one-shot for the GT3 Phase 2 cutover.

### F10 — `track_key` shadowing in aggregator [DEGRADED]

`teamdb/aggregator.py:104` — `track_key = track.lower().split()[0]` is a local variable that masks the `track_key()` helper exported from `car_model/registry.py:181`. For `"Sebring International Raceway"` it works (`"sebring"`) but for `"Red Bull Ring"` it returns `"red"` — the registry's alias map is bypassed. GT3 candidate tracks include Red Bull Ring (Spielberg), Watkins Glen, Mount Panorama, Brands Hatch — all multi-word.

Fix: import and use `track_key` from the registry:

```python
from car_model.registry import track_key as _track_key
# …
track_short = _track_key(track)
```

### F11 — Support tier thresholds are GTP-tuned [DEGRADED]

`teamdb/aggregator.py:20-46` — `_TIER_THRESHOLDS` are hardcoded `{exploratory: 5, partial: 15, calibrated: 30}`. These were calibrated against GTP Step 1-6 with 6 dependent steps. GT3 has no Step 2; effective number of independent fits is lower; coverage threshold should differ.

Lighter fix: parameterize per architecture in `teamdb/aggregator.py`:

```python
_TIER_THRESHOLDS = {
    "gtp_heave_third_torsion_front": {"exploratory": 5, "partial": 15, "calibrated": 30},
    "gtp_heave_third_roll_front":   {"exploratory": 5, "partial": 15, "calibrated": 30},
    "gt3_coil_4wheel":              {"exploratory": 4, "partial": 10, "calibrated": 20},
}
def compute_support_tier(observation_count, suspension_arch, model_stability=None): …
```

### F12 — Local `pulled_models` cache uses `(car, track)` PK [DEGRADED]

`teamdb/sync_client.py:120-128,277-295` — `pulled_models` table PK is `(car, track)`. Same architecture-collision problem as F7. When the desktop app pulls models for both `bmw` (GTP) and `bmw_m4_gt3` it works because canonicals differ — but if a future BoP-tracked GTP model arrives with the same `(car, track)`, it overwrites the older one with no provenance.

Fix:

```sql
CREATE TABLE IF NOT EXISTS pulled_models (
    car TEXT NOT NULL,
    track TEXT NOT NULL,
    suspension_arch TEXT NOT NULL DEFAULT 'gtp_heave_third_torsion_front',
    bop_version TEXT,
    model_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (car, track, suspension_arch, bop_version)
)
```

`get_team_model(car, track)` becomes `get_team_model(car, track, suspension_arch, bop_version)`. Caller must pass the matching arch — the solver's `CarModel.suspension_arch` is the source of truth.

### F13 — `car_filter` UX undefined for class-level filtering [DEGRADED]

`desktop/config.py:59` — `car_filter: list[str]` filters by canonical car name. Users who want "all GT3 sessions" must enumerate every GT3 canonical (10 strings). Add `class_filter`:

```python
class_filter: list[str] = field(default_factory=list)  # ["GTP", "GT3"]
```

`watcher/service.py:160-162` should test both:

```python
if self._car_filter and car_canonical not in self._car_filter:
    return None
if self._class_filter and identity.car_class not in self._class_filter:
    return None
```

(Requires `CarIdentity.car_class` — adds another column to the registry.)

### F14 — Stability window not tuned for short GT3 sessions [DEGRADED]

`watcher/monitor.py:108-128` — `_wait_until_stable` uses 300 s timeout + 3 s no-growth. GT3 hot-lap practice IBTs are short (90 s sessions are common). The 3 s window is fine but the 300 s upper bound is a no-op for short sessions. Cosmetic-ish — but flag for consistency: if a session is genuinely 30 s (single timed lap), the 3 s stable window can mis-fire if iRacing flushes in bursts.

No code change recommended — flagging only. If false-positive ingests are observed in GT3 sprint sessions, lengthen `_STABLE_WAIT_S` to 5 s.

### F15 — No GT3 support-tier UX in desktop [COSMETIC]

`desktop/app.py`, `desktop/tray.py` — surface no per-car support tier. A user who imports a GT3 IBT will see "Ingested" in the tray notification with no indication that GT3 is `exploratory` and the team server won't have a fitted model to pull. Consider adding `result.support_tier` to `IngestResult` (`watcher/service.py:26-39`) and surfacing in tray notifications.

### F16 — No GT3 fixtures in tests [COSMETIC]

`tests/test_sync_client.py` is the only `teamdb`/server test in the tree. It uses synthetic dicts, no GT3 fields. There are zero tests for `teamdb/aggregator.py`, `watcher/service.py` car detection, `desktop/app.py`, or `server/routes/observations.py`.

Required test additions for the GT3 Phase 2 implementation PR (new files to be created):

- **test_aggregator_arch_isolation** — feed mixed GTP+GT3 observations, assert aggregator raises or partitions, never co-fits.
- **test_watcher_carpath_resolution** — feed an IBT with `CarPath="bmwm4gt3"` and `CarScreenName="BMW M4 GT3 EVO"`, assert canonical resolves to `bmw_m4_gt3` not `bmw`.
- **test_observations_endpoint_arch_validation** — POST observation with `suspension_arch="gt3_coil_4wheel"` for a `CarDefinition` that has `suspension_arch="gtp_…"` → assert 400.

### F17 — `car_model_json` is per-team [COSMETIC]

`teamdb/models.py:200` — every team uploads its own copy of the canonical `car_model_json`. For GT3 Phase 2 with 11 cars × N teams, this is wasteful. A shared `global_car_definitions` table keyed on `(canonical_name, bop_version)` would let all teams pull the same definition. Out of scope for Phase 2 cutover — flag for Phase 3.

## Risk summary

| Risk | Likelihood | Impact | Driver |
|---|---|---|---|
| GT3 IBT silently dropped as "unknown car" | **Certain** | Total feature loss for GT3 users | F4, F5 |
| Cross-architecture aggregation corrupts GTP empirical models | **Certain** if any GT3 obs uploaded | Wrong setup recommendations for GTP users | F2, F3, F6 |
| BoP patch silently overwrites old empirical models with no audit | High (every season) | Lost regression data; no back-test | F7, F8 |
| Live DB migration applied via `create_all` no-ops | **Certain** | Phase 2 ships, columns missing in prod | F9 |
| Multi-word GT3 track names fail to aggregate | High (Red Bull Ring, Laguna Seca) | Per-track models never fit | F10 |

## Effort estimate

| Item | Files | Effort |
|---|---|---|
| F1 + F2 + F7 schema columns | `teamdb/models.py` | 0.5 day |
| F3 aggregator partition + arch dispatch | `teamdb/aggregator.py` | 1.0 day (incl. GT3 fitter stub) |
| F4 + F5 registry + watcher CarPath | `car_model/registry.py`, `watcher/service.py`, `track_model/ibt_parser.py` (verify CarPath exposed) | 1.0 day |
| F6 endpoint validation + Pydantic schemas | `server/routes/observations.py`, `server/routes/setups.py`, `server/routes/knowledge.py` | 0.5 day |
| F9 migration script (Option B) + Cloud SQL run | `migrations/0001_gt3_phase2.sql`, deployment | 0.5 day |
| F10 track_key fix | `teamdb/aggregator.py` | 0.1 day |
| F11 per-arch tier thresholds | `teamdb/aggregator.py` | 0.2 day |
| F12 sync_client cache PK | `teamdb/sync_client.py` | 0.3 day |
| F13 class_filter | `desktop/config.py`, `watcher/service.py`, registry | 0.3 day |
| F16 test coverage | `tests/` | 1.0 day |
| **Total (BLOCKER + DEGRADED)** | | **5.4 days** |

COSMETIC items (F14, F15, F17) deferred to a follow-up.

## Dependencies

This audit's fixes block on:

- **`car_model/cars.py` GT3 stubs being filled** — the registry needs a real `iracing_car_path` for all 11 GT3 cars; currently only 3 are confirmed. The Phase 2 implementation PR should either:
  - block until all 11 IBTs are captured (slow), or
  - ship with `PENDING_IBT` placeholders that auto-update on first ingest of an unknown CarPath.

- **`track_model/ibt_parser.py` exposing `CarPath`** — F5 assumes `IBTFile.car_info()["car_path"]` exists. Verify this in the GT3 Phase 1 audit (out of scope here). If it doesn't, that's a 0.2-day fix at the IBT parser layer.

- **`learner.empirical_models.fit_models()` arch-awareness** — F3's `_aggregate_gt3` path assumes there's a GT3-aware fitter. Today `fit_models` is GTP-shaped (heave/third features). The GT3 fitter is a separate audit/implementation item; this audit lists it as a dependency, not a deliverable.

- **GT3 aggregator-tier calibration** — F11's GT3 thresholds (4/10/20) are guesses. The calibration audit should ratify or revise after a few teams have produced real GT3 sessions.

## Migration execution checklist (for the implementation PR)

1. Add columns + indexes to `teamdb/models.py` (F1, F2, F7, F8).
2. Write `migrations/0001_gt3_phase2.sql` (F9 Option B).
3. Apply migration to Cloud SQL **before** deploying new server image.
4. Update `server/routes/observations.py` Pydantic + validation (F6).
5. Update `teamdb/aggregator.py` arch partitioning + `track_key` import (F3, F10, F11).
6. Update `teamdb/sync_client.py` `pulled_models` PK (F12) — note: SQLite ALTER TABLE has limits; recommend drop-and-recreate the local cache on first launch of the new desktop client.
7. Update `car_model/registry.py` GT3 entries + `_BY_IRACING_PATH` (F4).
8. Update `watcher/service.py` to use CarPath (F5).
9. Bump `desktop/config.py` schema version; on load detect old config and migrate filters (F13).
10. Add tests (F16).
11. Smoke-test: ingest a real GT3 IBT in dev environment, confirm `suspension_arch="gt3_coil_4wheel"` lands in DB and `EmpiricalModel` row keys correctly.
