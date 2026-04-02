# iOptimal — Master Calibration Plan
**All 5 GTP Cars | Complete & Instructional**
*Last updated: 2026-04-02*

---

## How to Use This Plan

- Work through cars in **priority order** (most data + most used first)
- Each **Phase** = one dedicated session (1–2 hours)
- Every stint = **Outlap → Push L1 → Push L2 → Inlap → pit/change (~2 min)**
- **Change ONE variable per stint only** — everything else locked to baseline
- **Drop IBT after every stint** — run the analysis command immediately
- **Pushrod rule:** whenever you change a spring index or torsion OD, adjust pushrod to restore the same static ride height. Verify ≥30mm front + rear before going out.

### After Every Stint — Run This
```bash
cd /root/.openclaw/workspace/isetup/gtp-setup-builder

# Ferrari
python3 -m pipeline.produce --car ferrari --ibt "ibtfiles/YOUR_FILE.ibt" --mode safe --delta-card --top-n 1

# Acura
python3 -m pipeline.produce --car acura --ibt "ibtfiles/YOUR_FILE.ibt" --mode safe --delta-card --top-n 1

# BMW
python3 -m pipeline.produce --car bmw --ibt "ibtfiles/YOUR_FILE.ibt" --mode safe --delta-card --top-n 1

# Cadillac
python3 -m pipeline.produce --car cadillac --ibt "ibtfiles/YOUR_FILE.ibt" --mode safe --delta-card --top-n 1

# Porsche
python3 -m pipeline.produce --car porsche --ibt "ibtfiles/YOUR_FILE.ibt" --mode safe --delta-card --top-n 1

# Find newest IBT
ls -t ibtfiles/*.ibt | head -3
```

