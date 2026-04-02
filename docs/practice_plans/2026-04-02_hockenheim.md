# Hockenheim Practice Plan — 2026-04-02
**Track:** Hockenheimring GP | **Duration:** 90 min | **Lap time:** ~90s
**Cars:** Ferrari 499P → Acura ARX-06

---

## Time Budget

| Block | Car | Duration | Goal |
|-------|-----|----------|------|
| T+0:00 – T+50:00 | Ferrari | 50 min | Baseline + LS damper sweep |
| T+50:00 – T+90:00 | Acura | 40 min | Baseline + ARB/wing sweep |

**Per stint formula:** 2.5 min outlap + N×1.5 min laps + 1.5 min inlap + 2 min setup change

---

## Ferrari 499P Block (T+0:00 – T+50:00)

### Stint 1 — Baseline `T+0:00 → T+12:00` (12 min)
**Setup:** fh=5, rh=8, ftb=2, rtb=1, FARB=B/1, RARB=C/1, wing=17, LS comp=24 (as-is)
- Outlap → **4 flying laps** → inlap
- **Drop IBT.** Best observed setup (87.575s). Anchors all comparisons.

---

### Stint 2 — LS Damper Soft `T+14:00 → T+23:00` (9 min)
**Change:** Front LS comp = **10** (only change from Stint 1)
- Outlap → **3 flying laps** → inlap
- **Drop IBT.** Soft end of damper range.

---

### Stint 3 — LS Damper Mid `T+25:00 → T+34:00` (9 min)
**Change:** Front LS comp = **20** (new model baseline click)
- Outlap → **3 flying laps** → inlap
- **Drop IBT.** Physics model calibration point.

---

### Stint 4 — LS Damper Stiff `T+36:00 → T+45:00` (9 min)
**Change:** Front LS comp = **30**
- Outlap → **3 flying laps** → inlap
- **Drop IBT.**

---

### Stint 5 — LS Damper Max `T+47:00 → T+56:00` (9 min)
> *Runs 6 min over budget — cut to 2 laps if needed to stay on time*

**Change:** Front LS comp = **40** (max)
- Outlap → **3 flying laps** → inlap
- **Drop IBT.** Completes 4-point sweep: 10/20/30/40 clicks vs lap time delta.

**What this sweep calibrates:** Each IBT gives a (click_count, ShockVel_p99, lap_time) triplet.
Four points = enough to fit a linear force-per-click model and validate/update the 31.3 N/click estimate.

---

## Acura ARX-06 Block (T+50:00 – T+90:00)

**DB state entering block:** 5 Hockenheim sessions. Best lap: 87.599s (Soft/1 + Soft/1, wing=10)
**Note:** All 5 sessions used wing=10 — no wing data yet.

---

### Stint 6 — Baseline `T+52:00 → T+63:00` (11 min)
**Setup:** wing=10, FARB=Soft/1, RARB=Soft/1 (matches best observed)
- Outlap → **4 flying laps** → inlap
- **Drop IBT.** Validate best observed setup + get fresh comparison reference.

---

### Stint 7 — ARB Stiff Extreme `T+65:00 → T+74:00` (9 min)
**Change:** FARB=Stiff/5, RARB=Stiff/5 (max roll stiffness)
- Outlap → **3 flying laps** → inlap
- **Drop IBT.** Brackets LLTD and roll gradient at stiff end.

---

### Stint 8 — Wing High `T+76:00 → T+85:00` (9 min)
**Change:** Back to Soft/1 + Soft/1 ARB. Wing=8 (lower — all prior sessions at 10)
- Outlap → **3 flying laps** → inlap
- **Drop IBT.** First wing variation data for Acura at Hockenheim.

---

### Stint 9 — Solver Recommendation `T+87:00 → ~T+90:00` (buffer)
- Check Telegram — auto-solver will have sent a card from earlier IBT drops
- Run 1-2 laps on whatever the solver recommended if time allows

---

## Data Collection Summary

| Car | Sweep | Variables | Calibrates |
|-----|-------|-----------|-----------|
| Ferrari | LS damper (4 pts) | LS comp: 10/20/30/40 | Force per click (31.3 N/click model) |
| Acura | ARB extreme | Soft vs Stiff | LLTD range, roll gradient |
| Acura | Wing | 10 vs 8 | DF balance at Hockenheim |

### Drop IBT after EVERY stint — solver ingests automatically (hourly cron)

---

## Notes
- Keep ALL other variables constant within each sweep — only change the target parameter
- If a stint feels undriveable, note which direction and cut it short — that data point still counts
- Ferrari Stint 5 can be skipped if running late — 3 sweep points (10/20/30) is sufficient for a linear fit
- Acura wing=10 may be optimal here (Hockenheim is high-speed) — wing=8 tests that assumption
