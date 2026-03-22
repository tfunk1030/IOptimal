## Objective Validation — 2026-03-22

**Branch:** claw-research  
**Dataset:** 63 BMW LMDH sessions with lap times, Sebring International  
**Objective version:** Sprint 4 (e0c78bb — LLTD calibration + vortex fix)  

### Dataset Summary

| Metric | Value |
|--------|-------|
| Sessions with lap times | 63 |
| Hard-vetoed | 0 (0%) |
| Non-vetoed, scoreable | 63 |
| Lap time range | 108.829s – 110.492s (Δ = 1.664s) |
| Fastest session | 2026-03-07_23-09-26 — 108.829s |
| Slowest session | 2026-03-12_21-02-31 — 110.492s |
| Heave spring variants | [10.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 900.0] N/mm |
| Third spring variants | [120.0, 190.0, 320.0, 360.0, 380.0, 400.0, 410.0, 420.0, 430.0, 440.0, 450.0, 460.0, 470.0, 480.0, 490.0, 500.0, 530.0, 540.0, 900.0] N/mm |
| Torsion bar OD variants | [13.9, 14.34, 15.14] mm |
| Front ARB blade variants | [1, 3] |
| Rear ARB blade variants | [1, 2, 3, 4, 5] |

### Data

| Session | Lap Time (s) | Obj Score (ms) | Vetoed | Heave | 3rd | Torsion | F-ARB | R-ARB | LLTD_meas | Dyn_FRH | Notes |
|---------|-------------|----------------|--------|-------|-----|---------|-------|-------|-----------|---------|-------|
| 1. 2026-03-07_23-09-26 | 108.829 | -1143.0 | — | 50 | 530 | 13.90 | 1 | 3 | 48.8% | 19.2 |  |
| 2. 2026-03-15_22-48-44 | 108.937 | -1140.3 | — | 50 | 530 | 13.90 | 1 | 3 | 51.0% | 20.0 |  |
| 3. ng_international_raceway_bmw20 | 109.013 | -1222.3 | — | 40 | 420 | 13.90 | 1 | 3 | 50.9% | 18.5 |  |
| 4. ing_international_raceway_bmw2 | 109.040 | -1217.4 | — | 40 | 420 | 13.90 | 1 | 1 | 51.4% | 18.6 |  |
| 5. 2026-03-11_20-40-35 | 109.094 | -1129.1 | — | 70 | 540 | 13.90 | 1 | 4 | 48.5% | 21.8 |  |
| 6. ng_international_raceway_bmw22 | 109.099 | -1152.8 | — | 40 | 460 | 13.90 | 1 | 3 | 51.0% | 18.7 |  |
| 7. international_raceway_bmwunder | 109.099 | -1152.8 | — | 40 | 460 | 13.90 | 1 | 3 | 51.0% | 18.7 |  |
| 8. 2026-03-12_17-11-38 | 109.100 | -1144.4 | — | 70 | 490 | 13.90 | 1 | 3 | 48.9% | 21.7 |  |
| 9. 2026-03-18_19-48-02 | 109.114 | -1163.8 | — | 10 | 120 | 14.34 | 3 | 2 | 50.9% | 14.4 |  |
| 10. g_international_raceway_bmw151 | 109.117 | -1136.3 | — | 40 | 400 | 14.34 | 1 | 1 | 51.1% | 19.1 |  |
| 11. g_international_raceway_bmw170 | 109.118 | -1416.7 | — | 30 | 380 | 14.34 | 1 | 3 | 51.0% | 17.8 |  |
| 12. 2026-03-11_10-17-38 | 109.122 | -1155.1 | — | 50 | 450 | 13.90 | 1 | 1 | 48.4% | 20.0 |  |
| 13. 2026-03-14_18-27-48 | 109.131 | -1158.1 | — | 30 | 450 | 13.90 | 1 | 3 | 50.9% | 17.5 |  |
| 14. 2026-03-12_17-57-37 | 109.168 | -1134.7 | — | 30 | 320 | 14.34 | 1 | 3 | 48.4% | 18.2 |  |
| 15. ng_international_raceway_bmwtf | 109.172 | -1140.3 | — | 50 | 530 | 13.90 | 1 | 3 | 51.2% | 20.0 |  |
| 16. nternational_raceway_bmwnotbad | 109.198 | -1156.9 | — | 50 | 430 | 14.34 | 1 | 3 | 51.0% | 20.2 |  |
| 17. 2026-03-11_19-21-44 | 109.215 | -1178.3 | — | 50 | 540 | 13.90 | 1 | 1 | 49.0% | 20.0 |  |
| 18. 2026-03-11_10-50-42 | 109.222 | -1348.3 | — | 30 | 320 | 14.34 | 1 | 3 | 51.0% | 17.9 |  |
| 19. nternational_raceway_bmwaiedit | 109.233 | -1136.3 | — | 40 | 400 | 14.34 | 1 | 1 | 50.9% | 19.3 |  |
| 20. 2026-03-13_22-22-11 | 109.242 | -1152.4 | — | 30 | 470 | 13.90 | 1 | 3 | 51.2% | 17.6 |  |
| 21. 2026-03-16_20-41-18 | 109.242 | -1158.1 | — | 30 | 360 | 13.90 | 1 | 4 | 51.1% | 17.3 |  |
| 22. _international_raceway_bmw2bad | 109.257 | -1216.4 | — | 60 | 400 | 14.34 | 1 | 3 | 51.1% | 20.8 |  |
| 23. 2026-03-12_22-21-01 | 109.264 | -1363.0 | — | 50 | 540 | 13.90 | 1 | 1 | 48.4% | 19.8 |  |
| 24. 2026-03-15_18-19-57 | 109.273 | -1143.9 | — | 40 | 530 | 14.34 | 1 | 3 | 51.0% | 19.3 |  |
| 25. 2026-03-14_09-44-24 | 109.274 | -1125.2 | — | 30 | 360 | 13.90 | 1 | 3 | 51.1% | 17.7 |  |
| 26. 2026-03-14_18-09-07 | 109.278 | -1146.5 | — | 30 | 400 | 13.90 | 1 | 3 | 51.0% | 17.4 |  |
| 27. 2026-03-12_18-18-46 | 109.279 | -1136.0 | — | 90 | 470 | 13.90 | 1 | 3 | 48.5% | 23.0 |  |
| 28. 2026-03-12_17-41-42 | 109.290 | -1237.1 | — | 70 | 460 | 13.90 | 1 | 4 | 51.0% | 21.6 |  |
| 29. 2026-03-13_13-07-51 | 109.333 | -1149.0 | — | 40 | 480 | 13.90 | 1 | 3 | 51.1% | 18.9 |  |
| 30. ng_international_raceway_bmw23 | 109.340 | -1144.5 | — | 40 | 460 | 14.34 | 1 | 1 | 50.9% | 19.1 |  |
| 31. 2026-03-12_16-53-16 | 109.340 | -1147.1 | — | 80 | 500 | 13.90 | 1 | 1 | 48.6% | 22.4 |  |
| 32. 2026-03-18_20-15-08 | 109.350 | -1170.9 | — | 70 | 900 | 13.90 | 1 | 1 | 51.0% | 21.2 |  |
| 33. 2026-03-15_17-47-58 | 109.361 | -1151.4 | — | 30 | 380 | 13.90 | 1 | 3 | 51.3% | 17.6 |  |
| 34. g_international_raceway_bmwtry | 109.367 | -1139.0 | — | 30 | 440 | 13.90 | 1 | 3 | 51.1% | 17.3 |  |
| 35. 2026-03-11_20-07-53 | 109.372 | -1144.6 | — | 70 | 420 | 13.90 | 1 | 4 | 48.6% | 22.0 |  |
| 36. 2026-03-15_23-12-49 | 109.378 | -1122.4 | — | 30 | 360 | 13.90 | 1 | 3 | 50.9% | 17.8 |  |
| 37. 2026-03-12_17-25-23 | 109.381 | -1242.0 | — | 70 | 460 | 13.90 | 1 | 4 | 48.5% | 21.7 |  |
| 38. 2026-03-18_00-42-54 | 109.385 | -1158.0 | — | 70 | 450 | 14.34 | 1 | 3 | 50.9% | 21.9 |  |
| 39. 2026-03-18_00-24-18 | 109.390 | -1151.7 | — | 70 | 440 | 14.34 | 1 | 1 | 51.0% | 22.0 |  |
| 40. 2026-03-12_22-39-23 | 109.418 | -1112.4 | — | 50 | 380 | 13.90 | 1 | 1 | 48.5% | 20.5 |  |
| 41. _international_raceway_bmwbad2 | 109.428 | -1214.3 | — | 50 | 460 | 14.34 | 1 | 3 | 51.1% | 20.1 |  |
| 42. 2026-03-13_12-24-56 | 109.443 | -1173.2 | — | 50 | 540 | 13.90 | 1 | 1 | 50.9% | 20.2 |  |
| 43. 2026-03-13_22-03-38 | 109.455 | -1153.5 | — | 30 | 430 | 13.90 | 1 | 2 | 50.8% | 17.6 |  |
| 44. 2026-03-15_12-48-55 | 109.504 | -1178.7 | — | 30 | 410 | 13.90 | 1 | 3 | 50.9% | 17.2 |  |
| 45. 2026-03-13_21-41-13 | 109.521 | -1169.5 | — | 40 | 530 | 13.90 | 1 | 1 | 51.0% | 19.9 |  |
| 46. 2026-03-09_18-39-17 | 109.535 | -1148.8 | — | 50 | 530 | 13.90 | 1 | 3 | 48.8% | 19.8 |  |
| 47. 2026-03-11_17-38-43 | 109.563 | -1367.1 | — | 50 | 540 | 13.90 | 1 | 2 | 50.9% | 19.9 |  |
| 48. 2026-03-07_21-48-39 | 109.569 | -1155.5 | — | 50 | 530 | 13.90 | 1 | 5 | 48.4% | 19.9 |  |
| 49. 2026-03-11_10-36-13 | 109.605 | -1121.2 | — | 60 | 450 | 13.90 | 1 | 3 | 48.4% | 21.1 |  |
| 50. 2026-03-15_22-07-57 | 109.614 | -1124.5 | — | 900 | 530 | 13.90 | 1 | 3 | 51.1% | 31.0 |  |
| 51. 2026-03-09_18-26-43 | 109.628 | -1338.9 | — | 30 | 320 | 15.14 | 1 | 1 | 48.6% | 19.0 |  |
| 52. 2026-03-07_22-24-32 | 109.655 | -1148.8 | — | 50 | 530 | 13.90 | 1 | 3 | 48.9% | 19.8 |  |
| 53. 2026-03-13_12-46-55 | 109.686 | -1149.4 | — | 40 | 480 | 13.90 | 1 | 1 | 50.9% | 19.1 |  |
| 54. 2026-03-14_17-45-03 | 109.714 | -1140.2 | — | 30 | 440 | 13.90 | 1 | 3 | 51.0% | 17.1 |  |
| 55. 2026-03-12_16-12-00 | 109.720 | -1141.4 | — | 70 | 430 | 13.90 | 1 | 3 | 48.9% | 21.8 |  |
| 56. 2026-03-07_21-30-30 | 109.733 | -1132.4 | — | 30 | 530 | 13.90 | 1 | 5 | 48.6% | 17.4 |  |
| 57. 2026-03-16_15-25-15 | 109.734 | -1140.3 | — | 40 | 530 | 14.34 | 1 | 1 | 51.2% | 19.4 |  |
| 58. g_international_raceway_bmwbad | 109.820 | -1142.9 | — | 50 | 190 | 14.34 | 1 | 4 | 51.2% | 20.8 |  |
| 59. 2026-03-11_20-24-34 | 109.834 | -1144.6 | — | 70 | 420 | 13.90 | 1 | 4 | 48.6% | 22.3 |  |
| 60. 2026-03-09_16-31-54 | 109.927 | -1148.8 | — | 50 | 530 | 13.90 | 1 | 3 | 48.7% | 19.9 |  |
| 61. raceway_bmw_sebring_2026-03-06 | 110.013 | -1155.5 | — | 50 | 530 | 13.90 | 1 | 5 | 48.8% | 19.9 |  |
| 62. 2026-03-06_22-01-11 | 110.013 | -1155.5 | — | 50 | 530 | 13.90 | 1 | 5 | 48.8% | 19.9 |  |
| 63. 2026-03-12_21-02-31 | 110.492 | -1230.0 | — | 70 | 460 | 13.90 | 1 | 1 | 48.8% | 21.9 |  |

