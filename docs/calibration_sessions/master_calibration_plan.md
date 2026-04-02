# iOptimal — Master Calibration Plan
**All 5 GTP Cars | Batched Calibration**
*Last updated: 2026-04-02 05:05 UTC — Rewritten to batch multiple parameters per stint*

---

## Philosophy: Batch What's Separable

Old plan: 1 variable per stint. Clean but takes forever.

New plan: **Batch parameters that affect different telemetry channels.** The learner can separate their effects because each parameter has a distinct telemetry fingerprint:

| Parameter Group | Primary Telemetry Signal | Can Batch With |
|----------------|------------------------|----------------|
| Front LS comp | front_shock_oscillation, braking_pitch | Rear springs, camber |
| Front HS comp | front_shock_vel_p99 | Rear springs, camber |
| Front heave idx | front_rh_std, front_excursion | Rear dampers, camber |
| Rear heave idx | rear_rh_std, rear_excursion | Front dampers, camber |
| Front torsion OD | LLTD, front_wheel_rate | Rear dampers |
| Rear torsion OD | LLTD, rear_wheel_rate | Front dampers |
| Front camber | tyre_temp_spread (inner-outer) | Dampers, springs |
| Rear camber | tyre_temp_spread (inner-outer) | Dampers, springs |
| Wing | DF_balance, drag, top_speed | Nothing (affects everything) |

**Rules:**
- ✅ Change front dampers + rear springs in same stint (different channels)
- ✅ Change front camber + rear damper (different channels)
- ✅ Change front heave + rear camber (different channels)
- ❌ Don't change front heave + front torsion in same stint (both affect front RH)
- ❌ Don't change front LS + front HS in same stint (both affect front shock)
- ❌ Don't change wing + anything else (wing affects everything)
- **Pushrod rule still applies:** if you change a spring index or torsion OD, adjust pushrod to restore static RH ≥30mm before going out.

### After Every Stint — Run This
```bash
cd C:\Users\VYRAL\IOptimal
python3 -m pipeline.produce --car ferrari --ibt "ibtfiles\YOUR_FILE.ibt" --mode safe --delta-card --top-n 1

# Find newest IBT
dir /b /o-d ibtfiles\*.ibt | head 3
```

### Dependency Rules
| If you change | You MUST also adjust | Why |
|--------------|---------------------|-----|
| Heave spring index | Front/rear **pushrod** → restore static RH | Stiffer spring = less deflection = car sits higher |
| Torsion bar OD | **Torsion bar turns** → restore static RH | Larger OD = stiffer corner spring = RH rises |
| Pushrod only | Nothing — direct RH control | ✓ |
| Camber | Verify toe still in legal range | Minor geometry coupling |
| Any spring change | Verify garage shows **≥30mm** front + rear static RH | Legal minimum |

---

## Calibration Status

| Car | Sessions | Dampers | Springs | Aero | Geometry | Score |
|-----|----------|---------|---------|------|----------|-------|
| BMW | 102 | ✅ | ✅ | ✅ | ✅ | 8.5/10 |
| Ferrari | 24 | 🔄 LS done, HS started | ✅ heave/torsion validated | ✅ wing=17 | partial (camber at limit) | 6.5/10 |
| Acura | 5 | ❌ | ❌ | ❌ | ❌ | 2.5/10 |
| Cadillac | 4 | ❌ | ❌ | ❌ | ❌ | 3.0/10 |
| Porsche | 2 | ❌ (verify IBT parse first) | ❌ | ❌ | ❌ | 1.5/10 |

---

# CAR 1: FERRARI 499P
**Track:** Hockenheimring GP (24 sessions)
**Calibration score:** 6.5/10

