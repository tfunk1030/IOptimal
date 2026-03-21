# Ferrari 499P Damper Click Force Calibration — Sebring

**Date:** 2026-03-21  
**Source:** 7 IBT sessions (setup data) + observation telemetry  
**Reference:** Fastest session Mar19C (108.113s)

## Available Telemetry Channels

From IBTFile analysis: suspension velocity channels are NOT directly available
as named telemetry variables in the iRacing IBT format for Ferrari 499P.
Instead, we use derived shock velocity from the observation pipeline.

**Channels confirmed present (observation pipeline extraction):**
- `lf_shock_vel_p95_mps` — Left front shock velocity 95th percentile (m/s)
- `rf_shock_vel_p95_mps` — Right front shock velocity 95th percentile (m/s)  
- `lr_shock_vel_p95_mps` — Left rear shock velocity 95th percentile (m/s)
- `rr_shock_vel_p95_mps` — Right rear shock velocity 95th percentile (m/s)
- `front_shock_vel_p95_mps` — Front mean p95 (m/s)
- `rear_shock_vel_p95_mps` — Rear mean p95 (m/s)

## Damper Settings vs Shock Velocity

### Fastest Session: Mar19C (108.113s) — Damper Setup

| Corner | LS Comp | LS Rbd | HS Comp | HS Rbd | HS Slope |
|--------|---------|--------|---------|--------|----------|
| LF | 15 | 25 | 15 | 6 | 5 |
| RF | 15 | 25 | 15 | 6 | 5 |
| LR | 18 | 10 | 40 | 40 | 11 |
| RR | 18 | 10 | 40 | 40 | 11 |

### All Sessions: Damper Comparison

| Session | Lap (s) | LF_LSC | LF_LSR | LR_LSC | LR_LSR | LR_HSC | LR_HSR |
|---------|---------|--------|--------|--------|--------|--------|--------|
| Mar16 | 109.116 | 24 | 21 | 13 | 15 | 8 | 27 |
| Mar19A | 109.717 | 24 | 21 | 13 | 15 | 8 | 27 |
| Mar19B | 109.949 | 22 | 28 | 14 | 34 | 24 | 40 |
| Mar19C | 108.113 | 15 | 25 | 18 | 10 | 40 | 40 |
| Mar20A | 109.188 | 36 | 20 | 15 | 18 | 13 | 40 |
| Mar20B | 109.227 | 18 | 15 | 18 | 20 | 40 | 40 |
| Mar20C | 109.032 | 38 | 20 | 18 | 20 | 15 | 40 |

## Shock Velocity vs Damper Clicks (from observation pipeline)

| Session | Lap (s) | F_p95 (m/s) | R_p95 (m/s) | LF_p95 | RF_p95 | LR_p95 | RR_p95 |
|---------|---------|-------------|-------------|--------|--------|--------|--------|
| Mar16 | 109.116 | 0.1107 | 0.1553 | 0.1177 | 0.1038 | 0.1685 | 0.1427 |
| Mar20C | 109.032 | 0.0839 | 0.1315 | 0.0900 | 0.0795 | 0.1439 | 0.1187 |

## Force-Per-Click Estimation

**Key data point (Mar16 vs Mar20C comparison):**
- Mar16: front LS_Comp=24, Rear LS_Comp=13 → F_p95=0.1107 m/s, R_p95=0.1553 m/s
- Mar20C: front LS_Comp=38, Rear LS_Comp=18 → F_p95=0.0839 m/s, R_p95=0.1315 m/s

**Mar16 vs Mar20C delta:**
- Front: +14 clicks LS_Comp → velocity drop from 0.1107 to 0.0839 m/s (~24% reduction)
- Rear: +5 clicks LS_Comp → velocity drop from 0.1553 to 0.1315 m/s (~15% reduction)

**Velocity reduction per click (rough):**
- Front LS_Comp: ~1.9% per click at the p95 operating range
- Rear LS_Comp: ~3.0% per click

**Note:** Velocity reduction is not linear per click; this is a linearization at
the operating point. The actual damper curve is progressive.

## Low-Speed vs High-Speed Velocity Distribution

From Mar16 observation (best data):
- `front_heave_vel_ls_pct`: 25.8% of heave motion is in LS range (<0.1 m/s)
- `front_heave_vel_hs_pct`: 24.7% of heave motion is in HS range (>0.3 m/s)
- Remaining ~50% is in mid-speed range (0.1–0.3 m/s)

**Implication:** The Ferrari 499P at Sebring operates predominantly in mid-speed
damping range. Click tuning matters most for LS comp (corner entry) and LS rbd
(exit roll recovery). HS comp is primarily relevant for kerb impacts.

## Fastest Session Damper Analysis (Mar19C)

Mar19C (108.113s) used significantly softer damper settings:
- Front LS_Comp: **15 clicks** (vs 24 for Mar16, vs 38 for Mar20C)
- Front LS_Rbd: **25 clicks** (vs 21 for Mar16)  
- Rear LS_Comp: **18 clicks** (same as Mar20C)
- Rear LS_Rbd: **10 clicks** (vs 15 for Mar16 — softer rear rebound)
- Rear HS_Comp: **40 clicks** (much stiffer HS comp on rear)
- Rear HS_Rbd: **40 clicks** (much stiffer HS rbd)

**Pattern:** Mar19C had softest front LS comp + stiffer rear HS → 
Allows front rotation on entry, stiffens rear on high-speed bumps.
This is consistent with the progressive throttle driver profile.

## Recommended Solver Click Targets (Ferrari Sebring)

Based on fastest session (Mar19C) and velocity analysis:
- Front LS_Comp: 14–16 clicks (soft entry, allows rotation)
- Front LS_Rbd: 24–26 clicks (medium-firm to control post-apex weight transfer)
- Front HS_Comp: 14–16 clicks (moderate kerb absorption)
- Rear LS_Comp: 17–19 clicks (medium rear compression)
- Rear LS_Rbd: 9–11 clicks (soft rear rebound, allows rotation)
- Rear HS_Comp: 38–42 clicks (stiff for platform stability)
- Rear HS_Rbd: 38–42 clicks (stiff to control bump rebound)