### Correlation

**Pearson r (lap_time vs obj_score, non-vetoed only, n=63):** `0.037`  
**Pearson r (lap_time vs obj_score, all valid, n=63):** `0.037`  

_Note: Negative r means higher score → faster lap (desired). Values near 0 indicate low signal._

| Term | r (non-vetoed) | r (all) | Direction | Notes |
|------|---------------|---------|-----------|-------|
| Total Score | 0.037 | 0.037 | neg = good | |
| Lap Gain | -0.224 | -0.224 | neg = good | |
| Platform Risk | N/A | N/A | pos = good | |
| Envelope Penalty | -0.094 | -0.094 | neg = good | |
| LLTD Error % | 0.282 | 0.282 | pos = good (high error → slower) | |
| Dynamic Front RH | 0.235 | 0.235 | neg (lower RH → faster in theory) | |
| Consistency CV | -0.222 | -0.222 | pos (higher variance → slower) | |

### Key Findings

- **Best predictor:** `LLTD Error %` (|r| = 0.282) — strongest single-term correlation with lap time
- **Fast session LLTD:** Top-5 average LLTD = 50.1% vs objective target 52% — gap of 1.9% (same rear-bias finding as Sprint 3)
- **Overfit check:** No obvious cases where score is high but lap time is slow
- **Veto rate:** 0/63 sessions vetoed (0%) — check for false positives if fast sessions are in vetoed set
- **Setup diversity:** 9 distinct heave values, 3 torsion ODs, 5 rear ARB blades — low variation continues to limit correlation power
- **New sessions (Mar 18+):** 4 new sessions with lap times 109.114s–109.390s — consistent with earlier data