## Baseline Setup (best observed: 87.575s)
| Parameter | Value | Status |
|-----------|-------|--------|
| Wing | 17 | ✅ VALIDATED |
| Front heave index | 5 | ✅ VALIDATED |
| Rear heave index | 8 | ✅ VALIDATED |
| Front heave perch | –6.5 mm | ✅ VALIDATED |
| Rear heave perch | –104.0 mm | ✅ VALIDATED |
| Front torsion bar index | 2 | ✅ VALIDATED |
| Rear torsion bar index | 1 | ✅ VALIDATED |
| Front torsion bar turns | 0.100 turns | ✅ VALIDATED |
| Rear torsion bar turns | 0.048 turns | ✅ VALIDATED |
| FARB | B / blade 1 | ✅ VALIDATED |
| RARB | C / blade 1 | ✅ VALIDATED |
| Front pushrod delta | +2.0 mm | ✅ VALIDATED |
| Rear pushrod delta | +18.0 mm | ✅ VALIDATED |
| Static front RH | 30.1 mm | ✅ VALIDATED |
| Static rear RH | 47.5 mm | ✅ VALIDATED |
| Front camber | –2.9° (at legal limit) | ✅ VALIDATED |
| Rear camber | –1.8° | ✅ VALIDATED |
| Front toe | –0.5 mm | ✅ VALIDATED |
| Rear toe | 0.0 mm | ✅ VALIDATED |
| Corner weights | F: 2669 N × 2, R: 2938 N × 2 | ✅ VALIDATED |
| Fuel | 58.0 L | ✅ VALIDATED |
| Front torsion k_bar | 142 N/mm at idx=2 | ✅ CALIBRATED |
| Rear torsion k_bar | 173 N/mm at idx=1 | ✅ CALIBRATED |

### Dampers — VALIDATED from best lap garage screenshot
| Position | LS Comp | LS Rbd | HS Comp | HS Rbd | HS Slope |
|----------|---------|--------|---------|--------|----------|
| Front | **0** | **0** | **0** | **0** | **7** |
| Rear | **40** | **35** | **40** | **0** | **10** |

### Systems — VALIDATED
| Parameter | Value |
|-----------|-------|
| Brake bias | 49.0% |
| Brake pad | Low |
| MC F/R | 17.8 / 19.1 mm |
| Brake migration | 1 / gain 0.00 |
| TC gain / slip | 3 / 4 |
| Hybrid corner | 90% rear |
| Front diff preload | 5 Nm |
| Rear diff | More Locking / 6 plates / 20 Nm |
| Gears | Short (121.7 → 329.2 km/h) |

---

## Ferrari Session A — Front Dampers + Rear Springs + Camber (~1.5 hours, 6 stints)

**Batching logic:** Front dampers affect front shock channels. Rear heave affects rear RH channels. Camber affects tyre temps. All separable.

| Stint | Front LS Comp | Rear Heave Idx | Front Camber | Everything Else | What It Calibrates |
|-------|--------------|----------------|-------------|-----------------|-------------------|
| 1 | **0** (baseline) | **8** (baseline) | **–2.9°** (baseline) | baseline | Reference for all 3 axes |
| 2 | **10** | **8** | **–2.9°** | baseline | LS force-per-click (front shock osc, pitch) |
| 3 | **20** | **6** | **–2.9°** | adjust rear pushrod for RH≥30 | LS comp + rear heave m_eff |
| 4 | **30** | **6** | **–2.5°** | same | LS comp + camber contact patch |
| 5 | **40** | **4** | **–2.5°** | adjust rear pushrod | LS comp extreme + rear heave soft |
| 6 | **0** (return) | **8** (return) | **–2.9°** (return) | restore baseline | Confirm repeatability |

**What you calibrate in 6 stints:** LS force-per-click (5 data points), rear heave m_eff (3 heave values), front camber sensitivity (2 camber values). That's 3 phases of the old plan in 1 session.

**After stint 6:** If lap time matches stint 1 within 0.3s, the data is clean. If not, there's a hysteresis issue (tyres, track temp) and we need to time-weight.

---

## Ferrari Session B — HS Dampers + Front Springs + Rear Camber (~1.5 hours, 6 stints)