### Calibration Sequence (same for every car)
```
Phase 1 → LS Damper sweep (biggest unknown, most impact on recommendations)
Phase 2 → Heave spring sweep (calibrates m_eff, validates index→N/mm)
Phase 3 → DF balance (wing + RH sweep, validates aero model)
Phase 4 → HS Damper sweep (refines high-speed damping model)
Phase 5 → Camber/toe (validates geometry model)
Phase 6 → ARB stiffness (lowest priority — LLTD sweep if time allows)
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

| Car | Sessions | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Score |
|-----|----------|---------|---------|---------|---------|---------|-------|
| BMW | 102 | ✅ done | ✅ done | ✅ done | ✅ done | ✅ done | 8.5/10 |
| Ferrari | 20 | 🔄 tonight | ✅ done | ✅ done | ❌ needed | partial | 6.5/10 |
| Acura | 5 | ❌ needed | ❌ needed | ❌ needed | ❌ needed | ❌ needed | 2.5/10 |
| Cadillac | 4 | ❌ needed | ❌ needed | ❌ needed | ❌ needed | ❌ needed | 3.0/10 |
| Porsche | 2 | ❌ needed | ❌ needed | ❌ needed | ❌ needed | ❌ needed | 1.5/10 |

---

# CAR 1: FERRARI 499P
**Track:** Hockenheimring GP (most data — 20 sessions)
**Calibration score:** 6.5/10

## Baseline Setup (best observed: 87.575s)
| Parameter | Value |
|-----------|-------|
| Wing | 17 |
| Front heave index | 5 |
| Rear heave index | 8 |
| Front torsion bar index | 2 |
| Rear torsion bar index | 1 |
| FARB | B / blade 1 |
| RARB | C / blade 1 |
| Front pushrod | –3.0 mm |
| Rear pushrod | +14.0 mm |
| Static front RH | 30.1 mm |
| Static rear RH | 47.5 mm |
| Front camber | –1.9° |
| Rear camber | –1.6° |
| Front toe | –2.0 mm |
| Rear toe | 0.0 mm |
| LS comp | 24 (current running value) |

---

## Ferrari Phase 1 — LS Damper Sweep *(1.5 hours — do tonight)*
**What it calibrates:** `ls_force_per_click_n` (currently 31.3 N/click — estimated). Validates or corrects the model by measuring lap time vs ShockVel at 5 click settings.
**Lock:** Everything at baseline except front LS comp

| Stint | Front LS comp | Expected effect | Drop IBT? |
|-------|-------------|----------------|-----------|
| 1 | 24 (baseline) | Reference | ✅ |
| 2 | 10 | Underdam front — floaty entry | ✅ |
| 3 | 20 | Physics baseline click | ✅ |
| 4 | 30 | Stiff entry | ✅ |
| 5 | 40 (max) | Overdamped — harsh | ✅ |

**What to note per stint:** Does the car feel underdamped (bouncy over kerbs, floating on braking) or overdamped (harsh, loses traction on bumps)?

**After all 5 IBTs are dropped**, the analysis will extract ShockVel_p99 vs lap time for each and fit the force-per-click curve.

---

## Ferrari Phase 2 — HS Damper Sweep *(1 hour)*
**What it calibrates:** `hs_force_per_click_n` (currently 151.8 N/click — estimated).
**Lock:** Everything at baseline. Reset LS comp to 20 (Phase 1 best, or stay at 24 if baseline was fastest). Only vary HS comp.

| Stint | Front HS comp | Expected effect |
|-------|-------------|----------------|
| 1 | 13 (baseline) | Reference |
| 2 | 5 | Soft HS — kerb absorption but platform instability |
| 3 | 20 | Mid |
| 4 | 30 | Stiff HS |
| 5 | 40 (max) | Very stiff over kerbs |

---

## Ferrari Phase 3 — Heave Spring Sweep *(1 hour)*
**What it calibrates:** m_eff_front (1439 kg — estimated), validates index→N/mm mapping (only idx=1 is confirmed validated).
**IMPORTANT:** For every heave index change, adjust front pushrod to restore static front RH to **30.1 mm** (baseline).

| Stint | Front heave idx | Adjust pushrod | Expected static RH after |
|-------|----------------|----------------|--------------------------|
| 1 | 5 (baseline) | –3.0 mm (baseline) | 30.1 mm |
| 2 | 3 | Raise pushrod until garage shows ~30.1 mm | 30.1 mm |
| 3 | 5 | Restore to –3.0 mm | 30.1 mm |
| 4 | 7 | Lower pushrod until garage shows ~30.1 mm | 30.1 mm |
| 5 | 1 (softest) | Raise pushrod significantly | 30.1 mm |

**Verify:** After each pushrod adjust, garage static front RH = 30.1 mm ±0.5 mm before going out.

---

## Ferrari Phase 4 — DF Balance / Wing Sweep *(1 hour)*
**What it calibrates:** `default_df_balance_pct` (currently 48.3% — empirical from 17 sessions). Validates wing sensitivity at different RH positions.
**Lock:** Dampers at best setting from Phase 1-2. Heave at idx=5. Only vary wing.

| Stint | Wing | Expected balance |
|-------|------|-----------------|
| 1 | 17 (baseline) | ~48% (best observed) |
| 2 | 15 | Lower front DF |
| 3 | 14 | Lower still |
| 4 | 16 | Between 15-17 |
| 5 | 12 | Should be noticeably slower (currently confirmed) |

---

## Ferrari Phase 5 — Camber Sweep *(45 min)*
**What it calibrates:** Camber sensitivity, tire contact patch model.
**Lock:** Everything at best from previous phases. Only vary front camber.

| Stint | Front camber | Expected |
|-------|-------------|----------|
| 1 | –1.9° (baseline) | Reference |
| 2 | –1.5° | Less negative — reduce front grip |
| 3 | –2.2° | More negative — increase limit grip, possible wear |
| 4 | –1.7° | Between baseline and –1.5° |

---

# CAR 2: ACURA ARX-06
**Track:** Hockenheimring GP (5 sessions — most Acura data here)
**Calibration score:** 2.5/10 — almost everything is estimated

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
| LLTD measured | 0.5088 |

**Note:** Acura uses N/mm directly (not indexed like Ferrari). The torsion OD options are [13.9, 14.34, 14.76, 15.14, 15.51, 15.86] mm.

---

## Acura Phase 1 — LS Damper Sweep *(1.5 hours)*
**Lock:** Everything at baseline except LS comp. Acura LS range: 1–10 clicks.

| Stint | LS comp | Expected |
|-------|---------|----------|
| 1 | current (baseline) | Reference |
| 2 | 2 | Soft end |
| 3 | 5 | Mid |
| 4 | 8 | Stiff |
| 5 | 10 (max) | Max |

---

## Acura Phase 2 — Heave Spring Sweep *(1 hour)*
**What it calibrates:** m_eff_front (450 kg — ESTIMATE with no data), m_eff_rear (220 kg — ESTIMATE).
**IMPORTANT:** Adjust front pushrod after each heave change to maintain static front RH = 30.0 mm.

| Stint | Front heave (N/mm) | Pushrod action | Verify static RH |
|-------|-------------------|----------------|-----------------|
| 1 | 200 (baseline) | baseline pushrod | 30.0 mm |
| 2 | 150 | Raise front pushrod | 30.0 mm |
| 3 | 200 | Restore baseline | 30.0 mm |
| 4 | 250 | Lower front pushrod | 30.0 mm |
| 5 | 100 (soft) | Raise significantly | 30.0 mm |

---

## Acura Phase 3 — Torsion Bar OD Sweep *(45 min)*
**What it calibrates:** Corner spring rate at each OD. Acura options: [13.9, 14.34, 14.76, 15.14, 15.51, 15.86] mm.
**IMPORTANT:** After each OD change, adjust torsion bar turns to restore static front RH = 30.0 mm.

| Stint | Front torsion OD (mm) | Adjust turns | Verify RH |
|-------|----------------------|-------------|-----------|
| 1 | 15.51 (baseline) | baseline turns | 30.0 mm |
| 2 | 13.90 (softest) | Add turns until RH = 30.0 | 30.0 mm |
| 3 | 14.76 (mid) | Adjust turns | 30.0 mm |
| 4 | 15.86 (stiffest) | Reduce turns until RH = 30.0 | 30.0 mm |

---

## Acura Phase 4 — DF Balance / Wing Sweep *(1 hour)*
**Note:** All 5 prior sessions used wing=10 — no wing variation data exists yet.

| Stint | Wing | Expected |
|-------|------|----------|
| 1 | 10 (baseline) | Reference |
| 2 | 8 | Less DF — faster straight, less downforce |
| 3 | 9 | Between 8-10 |
| 4 | 10 | Confirm baseline |

---

## Acura Phase 5 — Camber Sweep *(45 min)*
**Lock:** Everything at best from previous phases.

| Stint | Front camber | Notes |
|-------|-------------|-------|
| 1 | current baseline | Reference |
| 2 | baseline –0.3° | Less negative |
| 3 | baseline +0.5° | More negative |
| 4 | baseline +0.3° | Moderate |

---

# CAR 3: CADILLAC V-SERIES.R
**Track:** Silverstone Circuit (4 sessions only)
**Calibration score:** 3.0/10 — adapter bug was fixed; physics still BMW copies
**Note:** Cadillac uses N/mm directly. LS range: 1–11 clicks. Torsion OD: [13.9, 14.34, 14.76] mm.

## Baseline Setup (best observed: 108.018s at Silverstone)
| Parameter | Value |
|-----------|-------|
| Wing | 17 |
| Front heave (N/mm) | 60 |
| Rear heave/third (N/mm) | 150 |
| Front torsion OD | 14.34 mm |
| Rear spring (N/mm) | 110 |
| FARB | Soft / blade 1 |
| RARB | Soft / blade 3 |
| Static front RH | 30.9 mm |
| Static rear RH | 46.6 mm |
| LLTD | 0.5063 |

---

## Cadillac Phase 1 — LS Damper Sweep *(1.5 hours, at Silverstone)*
**Lock:** Everything at baseline. LS range: 1–11 clicks.

| Stint | LS comp | Notes |
|-------|---------|-------|
| 1 | current | Reference |
| 2 | 2 | Soft |
| 3 | 5 | Mid |
| 4 | 8 | Stiff |
| 5 | 11 (max) | Max |

---

## Cadillac Phase 2 — Heave Spring Sweep *(1 hour)*
**What it calibrates:** m_eff_front (266 kg — ESTIMATE, implausibly low), m_eff_rear (2200 kg — ESTIMATE).
**IMPORTANT:** Adjust pushrod after each heave change to restore static front RH = 30.9 mm.

| Stint | Front heave (N/mm) | Adjust pushrod | Verify RH |
|-------|-------------------|----------------|-----------|
| 1 | 60 (baseline) | baseline | 30.9 mm |
| 2 | 40 | Raise pushrod | 30.9 mm |
| 3 | 60 | Restore | 30.9 mm |
| 4 | 80 | Lower pushrod | 30.9 mm |
| 5 | 100 | Lower further | 30.9 mm |

---

## Cadillac Phase 3 — Torsion Bar OD Sweep *(45 min)*
Options: [13.9, 14.34, 14.76] mm. Adjust torsion bar turns after each change to restore static front RH.

| Stint | Front torsion OD | Adjust turns | Verify RH |
|-------|-----------------|-------------|-----------|
| 1 | 14.34 (baseline) | baseline | 30.9 mm |
| 2 | 13.90 (softest) | Add turns | 30.9 mm |
| 3 | 14.76 (stiffest) | Reduce turns | 30.9 mm |

---

## Cadillac Phase 4 — Wing Sweep *(1 hour)*

| Stint | Wing | Notes |
|-------|------|-------|
| 1 | 17 (baseline) | Reference — best observed always wing=17 |
| 2 | 15 | Less DF |
| 3 | 14 | More aggressive |
| 4 | 17 | Confirm baseline |

---

# CAR 4: BMW M HYBRID V8
**Track:** Sebring International Raceway (102 sessions — fully calibrated)
**Calibration score:** 8.5/10 — mostly done
**Status:** Nearly complete. Do these only if you want to push from 8.5 → 9.5+.

## Remaining Gaps

### BMW Gap 1 — Torsion Bar OD Validation *(1 hour)*
**Why:** We have 14 OD options [13.9 → 18.2 mm] but only validated the lower range. Upper range (≥17mm) behavior is extrapolated.
**Lock:** Everything at BMW best setup. Only vary front torsion OD.
**IMPORTANT:** Adjust torsion bar turns after each OD change to restore front RH = 30.0 mm.

| Stint | Front torsion OD (mm) | Adjust turns | Notes |
|-------|----------------------|-------------|-------|
| 1 | 14.34 (best observed) | baseline | Reference |
| 2 | 13.90 (softest) | Add turns | Bracket low end |
| 3 | 15.14 | Reduce turns | Mid range |
| 4 | 16.51 | Reduce further | Upper range |
| 5 | 18.20 (max) | Reduce significantly | Max stiffness |

### BMW Gap 2 — HS Damper Validation *(1 hour)*
Current hs_force_per_click_n = 80.0 N/click (validated). This sweep confirms the HS knee velocity model.

| Stint | HS comp | Notes |
|-------|---------|-------|
| 1 | current | Reference |
| 2 | 2 | Soft HS |
| 3 | 5 | Mid |
| 4 | 9 | Stiff |
| 5 | 11 (max) | Max |

---

# CAR 5: PORSCHE 963
**Track:** Sebring International Raceway (2 sessions only — essentially start from scratch)
**Calibration score:** 1.5/10
**Special notes:**
- Porsche has **DSSV dampers** — non-linear, spool valve behavior
- LS range: 0–11 clicks; HS range: 0–11 clicks
- **No Disconnected ARB option** (Soft/Medium/Stiff only)
- Torsion OD: [13.9, 14.34, 14.76] mm
- m_eff is BMW copy (2100 kg rear — ESTIMATE)
- LLTD measured = 0.4907 (different from other GTP cars — model needs calibration)

## Baseline Setup (best observed: 82.180s at Sebring)
| Parameter | Value |
|-----------|-------|
| Wing | 17 |
| Front heave (N/mm) | unknown (not parsed — IBT parsing issue) |
| Static front RH | 30.1 mm |
| Static rear RH | 54.1 mm |
| LLTD | 0.4907 |

**First step before any sweeps:** Run a clean baseline session and verify the IBT parses correctly (Porsche has had parsing issues — check garage values appear in observation JSON before starting sweeps).

---

## Porsche Phase 0 — Baseline Verification *(30 min)*
Run 2 clean stints with no changes. Verify the observation JSON contains:
- `setup.front_heave_nmm` populated
- `setup.front_torsion_bar_index` populated
- `telemetry.lltd_measured` populated

```bash
cat data/learnings/observations/porsche_sebring_*.json | python3 -c "
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    s = d.get('setup', {})
    print('heave:', s.get('front_heave_nmm'), 'torsion:', s.get('front_torsion_bar_index'))
