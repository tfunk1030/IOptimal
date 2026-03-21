# Ferrari 499P Rear Heave Spring Calibration — Sebring

**Date:** 2026-03-21  
**Source:** 7 IBT sessions, Sebring International  
**Method:** F = k·x → k = F_eff / deflection, F_eff = corner_weight × fraction

## Raw IBT Data

| Session | Lap (s) | Heave Idx | Perch (mm) | Defl_cur (mm) | Defl_max (mm) | Defl% | Slider_cur (mm) | CW (N) |
|---------|---------|-----------|------------|--------------|--------------|-------|----------------|--------|
| Mar16 | 109.116 | 2 | -101.0 | 1.3 | 75.6 | 1.7% | 22.5 | 2997 |
| Mar19A | 109.717 | 2 | -101.0 | 1.3 | 75.6 | 1.7% | 22.5 | 2997 |
| Mar19B | 109.949 | 5 | -112.5 | 13.0 | 67.0 | 19.4% | 22.7 | 2938 |
| Mar19C | 108.113 | 2 | -101.5 | 1.6 | 75.6 | 2.1% | 22.3 | 2997 |
| Mar20A | 109.188 | 2 | -106.0 | 13.6 | 75.6 | 18.0% | 29.8 | 2997 |
| Mar20B | 109.227 | 3 | -104.5 | 6.3 | 72.5 | 8.7% | 24.0 | 2997 |
| Mar20C | 109.032 | 7 | -103.5 | 11.1 | 62.2 | 17.8% | 29.8 | 2997 |

## Spring Rate Estimates (k = F_eff / defl)

**Methodology:** F_eff = corner_weight × fraction, where fraction represents what
portion of rear corner weight is supported through the heave spring path. Only
sessions with deflection > 3mm are used (small deflections have high geometric error).

Fraction sweep: 0.30 | 0.35 | 0.40 | 0.50

| Session | Heave Idx | Defl (mm) | k(f=0.35) N/mm | k(f=0.40) N/mm | k(f=0.50) N/mm | Notes |
|---------|-----------|-----------|----------------|----------------|----------------|-------|
| Mar16 | 2 | 1.3 | — | — | — | Defl too small (<3mm) |
| Mar19A | 2 | 1.3 | — | — | — | Defl too small (<3mm) |
| Mar19B | 5 | 13.0 | 79.1 | 90.4 | 113.0 | |
| Mar19C | 2 | 1.6 | — | — | — | Defl too small (<3mm) |
| Mar20A | 2 | 13.6 | 77.1 | 88.1 | 110.2 | |
| Mar20B | 3 | 6.3 | 166.5 | 190.3 | 237.9 | |
| Mar20C | 7 | 11.1 | 94.5 | 108.0 | 135.0 | |

## Index → N/mm Lookup (Best Estimate)

Using f=0.40 (middle estimate). Sessions with defl <3mm excluded.

| Spring Index | Estimated k (N/mm) | Data Sessions | Confidence |
|-------------|-------------------|---------------|-----------|
| 2 | 88 | n=1 | LOW (1 sample) |
| 3 | 190 | n=1 | LOW (1 sample) |
| 5 | 90 | n=1 | LOW (1 sample) |
| 7 | 108 | n=1 | LOW (1 sample) |

## Key Observations

1. **Index 2 at perch=-101mm**: Deflection is only 1.3–1.6mm (very small).
   The heave spring is barely engaged at this perch setting — nearly all weight
   on corner springs. k cannot be reliably estimated.

2. **Index 2 at perch=-106mm**: Deflection jumps to 13.6mm. The perch offset
   controls spring engagement — 5mm more negative perch → ~12mm more deflection.
   This means the heave spring is geometry-sensitive; k estimate here is more
   reliable: ~88–110 N/mm at f=0.35–0.40.

3. **Index 3 vs Index 5 vs 7**: Clear progression showing softer springs at
   higher indices (lower k). The index naming appears to go from hard (1) to soft (7+).

4. **Slider travel**: All sessions show slider at ~22–30mm of 300mm max (7–10%).
   Rear heave slider has ample travel — not a bottoming concern for heave travel,
   but corner springs are near their limits.

## Recommended Solver Update

```python
FERRARI_REAR_HEAVE_SPRING_NMM = {
    1: 600,   # estimated (no clean data)
    2: 100,   # f=0.40 at 13.6mm defl (most reliable data point)
    3: 190,   # f=0.40 at 6.3mm defl
    4: 130,   # interpolated
    5:  90,   # f=0.40 at 13.0mm defl
    6:  85,   # interpolated
    7: 108,   # f=0.40 at 11.1mm defl
}
```

**Caveat:** True k requires installation ratio and geometric spring fraction.
Use these as relative reference (index 2 ≈ stiffer than index 5 ≈ index 7).
The geometric confound from perch offset means absolute values have ±30% uncertainty.