| Stint | Front HS Comp | Front Heave Idx | Rear Camber | Everything Else | What It Calibrates |
|-------|--------------|----------------|-------------|-----------------|-------------------|
| 1 | **0** (baseline) | **5** (baseline) | **–1.8°** (baseline) | baseline | Reference |
| 2 | **10** | **5** | **–1.8°** | baseline | HS force-per-click |
| 3 | **20** | **3** | **–1.8°** | adjust front pushrod | HS comp + front heave m_eff |
| 4 | **30** | **3** | **–1.5°** | same | HS comp + rear camber |
| 5 | **40** | **7** | **–1.5°** | adjust front pushrod | HS comp max + front heave stiff |
| 6 | **0** (return) | **5** (return) | **–1.8°** (return) | restore baseline | Repeatability check |

**What you calibrate:** HS force-per-click (5 points), front heave m_eff (3 values), rear camber sensitivity (2 values).

---

## Ferrari Session C — Wing + Torsion OD (~1 hour, 4 stints)

**Wing must be isolated** (affects everything). But front torsion OD and rear torsion OD affect different axes, so they CAN be batched with each other.

| Stint | Wing | Front Torsion OD Idx | Rear Torsion OD Idx | Adjust | What It Calibrates |
|-------|------|---------------------|--------------------|---------|--------------------|
| 1 | **17** (baseline) | **2** (baseline) | **1** (baseline) | — | Reference |
| 2 | **15** | **2** | **1** | — | Wing sensitivity (isolated) |
| 3 | **17** | **0** | **3** | adjust turns for RH≥30 | Front + rear torsion OD (batched) |
| 4 | **17** | **4** | **0** | adjust turns for RH≥30 | Front + rear torsion OD (opposite direction) |

**What you calibrate:** Wing sensitivity (2 wing values at same setup), front torsion OD→wheel rate (3 values), rear torsion OD→wheel rate (3 values).

---

## Ferrari Session D — Toe + Diff + HS Slope (~45 min, 4 stints)

**Low-priority parameters.** Toe, diff preload, and HS slope affect completely different systems.

| Stint | Rear Toe | Diff Preload | Front HS Slope | Everything Else |
|-------|----------|-------------|----------------|-----------------|
| 1 | **0.0** (baseline) | **20 Nm** (baseline) | **7** (baseline) | baseline |
| 2 | **+0.5** | **30 Nm** | **7** | — |
| 3 | **+0.5** | **20 Nm** | **4** | — |
| 4 | **0.0** | **15 Nm** | **10** | — |

---

## Ferrari Total: 4 sessions × ~1.5 hours = ~6 hours

Old plan: 6 phases × 1.5 hours = **9 hours**
New plan: 4 sessions = **~5.5 hours** (39% less time, same data)

And sessions A+B are the highest value — if you only have 3 hours, do A+B and you'll cover dampers + springs + camber.

---

# CAR 2: ACURA ARX-06
**Track:** Hockenheimring GP (5 sessions)
**Calibration score:** 2.5/10

## Baseline Setup (best observed: 87.599s)
| Parameter | Value |
|-----------|-------|
| Wing | 10 |
| Front heave (N/mm) | 200 |
| Rear heave (N/mm) | 220 |
| Front torsion OD | 15.51 mm |
| FARB | Soft / blade 1 |
| RARB | Soft / blade 1 |
| Static front RH | 30.0 mm |
| Static rear RH | 41.4 mm |

**Note:** Acura uses N/mm directly, not indexed. LS damper range: 1–10 clicks.

## Acura Session A — LS Dampers + Heave Springs + Camber (~1.5 hours, 6 stints)

