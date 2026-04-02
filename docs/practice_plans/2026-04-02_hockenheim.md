# Hockenheim Practice Plan — 2026-04-02
**Track:** Hockenheimring GP | **Duration:** 90 min | **Lap time:** ~90s
**Cars:** Ferrari 499P → Acura ARX-06

## Stint Structure
Every stint: **Outlap → Push L1 → Push L2 → Inlap → pit/setup change (~2 min)**
= 4 laps × 1.5 min = 6 min + 2 min = **~8 min per stint**

---

## Ferrari 499P Block (T+0:00 – T+44:00) — 5 stints

| # | Time | LS comp | Setup | Notes |
|---|------|---------|-------|-------|
| 1 | T+0:00 | 24 (baseline) | fh=5, rh=8, FARB=B/1, RARB=C/1, wing=17 | Reference — best observed (87.575s) |
| 2 | T+10:00 | **10** | same as above | Soft end |
| 3 | T+20:00 | **20** | same | Physics model baseline click |
| 4 | T+30:00 | **30** | same | Stiff end |
| 5 | T+40:00 | **40** | same | Max |

**Drop IBT after every stint.**
Only variable changing: front LS comp. Everything else locked to Stint 1.

---

## Acura ARX-06 Block (T+46:00 – T+90:00) — 5 stints

| # | Time | Config | Notes |
|---|------|--------|-------|
| 6 | T+46:00 | FARB=Soft/1, RARB=Soft/1, wing=10 | Baseline — matches best observed (87.599s) |
| 7 | T+56:00 | FARB=Stiff/5, RARB=Stiff/5, wing=10 | ARB stiff extreme |
| 8 | T+66:00 | FARB=Soft/1, RARB=Soft/1, wing=**8** | First wing variation |
| 9 | T+76:00 | FARB=Medium/1, RARB=Medium/5, wing=10 | Best prior non-baseline (88.048s) |
| 10 | T+86:00 | Solver rec | Check Telegram card, run it |

**Drop IBT after every stint.**

---

## What This Calibrates

| Stints | Sweep | Model it feeds |
|--------|-------|---------------|
| 1–5 | Ferrari LS comp 10→40 | Force per click (31.3 N/click validation) |
| 6–7 | Acura ARB soft vs stiff | LLTD range, roll gradient |
| 8 | Acura wing 10 vs 8 | DF balance at Hockenheim |
| 9 | Acura best prior | k-NN baseline validation |
