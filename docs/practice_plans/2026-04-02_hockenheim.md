# Hockenheim Practice Plan — 2026-04-02
**Track:** Hockenheimring GP | **Duration:** 90 min | **Lap time ref:** ~90s
**Cars:** Ferrari 499P → Acura ARX-06
**Validated baselines from:** 87.575s lap garage screenshots (dampers + chassis + systems tabs, 2026-04-02)

## Stint Structure
Every stint: **Outlap → Push L1 → Push L2 → Inlap → pit/setup change (~2 min)**
= 4 laps × ~1.5 min + 2 min = **~8 min per stint**

## High-Value Parameters (Priority order)
Dampers · Heaves · Torsion bar OD · Torsion bar turns · Pushrod · Camber · Toe
*(ARBs deprioritized — less setup sensitivity)*

---

## ⚠️ Dependency Rules — Must Follow for Clean Data

| If you change... | You must also adjust... | Why |
|-----------------|------------------------|-----|
| Heave spring index | **Pushrod** → restore static RH to baseline | Stiffer = higher car → must compensate |
| Torsion bar OD | **Torsion bar turns** → restore static RH | Stiffer OD = higher car |
| Pushrod directly | Nothing — it IS the RH control | |
| Camber | Check toe still in legal range | Minor geometry coupling |

**Minimum legal static RH: ≥30mm front AND rear. Verify in garage before going out.**

---

## Ferrari 499P — VALIDATED BASELINE SETUP

All values from 87.575s best lap garage screenshots. Use this exactly for Stint 1.

### Springs / Ride Height
| Parameter | Value |
|-----------|-------|
| Front heave index | **5** |
| Front heave perch | **–6.5 mm** |
| Rear heave index | **8** |
| Rear heave perch | **–104.0 mm** |
| Front torsion bar OD index | **2** |
| Front torsion bar turns | **0.100 turns** |
| Rear torsion bar OD index | **1** |
| Rear torsion bar turns | **0.048 turns** |
| Front pushrod delta | **+2.0 mm** |
| Rear pushrod delta | **+18.0 mm** |
| Static front RH | **30.1 mm** |
| Static rear RH | **47.5 mm** |

### Aero / ARB
| Parameter | Value |
|-----------|-------|
| Wing | **17** |
| FARB | **B / blade 1** |
| RARB | **C / blade 1** |

### Geometry
| Parameter | Value |
|-----------|-------|
| Front camber | **–2.9°** (at legal limit — ⚠️) |
| Rear camber | **–1.8°** |
| Front toe | **–0.5 mm** |
| Rear toe | **0.0 mm** |

### Dampers (Corner)
| Parameter | Value |
|-----------|-------|
| Front LS comp | **0** |
| Front HS comp | **0** |
| Front HS slope | **7** |
| Front LS rbd | **0** |
| Front HS rbd | **0** |
| Rear LS comp | **40** (MAX) |
| Rear HS comp | **40** (MAX) |
| Rear HS slope | **10** |
| Rear LS rbd | **35** |
| Rear HS rbd | **0** |

### Systems
| Parameter | Value |
|-----------|-------|
| Brake bias | **49.0%** front |
| Brake pad | Low |
| Front MC | 17.8 mm |
| Rear MC | 19.1 mm |
| Brake migration | 1 / gain 0.00 |
| TC2 (gain) | **3** |
| TC1 (slip) | **4** |
| Hybrid corner % | 90% rear |
| Front diff preload | **5 Nm** |
| Rear diff | More Locking / 6 plates / **20 Nm** |
| Gear stack | Short |
| Fuel | 58.0 L (adjust to your practice fuel load) |

---

## Ferrari Block (T+0:00 – T+44:00) — LS Damper Sweep

**Goal:** Calibrate `ls_force_per_click_n` (currently 31.3 N/click — physics estimate).
We're measuring the **cost of adding front LS damping** — the best lap ran zero.

**Lock everything at validated baseline. Only change: front LS comp.**
Rear LS stays at **40** every stint.