| Stint | LS Comp | Front Heave (N/mm) | Front Camber | Adjust | Calibrates |
|-------|---------|-------------------|-------------|---------|------------|
| 1 | baseline | 200 (baseline) | baseline | — | Reference |
| 2 | 3 | 200 | baseline | — | LS comp soft |
| 3 | 6 | 150 | baseline | pushrod for RH≥30 | LS mid + heave soft |
| 4 | 9 | 150 | baseline –0.5° | same | LS stiff + camber |
| 5 | 10 (max) | 250 | baseline –0.5° | pushrod for RH≥30 | LS max + heave stiff |
| 6 | baseline | 200 | baseline | restore | Repeatability |

## Acura Session B — Wing + Torsion OD (~1 hour, 4 stints)

| Stint | Wing | Front Torsion OD (mm) | Adjust |
|-------|------|-----------------------|--------|
| 1 | 10 (baseline) | 15.51 (baseline) | — |
| 2 | 8 | 15.51 | — |
| 3 | 10 | 13.90 (softest) | turns for RH≥30 |
| 4 | 10 | 15.86 (stiffest) | turns for RH≥30 |

## Acura Total: 2 sessions × ~1.25 hours = ~2.5 hours
Old plan: 5 phases = ~5.5 hours. **55% less time.**

---

# CAR 3: CADILLAC V-SERIES.R
**Track:** Silverstone Circuit (4 sessions)
**Calibration score:** 3.0/10

**Note:** Cadillac uses N/mm directly. LS range: 1–11 clicks. Torsion OD: [13.9, 14.34, 14.76] mm.

## Baseline Setup (best observed: 108.018s)
| Parameter | Value |
|-----------|-------|
| Wing | 17 |
| Front heave (N/mm) | 60 |
| Rear heave/third (N/mm) | 150 |
| Front torsion OD | 14.34 mm |
| Static front RH | 30.9 mm |
| Static rear RH | 46.6 mm |

## Cadillac Session A — LS Dampers + Heave Springs + Camber (~1.5 hours, 6 stints)

| Stint | LS Comp | Front Heave (N/mm) | Front Camber | Adjust | Calibrates |
|-------|---------|-------------------|-------------|---------|------------|
| 1 | baseline | 60 (baseline) | baseline | — | Reference |
| 2 | 3 | 60 | baseline | — | LS soft |
| 3 | 6 | 40 | baseline | pushrod | LS mid + heave soft |
| 4 | 9 | 40 | baseline –0.3° | same | LS stiff + camber |
| 5 | 11 (max) | 80 | baseline –0.3° | pushrod | LS max + heave stiff |
| 6 | baseline | 60 | baseline | restore | Repeatability |

## Cadillac Session B — Wing + Torsion OD (~45 min, 3 stints)

| Stint | Wing | Front Torsion OD (mm) | Adjust |
|-------|------|-----------------------|--------|
| 1 | 17 (baseline) | 14.34 (baseline) | — |
| 2 | 15 | 14.34 | — |
| 3 | 17 | 13.90 | turns for RH≥30 |

## Cadillac Total: 2 sessions × ~1.25 hours = ~2.5 hours

---

# CAR 4: BMW M HYBRID V8
**Track:** Sebring (102 sessions — nearly complete)
**Calibration score:** 8.5/10

## Remaining Gaps (optional)

### BMW Session A — Torsion OD Upper Range + HS Damper (~1.5 hours, 5 stints)

| Stint | Front Torsion OD (mm) | HS Comp | Adjust |
|-------|----------------------|---------|--------|
| 1 | 14.34 (baseline) | baseline | — |
| 2 | 16.51 | baseline | turns for RH≥30 |
| 3 | 18.20 (max) | baseline | turns for RH≥30 |
| 4 | 14.34 | 5 (mid) | restore turns |
| 5 | 14.34 | 11 (max) | — |

---

# CAR 5: PORSCHE 963
**Track:** Sebring (2 sessions)
**Calibration score:** 1.5/10

**DSSV dampers** — non-linear spool valve. LS: 0–11 clicks. HS: 0–11 clicks.

## Porsche Phase 0 — Verify IBT Parsing (~30 min, REQUIRED FIRST)

