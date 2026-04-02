# Hockenheim Practice Plan — 2026-04-02
**Track:** Hockenheimring GP | **Duration:** 90 min | **Lap time:** ~90s
**Cars:** Ferrari 499P → Acura ARX-06

## Stint Structure
Every stint: **Outlap → Push L1 → Push L2 → Inlap → pit/setup change (~2 min)**
= 4 laps × 1.5 min + 2 min = **~8 min per stint**

## High-Value Parameters (Taylor's priority list)
Heaves · Torsion bar OD · Torsion bar turns · Pushrod · Heave springs · Camber · Toe · Dampers
*(ARBs deprioritized — less setup sensitivity)*

## ⚠️ Dependency Rules — Must Follow for Clean Data

| If you change... | You must also adjust... | Why |
|-----------------|------------------------|-----|
| Front/rear heave index (spring stiffness) | **Front/rear pushrod** → restore static RH to baseline value | Stiffer spring = less deflection = car sits higher unless pushrod compensates |
| Torsion bar OD | **Torsion bar turns** → restore static RH | Larger OD = stiffer corner spring = RH rises |
| Pushrod directly | Nothing — direct RH control | |
| Camber | Check toe is still in legal range | Geometry coupling, minor |

**Minimum legal static RH: 30mm front and rear.** After any spring or pushrod change, verify garage shows ≥30mm before going out.

**The auto-solver handles this cascade automatically** — when it recommends a new heave index, the delta card will also show the required pushrod adjustment. For manual testing stints below, you must do this yourself.

---

## Ferrari 499P Block (T+0:00 – T+44:00) — 5 stints

| # | Time | Change | Value |
|---|------|--------|-------|
| 1 | T+0:00 | **Baseline** | fh=5, rh=8, ftb=2, rtb=1, FARB=B/1, RARB=C/1, wing=17, front LS=**0**, rear LS=**40** |
| 2 | T+10:00 | Front LS comp = **5** | First step up from zero |
| 3 | T+20:00 | Front LS comp = **10** | Moderate front damping |
| 4 | T+30:00 | Front LS comp = **20** | Noticeably more front |
| 5 | T+40:00 | Front LS comp = **30** | High front damping |

**Note:** Baseline is front LS=0 (VALIDATED from best observed IBT). Sweeping upward from zero.
**Only changing:** front LS comp. All else locked to Stint 1 — especially rear LS stays at 40.
**Drop IBT after every stint.**

---

## Acura ARX-06 Block (T+46:00 – T+90:00) — 5 stints

**DB entering:** 5 sessions. Best: 87.599s. All prior at wing=10, Medium or Soft ARB.
**Focus:** heave springs, torsion bars, camber — parameters that actually move the car.

| # | Time | Change | Value | Why |
|---|------|--------|-------|-----|
| 6 | T+46:00 | **Baseline** | Current best setup as-is | Reference anchor |
| 7 | T+56:00 | Front heave **–2 idx** + adjust front pushrod to restore baseline static front RH | Less front heave stiffness | Heave spring sensitivity |
| 8 | T+66:00 | Front heave **+2 idx** + adjust front pushrod to restore baseline static front RH | More front heave stiffness | Brackets heave range |
| 9 | T+76:00 | Front camber **–0.5°** more negative | e.g. –3.0° → –3.5° | Camber sensitivity + tire temp |
| 10 | T+86:00 | **Solver rec** | Check Telegram card | Run whatever was recommended |

**Only changing one variable per stint from Stint 6 baseline.**
**Drop IBT after every stint.**

---

## What This Feeds

| Stints | Car | Parameter | Calibrates |
|--------|-----|-----------|-----------|
| 1–5 | Ferrari | LS damper 10/20/24/30/40 | Force per click model (31.3 N/click) |
| 6–8 | Acura | Front heave ±2 idx | Heave spring sensitivity, m_eff estimate |
| 9 | Acura | Front camber –0.5° | Camber lap time gradient |
| 10 | Acura | Solver rec | Real-world validation |
