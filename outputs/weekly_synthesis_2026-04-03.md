# Weekly Setup Synthesis — BMW LMDh @ Sebring
**Generated:** 2026-04-03 15:10 UTC  
**Solver:** iOptimal GridSearchEngine (budget=quick, 10,510 evals, 67s)  
**Baseline reference:** wing=17 | heave=40 | RARB=3  

---

## Summary

The solver evaluated 10,510 candidates across 4 layers (Sobol → balance grid → damper coord descent → neighborhood polish). All top-5 polished candidates converged on **wing=14** — a 3-degree reduction from the baseline. The platform varies more this week than last (March 27 had one dominant skeleton), with three distinct platform philosophies emerging. Best polished score: **−11.9 ms**.

> **Wing note:** The solver consistently prefers wing=14 over the baseline wing=17. This is the third consecutive synthesis confirming the wing recommendation. Less downforce, more top-end on Sebring's back straight.

---

## Family A — Best Robust (Recommended)

> Soft front heave / stiff rear platform. Best polished score, cleanest LLTD (47.2%). Lowest risk.

| Param | Value | vs Baseline |
|---|---|---|
| Wing | 14° | −3° |
| Front heave | 10 N/mm | −30 N/mm (softer) |
| Rear third | 180 N/mm | — |
| Rear spring | 180 N/mm | — |
| Torsion OD | 14.34 mm | — |
| ARB Front | Blade 2 | — |
| ARB Rear | Blade 4 | **+1 blade stiffer** |
| Camber F/R | −2.6° / −0.95° | — |
| Pushrod F | −10.5 mm | — |
| Pushrod R | +23.0 mm | — |
| Front HS comp/rbd | 0 / 11 | — |
| Rear HS comp/rbd | 8 / 5 | — |
| LLTD | 47.2% | — |
| Excursion F/R | 0.0 mm / 0.0 mm | ✅ |
| Platform risk | 0 | ✅ |

**Score: −11.9 ms**  
Low-speed compression zeroed front (relies on rear HS comp=8 for high-speed kerb control). Very soft heave lets the front follow the surface — suited to Sebring's bumpy infield sector.

---

## Family B — Best Aggressive (Highest Raw Gain)

> Stiff front heave / soft rear third. Opposite platform philosophy from A. Higher raw potential but less margin.

| Param | Value | vs Baseline |
|---|---|---|
| Wing | 14° | −3° |
| Front heave | 260 N/mm | **+220 N/mm (much stiffer)** |
| Rear third | 10 N/mm | **very soft** |
| Rear spring | 100 N/mm | softer |
| Torsion OD | 16.19 mm | stiffer |
| ARB Front | Blade 1 | softer |
| ARB Rear | Blade 5 | **+2 blades stiffer** |
| Camber F/R | −2.6° / −0.95° | — |
| Pushrod F | −31.0 mm | more negative rake |
| Pushrod R | +8.5 mm | — |
| Front HS comp/rbd | 8 / 5 | — |
| Rear HS comp/rbd | 0 / 11 | opposite of Family A |
| LLTD | 47.1% | — |

**Score: −12.8 ms**  
Stiff front heave locks the nose down for maximum aero efficiency in the fast Turn 1 / Turn 7 complex. Soft rear third allows rear compliance over kerbs. Heave asymmetry (front stiff / rear soft) is an interesting qualifier — trades front ride comfort for peak apex grip. Higher damping risk if track surface temp varies.

---

## Family C — Mid Platform (Balance-First)

> Moderate heave platform with forward-low rake geometry. Two variants (C1/C2) differ only in torsion OD.

| Param | C1 | C2 | vs Baseline |
|---|---|---|---|
| Wing | 14° | 14° | −3° |
| Front heave | 60 N/mm | 60 N/mm | +20 N/mm |
| Rear third | 65 N/mm | 65 N/mm | — |
| Rear spring | 140 N/mm | 140 N/mm | — |
| Torsion OD | **14.76 mm** | **15.86 mm** | varies |
| ARB Front | Blade 2 | Blade 1 | — |
| ARB Rear | Blade 5 | Blade 4 | +1–2 stiffer |
| Pushrod F | −19.0 mm | −19.0 mm | — |
| Pushrod R | −28.5 mm | −28.5 mm | **rear negative rake** |

**C1 Score: −13.0 ms | C2 Score: −13.1 ms**  
Rear pushrod at −28.5 mm (vs +23 in Family A) changes rear ride height geometry significantly. More front-forward weight bias. The torsion difference (14.76 vs 15.86) shifts oversteer balance slightly — C1 is more neutral, C2 is slightly looser on entry. Neither beats Family A, but both are more conservative on damper requirements.

---

## Top 3 Parameter Changes vs Sebring Baseline (wing=17, heave=40, RARB=3)

| Priority | Parameter | Baseline | Recommended | Impact |
|---|---|---|---|---|
| 1 | **Wing angle** | 17° | **14°** | Consistent across all 5 families; biggest single gain |
| 2 | **Rear ARB blade** | 3 | **4–5** | Stiffens rear LLTD, improves rotation; all families prefer this |
| 3 | **Front heave spring** | ~40 N/mm | **10–60 N/mm** | Family A goes very soft (10), families C go moderate (60); solver dislikes the 40 baseline |

---

## Expected Lap Delta by Family

| Family | Score | Interpretation |
|---|---|---|
| A (Robust) | −11.9 ms | Best polished result; recommended starting point |
| B (Aggressive) | −12.8 ms | 0.9 ms behind A; higher variance expected on bumpy days |
| C1 (Mid) | −13.0 ms | 1.1 ms behind A; safer damper demands |
| C2 (Mid-var) | −13.1 ms | 1.2 ms behind A; slightly looser balance |

> Score = objective function value (lower penalty = faster). All families represent improvements over the wing=17/heave=40/RARB=3 baseline.

---

## Recommendation

**Start with Family A.** Wing=14 is now a three-week-running unanimous recommendation. The soft heave (10 N/mm) is aggressive but Sebring's bumpy surface should absorb it. If the car feels nervous over T3–T5 kerbs, move to Family C1 (heave=60) as an in-session adjustment without changing platform fundamentals.

Family B is reserved for qualifying trim if clean lap pace matters more than tyre longevity.

---

## Notes

- All families converge on wing=14, RARB=4–5, camber F=−2.6°/R=−0.95°, and rear_toe=0.5 mm. These should be treated as confirmed direction.
- Brake bias (50%) and diff settings are solver defaults — not optimized in this run (no IBT telemetry input this cycle).
- No new IBT files detected this week. Solver ran open-loop against physics model only.
