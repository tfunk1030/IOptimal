# Garage61/VRS Competitive Gap Analysis (IOptimal)

_Date: 2026-03-28 (UTC)_

## Executive summary

IOptimal already has a strong **solver + telemetry ingestion + team sync** foundation, but it is still missing several product capabilities that make Garage61/VRS sticky for day-to-day team usage:

1. **Polished telemetry UX and comparisons at scale** (segments, overlays, rapid filtering, cross-driver browsing).
2. **Tiered content and team workflows** (coaching loops, curated setup packs, stronger social sharing loops).
3. **Operational reliability telemetry** (sync health dashboards, queue visibility, retry/error transparency).

The fastest practical strategy is:
- Keep IOptimal's engineering advantage (physics + legality + setup synthesis).
- Add product loops that copy the retention mechanics of VRS/Garage61: easier compare/browse/share, stable sync, and better trust surfaces.

## External product research snapshot

### VRS (public pricing/features page)
Observed from `https://virtualracingschool.com/pricing/` on 2026-03-28:

- Free, Dedicated, Competitive plans are explicitly listed.
- Published pricing includes **$4.99/mo** (Dedicated) and **$9.99/mo** (Competitive) in US pricing blocks.
- Feature matrix highlights:
  - iRacing integration and cloud lap storage in all tiers.
  - Advanced telemetry views (tyres, wheel slip, ride height) in higher tier.
  - Datapacks + downloadable setup/replay/ghost files.
  - Team access limits for lower tiers, broader access in higher tiers.
  - Coaching add-ons (group and 1:1).

### Garage61 (public API/community signal)
Direct Garage61 pages were not reliably machine-readable in this environment, so this snapshot uses publicly visible integration material:

- PyPI package `garage61api` (released 2025-01-14) describes a wrapper around Garage61 endpoints.
- Exposed endpoint categories in package docs include:
  - user/account stats,
  - teams,
  - cars/platforms/tracks,
  - laps and CSV telemetry export.

Inference: Garage61 emphasizes **telemetry openness and automation-friendly data access**, while VRS emphasizes **coaching/datapack workflows**.

## Codebase implementation map (what already exists)

### Strong foundations

- **Team sync architecture exists** with offline queue persistence, push/pull loops, and local model cache (`teamdb/sync_client.py`).
- **Team DB model richness exists**: members, setups, ratings, activities, empirical model primitives (`teamdb/models.py`).
- **Web app already has team/knowledge/telemetry pages** (`webapp/app.py`, `webapp/templates/*`).
- **Solver depth is high**: candidate search/ranker, legality engine, multi-step tuning chain (`solver/*`).

### Product-positioning edge vs competitors

- IOptimal can eventually win on **"actionable setup recommendations"** versus pure telemetry viewers.
- Existing pipeline already links telemetry -> diagnosis -> setup synthesis, which is the right moat direction.

## Bugs and reliability issues found during this pass

## 1) Push-loop backoff behavior did not match docstring intent (fixed)

Problem:
- `_push_loop` promised exponential backoff on failure.
- But HTTP/network failures inside `_push_pending` mostly returned `0` without raising; loop treated this similarly to "nothing to push".

Impact:
- Under server/API outages, client may retry too aggressively at base interval.

Fix applied:
- Added `_last_push_failed` state and set it from `_push_pending` on HTTP/network/unknown-type failures.
- `_push_loop` now doubles wait interval on failed push attempts up to max cap.
- `status.last_error` now gets populated on push failure and cleared on success.

## 2) Queue status accounting skipped setup payloads (fixed)

Problem:
- `queue_setup` inserted queue rows but did not increment queue counter.

Impact:
- UI/diagnostics could under-report pending items.

Fix applied:
- `queue_setup` now increments `queued_observations` counter.
- Post-push queue count now recomputes from DB (`_count_pending_queue_items`) for correctness.

## 3) Environment-level test fragility (observed, not fixed here)

Observed test collection failures due missing dependencies (`fastapi`, `numpy`, `httpx`, internal modules in this runtime). This is not necessarily a repo bug, but it blocks CI-like validation in minimal environments.

Recommendation:
- Add a lightweight smoke test lane and dependency markers (core vs full stack).

## Deep enhancement plan (prioritized)

## P0 (2-4 weeks): Reliability + trust surfaces

1. **Sync observability panel**
   - Queue depth over time, last N errors, retry/backoff state, per-endpoint success rate.
2. **Deterministic offline replay harness**
   - Local tool that replays queued payloads against mocked server responses (200/409/500/timeout).
3. **Upload/session provenance**
   - Every recommendation should show source sessions + model version hash.

## P1 (4-8 weeks): Garage61 parity loops

1. **Lap compare UX upgrade**
   - Multi-driver filtering, corner/sector bookmarks, one-click "show setup diff for this lap pair".
2. **Public-ish API surface for team integrations**
   - Token-scoped read API for laps, sessions, setup deltas, leaderboard slices.
3. **CSV/Parquet export workers**
   - Async exports for telemetry slices and aggregated metrics.

## P2 (8-12 weeks): VRS-style value capture

1. **Team datapack concept**
   - Curated setup packs by car/track/profile with quality score + confidence tier.
2. **Coach workflow primitives**
   - Annotated laps, assignment queue ("review this driver/session"), improvement tasks.
3. **Usage-aware tiering**
   - Internal usage quotas + premium features for large teams (if commercializing).

## P3 (12+ weeks): Differentiator moat

1. **Closed-loop recommendation scoring**
   - Measure adoption and lap-time outcomes after setup changes.
2. **Counterfactual simulator**
   - "If we changed X only, expected delta in sectors A/B/C".
3. **Cross-car transfer learning**
   - Reuse model priors across similar platforms while keeping per-car calibration boundaries.

## Suggested technical architecture additions

- Introduce a **`telemetry_index` service layer** (materialized views / precomputed summaries) to avoid expensive ad-hoc query paths.
- Add **job queue abstraction** for heavy tasks (exports, model rebuilds, comparison snapshots).
- Add **schema versioning + migration policy** for observation payloads and setup objects.
- Add **contract tests** for sync client/server payload shapes and error semantics.

## Success metrics to track

- Recommendation acceptance rate (% runs where suggested setup is exported/applied).
- Post-change lap delta distribution (median and tail).
- Sync SLO (P95 push latency, failed push rate, queue recovery time after outage).
- Team engagement (weekly active drivers, comparisons per driver, shared setup reuse rate).

## Notes on research quality

- VRS findings are direct from public pricing page at crawl time (2026-03-28).
- Garage61 direct pages were not consistently crawlable from this environment; Garage61 API capabilities were inferred from a public Python wrapper description and should be confirmed against official Garage61 docs before product commitments.

## Addendum — subagent-style solver validation pass (2026-03-28)

Parallel audit findings and improvements:

1. **Sync dependency hard-fail risk**
   - `teamdb/sync_client.py` imported `httpx` at module import time.
   - In minimal runtimes, this prevented test/import of sync logic.
   - Improvement implemented: optional import fallback + explicit user-facing status error when dependency is missing.

2. **Bayesian solver numerical stability risk**
   - `solver/bayesian_optimizer.py` used direct `np.linalg.inv(K)` in GP fit.
   - With duplicate / near-duplicate points (common after step snapping), kernel matrix can be ill-conditioned or singular.
   - Improvement implemented: progressive jitter escalation + pseudo-inverse fallback.

3. **Validation outcome**
   - Sync client tests now pass in minimal env using stubs.
   - Bayesian numeric test added and auto-skips when scientific stack is missing.
