# Hockenheim Practice Plan — 2026-04-02
**Track:** Hockenheimring GP | **Duration:** 90 min | **Lap time:** ~90s
**Cars:** Ferrari 499P → Acura ARX-06

## Tire Warm-Up Reality
- Laps 1-3: cold tires + charging battery = not valid data
- **Lap 4+:** warm tires + charged battery = valid
- Every stint restarts the clock (pit = cold tires again)

**Per stint formula:** 3 warm laps + N valid laps + pit/setup change (~2 min)
- Minimum useful stint: 3 warm + 1 valid = 4 laps = **8 min + 2 min change = 10 min**
- Two valid laps: 3 warm + 2 valid = 5 laps = **9.5 min + 2 min = 11.5 min**

---

## Ferrari 499P Block (T+0:00 – T+48:00) — 48 min / 4 stints

### Stint 1 — Baseline `T+0:00 → T+11:30`
**Setup:** fh=5, rh=8, ftb=2, rtb=1, FARB=B/1, RARB=C/1, wing=17, LS comp=24
- 3 warm laps → **2 valid laps (L4 + L5)** → pit
- **Drop IBT.** Best observed (87.575s) — reference for all comparisons.

### Stint 2 — LS Soft `T+13:30 → T+23:00`
**Change:** Front LS comp = **10** only
- 3 warm laps → **2 valid laps** → pit
- **Drop IBT.**

### Stint 3 — LS Mid `T+25:00 → T+34:30`
**Change:** Front LS comp = **20** (physics model baseline)
- 3 warm laps → **2 valid laps** → pit
- **Drop IBT.**

### Stint 4 — LS Stiff `T+36:30 → T+48:00`
**Change:** Front LS comp = **30**
- 3 warm laps → **2 valid laps** → pit
- **Drop IBT.**

> **LS=40 skipped** — 3 points (10/20/30) is enough for a linear fit. Use saved 11 min for Acura.

---

## Acura ARX-06 Block (T+50:00 – T+90:00) — 40 min / 3-4 stints

**DB entering:** 5 sessions. Best: 87.599s (Soft/1 + Soft/1, wing=10)
All 5 prior sessions used **wing=10** — no wing variation data yet.

### Stint 5 — Baseline `T+50:00 → T+61:30`
**Setup:** FARB=Soft/1, RARB=Soft/1, wing=10 (matches best observed)
- 3 warm laps → **2 valid laps** → pit
- **Drop IBT.** Validate best observed. Reference for stints 6-7.

### Stint 6 — ARB Stiff `T+63:30 → T+73:00`
**Change:** FARB=Stiff/5, RARB=Stiff/5 (max roll stiffness)
- 3 warm laps → **1 valid lap** → pit *(10 min slot)*
- **Drop IBT.** Brackets LLTD + roll gradient at stiff extreme.

### Stint 7 — Wing Low `T+75:00 → T+85:00`
**Change:** Back to Soft/1 + Soft/1. Wing=**8** (down from 10)
- 3 warm laps → **1 valid lap** → pit *(10 min slot)*
- **Drop IBT.** First wing variation at Hockenheim. Tests if wing=10 is actually optimal.

### Stint 8 — Free / Solver Rec `T+87:00 → T+90:00` *(if time)*
- Check Telegram — auto-solver card will have fired from earlier IBT drops
- 1-2 laps on solver recommendation if time allows

---

## Data Collection Summary

| Stint | Car | Variable | Valid Laps | Calibrates |
|-------|-----|----------|-----------|-----------|
| 1 | Ferrari | Baseline (LS=24) | 2 | Reference anchor |
| 2 | Ferrari | LS comp=10 | 2 | Damper force per click (soft) |
| 3 | Ferrari | LS comp=20 | 2 | Damper force per click (mid) |
| 4 | Ferrari | LS comp=30 | 2 | Damper force per click (stiff) |
| 5 | Acura | Baseline | 2 | Validate best observed |
| 6 | Acura | ARB max stiff | 1 | LLTD range, roll gradient |
| 7 | Acura | Wing=8 | 1 | Wing sensitivity at Hockenheim |

**Drop IBT after every stint — solver ingests automatically (hourly cron)**

---

## Rules
- Only change **one variable per stint** — everything else identical to Stint 1 (Ferrari) or Stint 5 (Acura)
- Use same fuel load each stint for consistent weight
- If a stint feels undriveable, note the direction and pit early — data point still counts