"
```

If parsing works, proceed. If fields are missing, the IBT structure needs investigation before calibration sessions will yield usable data.

---

## Porsche Phase 1 — LS Damper Sweep *(1.5 hours, at Sebring)*
**Note:** Porsche DSSV dampers are non-linear. The force-per-click model will have higher error than BMW. Still worth doing — establishes the operating range.

| Stint | LS comp | Notes |
|-------|---------|-------|
| 1 | current (baseline) | Reference |
| 2 | 2 | Soft |
| 3 | 5 | Mid |
| 4 | 8 | Stiff |
| 5 | 11 (max) | Max — expect non-linear behavior here |

---

## Porsche Phase 2 — Heave Spring Sweep *(1 hour)*
**What it calibrates:** m_eff_front (176 kg — ESTIMATE, likely wrong), m_eff_rear (2100 kg — ESTIMATE).
**IMPORTANT:** Adjust pushrod after each heave change to restore static front RH = 30.1 mm.

| Stint | Front heave (N/mm) | Adjust pushrod | Verify RH |
|-------|-------------------|----------------|-----------|
| 1 | baseline | baseline | 30.1 mm |
| 2 | baseline –20 | Raise pushrod | 30.1 mm |
| 3 | baseline | Restore | 30.1 mm |
| 4 | baseline +20 | Lower pushrod | 30.1 mm |
| 5 | baseline +40 | Lower further | 30.1 mm |

---

## Porsche Phase 3 — Torsion Bar OD Sweep *(45 min)*
Options: [13.9, 14.34, 14.76] mm.

| Stint | Front torsion OD | Adjust turns | Verify RH |
|-------|-----------------|-------------|-----------|
| 1 | baseline | baseline | 30.1 mm |
| 2 | 13.90 | Add turns | 30.1 mm |
| 3 | 14.76 | Reduce turns | 30.1 mm |

---

## Porsche Phase 4 — Wing Sweep *(1 hour)*

| Stint | Wing | Notes |
|-------|------|-------|
| 1 | 17 (baseline) | Reference |
| 2 | 15 | Less DF |
| 3 | 14 | More aggressive |
| 4 | 17 | Confirm baseline |

---

# WHAT CHANGES IN THE MODEL AFTER EACH SESSION

| Session | What gets updated automatically | Command to trigger update |
|---------|-------------------------------|--------------------------|
| Any new IBT | k-NN session database (better recommendations next run) | auto — happens on IBT ingest |
| LS Damper sweep (5 IBTs) | `ls_force_per_click_n` in cars.py — run calibration tool | `python3 validation/damper_calibration.py --car ferrari` *(once this exists)* |
| Heave sweep (5 IBTs) | `front_m_eff_kg`, `rear_m_eff_kg` in cars.py | `python3 validation/mass_calibration.py --car ferrari` *(once this exists)* |
| Wing sweep (4 IBTs) | `default_df_balance_pct` in cars.py | Update manually from `calibration_report.md` output |

**Currently:** After a sweep, report back here or send the IBTs — the calibration update will be applied manually from the data.

---

# SESSION PRIORITY ORDER

Do these roughly in this order when you have time:

| Priority | Car | Session | Time needed | Impact |
|----------|-----|---------|-------------|--------|
| 1 | Ferrari | Phase 1 LS Damper | 1.5 hr | Fixes damper click recommendations |
| 2 | Acura | Phase 1 LS Damper | 1.5 hr | First real Acura damper data |
| 3 | Acura | Phase 2 Heave sweep | 1 hr | Fixes m_eff (completely unknown) |
| 4 | Ferrari | Phase 2 HS Damper | 1 hr | Completes Ferrari damper model |
| 5 | Cadillac | Phase 1 LS Damper | 1.5 hr | First real Cadillac damper data |
| 6 | Ferrari | Phase 3 Heave sweep | 1 hr | Validates index→N/mm mapping |
| 7 | Acura | Phase 3 Torsion OD | 45 min | Corner spring validation |
| 8 | Cadillac | Phase 2 Heave sweep | 1 hr | Fixes m_eff |
| 9 | Porsche | Phase 0 Baseline verify | 30 min | Check IBT parsing before wasting time |
| 10 | Porsche | Phase 1 LS Damper | 1.5 hr | First real Porsche data |
| 11 | BMW | Gap 1 Torsion OD | 1 hr | Improves upper OD range |
| 12 | All cars | Camber/toe sweeps | 45 min each | Geometry refinement |
