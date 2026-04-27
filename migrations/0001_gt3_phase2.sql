-- migrations/0001_gt3_phase2.sql
-- GT3 Phase 2 schema additions (W8.1).
--
-- Adds the columns the aggregator + watcher need to keep GT3 observations
-- isolated from GTP fits and to track BoP-version provenance:
--
--   car_definitions
--     + iracing_car_path      VARCHAR(64)   -- stable iRacing CarPath
--     + bop_version           VARCHAR(32)   -- season BoP stamp
--     + suspension_arch       VARCHAR(48)   -- "gtp_..." | "gt3_coil_4wheel"
--
--   observations
--     + suspension_arch       VARCHAR(48)   -- NOT NULL after backfill
--     + bop_version           VARCHAR(32)
--     + iracing_car_path      VARCHAR(64)
--
-- Existing GTP rows (5 GTP cars, all observations uploaded prior to this
-- migration) are backfilled with `gtp_heave_third_torsion_front` (the
-- canonical BMW/Ferrari/Cadillac/Acura architecture). Porsche 963 uses
-- `gtp_heave_third_roll_front` and is patched explicitly. The Postgres
-- DEFAULT clause guards new INSERTs that omit the column.
--
-- This is the "Option B" raw-SQL path called out in the audit
-- (docs/audits/gt3_phase2/infra-teamdb-watcher-desktop.md F9). The repo
-- does not use Alembic; apply this script with `psql` against Cloud SQL
-- before rolling out the new server image.
--
-- Idempotent guards (IF NOT EXISTS) are used so the migration can be run
-- twice safely if a deployment is rolled back.

BEGIN;

-- ─── car_definitions ─────────────────────────────────────────────────────
ALTER TABLE car_definitions
    ADD COLUMN IF NOT EXISTS iracing_car_path VARCHAR(64);
ALTER TABLE car_definitions
    ADD COLUMN IF NOT EXISTS bop_version VARCHAR(32);
ALTER TABLE car_definitions
    ADD COLUMN IF NOT EXISTS suspension_arch VARCHAR(48);

CREATE INDEX IF NOT EXISTS ix_car_definitions_iracing_path
    ON car_definitions (iracing_car_path);
CREATE INDEX IF NOT EXISTS ix_car_definitions_arch
    ON car_definitions (suspension_arch);

-- Backfill existing GTP rows. The four torsion-front GTP cars all share
-- the same architecture; Porsche 963 is the lone roll-front GTP car.
UPDATE car_definitions
   SET suspension_arch = 'gtp_heave_third_torsion_front'
 WHERE suspension_arch IS NULL
   AND car_name IN ('bmw', 'ferrari', 'cadillac', 'acura');

UPDATE car_definitions
   SET suspension_arch = 'gtp_heave_third_roll_front'
 WHERE suspension_arch IS NULL
   AND car_name = 'porsche';

-- Any rows still NULL after the explicit backfill (e.g. unknown legacy
-- canonicals) get the safe GTP-torsion default; cross-arch contamination
-- of those rows is impossible because the aggregator filters by arch
-- anyway, so the only consequence is "their fits route through the GTP
-- partition".
UPDATE car_definitions
   SET suspension_arch = 'gtp_heave_third_torsion_front'
 WHERE suspension_arch IS NULL;

-- ─── observations ────────────────────────────────────────────────────────
-- DEFAULT clause backfills existing rows during the ADD COLUMN call.
ALTER TABLE observations
    ADD COLUMN IF NOT EXISTS suspension_arch VARCHAR(48)
    NOT NULL DEFAULT 'gtp_heave_third_torsion_front';
ALTER TABLE observations
    ADD COLUMN IF NOT EXISTS bop_version VARCHAR(32);
ALTER TABLE observations
    ADD COLUMN IF NOT EXISTS iracing_car_path VARCHAR(64);

-- Patch Porsche 963 GTP observations onto the roll-front partition. The
-- guard on `suspension_arch` skips rows that are already arch-tagged
-- (re-running the migration is a no-op).
UPDATE observations
   SET suspension_arch = 'gtp_heave_third_roll_front'
 WHERE car = 'porsche'
   AND suspension_arch = 'gtp_heave_third_torsion_front';

CREATE INDEX IF NOT EXISTS ix_observations_team_arch_track
    ON observations (team_id, suspension_arch, track);

COMMIT;