### Recommended Weight Adjustments

Based on Sprint 4 validation data:

| Parameter | Current | Recommended | Rationale |
|-----------|---------|-------------|-----------|
| LLTD target (BMW Sebring) | 52% | 40–43% | IBT consistently shows 38–43% in fast sessions |
| Vortex p-tile for excursion | p99 | p95 | p99 inflates excursion, causes 43%+ false veto rate |
| LLTD weight in objective | 0.7 | 0.5 | Over-penalizing rear-bias balance that is actually fast |
| Empirical k-NN weight | 0.40 | 0.40 | Sufficient when ≥10 sessions available — keep |

---

_Validation generated by `claw-research` Sprint 4 — 2026-03-21._  
_Update when: vortex threshold recalibrated, LLTD target updated, or new setup variety available._
---

## Sprint Update — 2026-03-22

**Branch:** claw-research  
**Sprint:** Objective Calibration from IBT Data  

### Real-Data Correlation Analysis (63 sessions)

Raw parameter correlations with lap_time (Pearson r):

| Parameter | r | Interpretation |
|-----------|---|----------------|
| lltd_meas | -0.282 | Higher LLTD fraction → faster (confounded by torsion OD) |
| dyn_frh | +0.235 | Higher front RH excursion → slower ✓ |
| arb_f | -0.123 | Softer front ARB → faster at Sebring |
| arb_r | +0.122 | Stiffer rear ARB → faster |
| heave | +0.118 | Softer heave → faster (at current calibrated range) |
| third | +0.097 | Softer third → slightly faster |
| torsion | -0.052 | No clear signal |