| # | Time | Front LS comp | Expected | Drop IBT |
|---|------|--------------|----------|----------|
| 1 | T+0:00 | **0** (validated baseline) | Reference — zero front mechanical damping | ✅ |
| 2 | T+8:00 | **5** | First step up — marginal effect expected | ✅ |
| 3 | T+16:00 | **10** | Moderate — may feel planted but slower | ✅ |
| 4 | T+24:00 | **20** | Noticeable — expect lap time loss | ✅ |
| 5 | T+32:00 | **30** | High — likely measurably slower | ✅ |

**Reset to 0 between stints** (don't leave it building up).

After 5 IBTs, run:
```bash
cd /root/.openclaw/workspace/isetup/gtp-setup-builder
ls -t ibtfiles/*.ibt | head -6
python3 -m pipeline.produce --car ferrari --ibt "ibtfiles/LATEST.ibt" --mode safe --delta-card --top-n 1
```

---

## Acura ARX-06 Block (T+46:00 – T+90:00) — Heave + Camber Sweep

**DB entering tonight:** 5 sessions. Best: 87.599s (wing=10, FARB=Soft/1, RARB=Soft/1).
**Goal:** First real heave sensitivity data — currently all m_eff/heave physics are estimates.

**Lock all Acura at current best setup baseline. Only change one variable per stint.**
After each stint, adjust pushrod to restore front static RH before going out.

| # | Time | Change | Value | Why |
|---|------|--------|-------|-----|
| 6 | T+46:00 | **Baseline** | Current best setup | Reference anchor |
| 7 | T+54:00 | Front heave **–2 idx** + pushrod adjust | Softer front | Heave sensitivity low end |
| 8 | T+62:00 | Front heave **+2 idx** + pushrod adjust | Stiffer front | Heave sensitivity high end |
| 9 | T+70:00 | Front heave **back to baseline** + pushrod restore | Reset | Back to ref before camber test |
| 10 | T+78:00 | Front camber **–0.5° more negative** | e.g. current → current – 0.5° | Camber sensitivity |

**Pushrod rule:** After heave index change, adjust front pushrod in the garage until static front RH shows the same value as Stint 6. Don't go out until ≥30mm confirmed.

After block, run:
```bash
python3 -m pipeline.produce --car acura --ibt "ibtfiles/LATEST_ACURA.ibt" --mode safe --delta-card --top-n 1
```

---

## What This Calibrates

| Stints | Car | Data collected | Updates |
|--------|-----|----------------|---------|
| 1–5 | Ferrari | LS comp 0/5/10/20/30 vs lap time | `ls_force_per_click_n` (31.3 estimate → validated) |
| 6–8 | Acura | Heave ±2 idx vs lap time | Heave sensitivity, m_eff front (unknown) |
| 9 | Acura | Heave back to baseline | Confirms setup reset for clean camber test |
| 10 | Acura | Camber –0.5° vs lap time | Camber gradient, geometry model |

---

## After Practice — Auto-Solver Will Ingest on the Hour

Drop IBTs into `ibtfiles/` and the cron picks them up. Or run manually:
```bash
# Newest IBT
LATEST=$(ls -t /root/.openclaw/workspace/isetup/gtp-setup-builder/ibtfiles/*.ibt | head -1)
echo "Latest IBT: $LATEST"

# Ferrari
python3 -m pipeline.produce --car ferrari --ibt "$LATEST" --mode safe --delta-card --top-n 1

# Acura
python3 -m pipeline.produce --car acura --ibt "$LATEST" --mode safe --delta-card --top-n 1
```

---

## Notes

- **HS slope** (7 front, 10 rear) = velocity knee between LS and HS regimes. Sweep this in a separate session after LS data is calibrated.
- **Front damper insight:** Ferrari @ Hockenheim runs zero front corner damping. The aero load handles front stability. Adding LS clicks likely hurts — we're quantifying by how much.
- **Rear is maxed:** Rear LS=40 + HS=40 controls platform bounce. Don't change rear dampers tonight — keeps rear behavior constant for a clean front LS variable.
- **Gear context:** Hockenheim is mainly 3rd–5th gear. Braking from 291 km/h at end of main straight. HS dampers matter at these speeds — useful when we run Phase 2 HS sweep.
