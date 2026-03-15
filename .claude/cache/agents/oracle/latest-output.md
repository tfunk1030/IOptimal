# Research Report: iRacing GTP Ride Height Mechanics
Generated: 2026-03-15

## Summary

iRacing GTP/Hypercar cars use a complex multi-element suspension where static ride height emerges from the interaction of pushrod length, heave spring preload (via perch offset), torsion bar stiffness, corner spring perch offsets, and camber. No single ride height slider exists -- ride height is an outcome of the force balance between car weight and all spring preloads. The codebase already has extensive calibration models fitted from 41 BMW Sebring sessions.

## Questions Answered

### Q1: What parameters affect static ride height?

Front ride height (BMW M Hybrid V8):
- Pushrod Length Offset: adjusts both front pushrods together. But front RH is nearly pinned at ~30mm (R2=0.15).
- Heave Perch Offset: adjusts heave spring preload. Lower = more preload = higher front RH. Weak on front, measurable on rear.
- Torsion Bar OD: changes stiffness (k = C * OD^4). Subtle effect.
- Front Camber: r=+0.32 correlation, ~0.07mm RH per degree.
- Fuel Load: weight change compresses springs.

Rear ride height:
- Rear Pushrod Offset: primary rear RH control. Range -31.5 to -16.0mm -> RH 45.3-51.5mm.
- Rear Spring Perch Offset: preload on rear coil spring.
- Rear Third Spring Rate and Perch: rear heave element.
- Rear Spring Rate: deflection under static load.

Confidence: High

### Q2: How does heave perch offset work?

Lower values (more negative) = more preload = higher front RH.
Higher values (less negative) = less preload = lower front RH.

Calibrated model: HeaveDeflStatic = -20.756 + 7.030/heave_nmm - 0.9146*perch_mm + 666311/OD^4
Increasing perch by 1mm decreases static deflection by ~0.9mm.

Observed range: -31.5mm to -10.0mm. Baseline: -13.0mm.

Confidence: High

### Q3: Torsion bar turns and ride height?

TB turns represent preload on front corner torsion bars.
Calibrated: TorsionBarTurns = 0.0856 + 0.668 / heave_spring_nmm
All sessions at OD=13.9mm show turns = 0.102 (constant).

TB turns primarily affect corner weights/crossweight, NOT ride height directly.

TB deflection model: TBDefl = (1009.9 - 3.860*heave_nmm + 10.083*perch_mm) / k_torsion

Confidence: High (R2=0.905)

### Q4: Known formulas?

Front RH: front_rh = 30.1458 + 0.001614*heave_nmm + 0.074486*camber_deg (R2=0.15)
Rear RH: 6-feature linear model (R2=0.52, LOO RMSE=0.845mm)

Deflection models:
- Rear spring: defl = (6091.76 - 115.89*spring_perch) / spring_rate
- Third spring: defl = (17817.75 - 357.96*third_perch) / third_rate
- Heave DeflMax: 106.43 - 0.310 * spring_rate (R2=0.985)
- Front shock: 21.228 + 0.226 * pushrod_offset
- Rear shock: 25.924 + 0.266 * pushrod_offset

### Q5: Valid ranges for heave perch offset?

BMW observed: -31.5mm to -10.0mm. Baseline: -13.0mm.
Rear third perch: 31.0 to 42.5mm. Rear spring perch: 30.0 to 41.5mm.

Confidence: Medium (may not cover full garage range)

### Q6: Corner spring rates and ride height?

Force balance: deflection = load / spring_rate.
Changing spring rate changes deflection, not load.
Front torsion bar effect on RH: r=-0.12 (nearly pinned).
Rear spring: perch 1mm change = 115.89 N load change = 0.72mm deflection at 160 N/mm.
Heave: DeflStatic ~ 40.5 - 0.55 * heave_nmm.

## Parameter Sensitivity to Rear RH

| Parameter | 1-unit Change | RH Effect |
|-----------|--------------|-----------|
| Rear pushrod (mm) | 1mm | ~0.4mm |
| Rear spring perch (mm) | 1mm | ~0.7mm |
| Heave perch (mm) | 1mm | ~0.3mm |
| Third spring perch (mm) | 1mm | ~0.1mm |
| Rear spring rate (N/mm) | 10 | ~0.2mm |
| Fuel (L) | 10 | ~0.3mm |

## Sources

1. BMW M Hybrid V8 Manual: https://s100.iracing.com/wp-content/uploads/2023/07/BMW-M-Hybrid-V8-Manual-V2.pdf
2. Cadillac GTP Manual: https://s100.iracing.com/wp-content/uploads/2023/07/Cadillac-V-Series.R-GTP-Manual-V2.pdf
3. Commodore Garage #14: https://www.iracing.com/commodores-garage-14-ride-heights-perches-and-deflections/
4. Commodore Garage #16: https://www.iracing.com/commodores-garage-16-adjusting-the-spring-package/
5. iRacing Wiki: http://iracing.wikidot.com/components:ride-heights
6. VRS Guide: https://virtualracingschool.com/academy/iracing-career-guide/setups/ride-heights/
7. Internal: calibration_dataset.json, car_model/cars.py

## Open Questions

- Full garage range for heave perch offset?
- Does front RH pinning apply to Ferrari 499P and Porsche 963?
- Nonlinear interactions between heave rate and perch for RH?