Run 2 clean stints with no changes. Check observation JSON contains:
- `setup.front_heave_nmm` populated
- `setup.front_torsion_bar_index` populated
- `telemetry.lltd_measured` populated

**If fields are missing, STOP.** The IBT parser needs fixing before any calibration data is usable.

## Porsche Session A — LS Dampers + Heave Springs (~1.5 hours, 6 stints)

| Stint | LS Comp | Front Heave (N/mm) | Front Camber | Adjust |
|-------|---------|-------------------|-------------|---------|
| 1 | baseline | baseline | baseline | — |
| 2 | 3 | baseline | baseline | — |
| 3 | 6 | baseline –20 | baseline | pushrod |
| 4 | 9 | baseline –20 | baseline –0.3° | same |
| 5 | 11 (max) | baseline +20 | baseline –0.3° | pushrod |
| 6 | baseline | baseline | baseline | restore |

## Porsche Session B — Wing + Torsion OD (~45 min, 3 stints)

| Stint | Wing | Front Torsion OD (mm) | Adjust |
|-------|------|-----------------------|--------|
| 1 | 17 (baseline) | baseline | — |
| 2 | 15 | baseline | — |
| 3 | 17 | 13.90 | turns for RH≥30 |

## Porsche Total: Phase 0 (30 min) + 2 sessions (~2.5 hours) = ~3 hours

---

# PRIORITY ORDER

| # | Car | Session | Time | Impact |
|---|-----|---------|------|--------|
| 1 | **Ferrari** | Session A (LS + rear springs + camber) | 1.5 hr | Completes damper + spring + camber calibration |
| 2 | **Ferrari** | Session B (HS + front springs + rear camber) | 1.5 hr | Completes all Ferrari physics |
| 3 | **Acura** | Session A (LS + heave + camber) | 1.5 hr | First real Acura data |
| 4 | **Ferrari** | Session C (wing + torsion OD) | 1 hr | Validates aero + corner spring model |
| 5 | **Cadillac** | Session A (LS + heave + camber) | 1.5 hr | First real Cadillac data |
| 6 | **Acura** | Session B (wing + torsion) | 1 hr | Completes Acura basics |
| 7 | **Ferrari** | Session D (toe + diff + HS slope) | 45 min | Low-priority refinement |
| 8 | **Cadillac** | Session B (wing + torsion) | 45 min | Completes Cadillac basics |
| 9 | **Porsche** | Phase 0 (verify parsing) | 30 min | Must do before any Porsche work |
| 10 | **Porsche** | Session A (LS + heave) | 1.5 hr | First real Porsche data |
| 11 | **BMW** | Session A (torsion OD + HS) | 1.5 hr | Push from 8.5 → 9+ |

---

# WHAT GETS CALIBRATED AUTOMATICALLY

| After Each IBT Drop | What Updates |
|---------------------|-------------|
| Any session | k-NN database + empirical model relationships + damper physics correlations |
| LS damper sweep data | `ls_force_per_click_n` via shock_vel_p99 vs click fit |
| HS damper sweep data | `hs_force_per_click_n` via shock_vel_p99 vs click fit |
| Heave spring sweep | `front_m_eff_kg` / `rear_m_eff_kg` via RH variance vs spring rate |
| Wing sweep | `default_df_balance_pct` via measured balance at each wing angle |
| Camber sweep | Tyre temp model calibration (inner-outer spread vs camber) |
| Torsion OD sweep | Corner spring rate validation (k = C × OD⁴) |

All automatic via `pipeline.produce`. No manual calibration scripts needed.

---

# SKIP WEEKS (iRacing Season 2026 S2)

Reference `data/season_2026_s2.json` for the full schedule. Taylor is unavailable weeks 5, 6, 9, 10, and 12.

Active weeks: prioritize the car that's racing that week for calibration. If Hockenheim is on the calendar, Ferrari data is highest value.