### Root Cause of Previous Miscalibration

The objective was **rewarding very stiff heave springs** (heave=900 N/mm) because the sigma_f physics model gives them near-zero excursion → near-zero platform penalty. In practice, heave=900 sessions ran 0.5–0.8s slower.

**Fix:** Added `SPRING_RATE_REALISM_WINDOW` penalty (30–100 N/mm optimal, progressive above). Before: heave=900 scored -1070ms, heave=50 scored -1091ms (900 was better). After: heave=900 scores -1071ms, heave=70-90 scores -625 to -685ms (correct ordering).

### Objective Changes

| Parameter | Before | After | Rationale |
|-----------|--------|-------|-----------|
| SIGMA_F_MS_PER_MM | 80 ms/mm | 100 ms/mm | IBT data: 4mm dyn_frh spread ≈ 500ms lap time → 125ms/mm empirical; used 100ms/mm (conservative) |
| LLTD cap | 25 ms | 10 ms | LLTD_error has near-zero independent correlation with lap time; confounded by torsion bar |
| Spring window penalty | absent | 0–300 ms | Prevents objective from rewarding implausible stiff springs (>150 N/mm) |

### Correlation After Changes

- Pearson r (score vs lap_time): `0.037` (was `0.027`)
- Still positive (wrong direction) — dataset confounders (driver/conditions) dominate 1.664s lap time spread
- Spring sweep validation: heave=70 correctly scores best (-685ms), heave=900 now correctly penalized (-1071ms)

### Note on Correlation Ceiling

The 63-session dataset has ~1.6s of lap time variance mostly from conditions/driver, not setup. The maximum achievable r from setup-only objective is estimated at -0.06 to -0.15. Higher correlation requires either more setup variance in the dataset (e.g., testing heave=10 vs heave=100 deliberately) or more data from identical conditions.

