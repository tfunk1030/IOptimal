# GT3 Per-Car Spec Sheet — iRacing 2026 Season 2

Source-of-truth document for `CarModel` field population. Aggregated from iRacing user manual PDFs (highest confidence), real-world FIA GT3 homologation data (sanity-check), Coach Dave Academy / Garage61 / community forums (qualitative). Every field is tagged `[MANUAL]`, `[REAL-WORLD]`, `[COMMUNITY]`, or `PENDING_IBT`.

The 11 GT3 cars in the 2026 S2 grid are: BMW M4, Mercedes-AMG (2020), Aston Martin Vantage EVO, Ferrari 296, Lamborghini Huracán EVO, McLaren 720S EVO, Porsche 911 GT3 R (992), Acura NSX EVO22, Audi R8 LMS evo II, Ford Mustang, Corvette Z06 GT3.R. The user's aero map collection contains 10 of these (no Audi).

## Cross-cutting architectural facts (all 11 cars)

- Double-wishbone front and rear (Aston EVO and Mercedes have multi-link rear in real life; iRacing typically homogenizes to dwb)
- **Coil springs at all four corners; NO heave/third springs** → `SuspensionArchitecture.GT3_COIL_4WHEEL`, `heave_spring=None`
- 4-way damper per corner (LSC/HSC/LSR/HSR); **click range and polarity vary per car — see table below**
- No roll dampers anywhere (`has_roll_dampers=False, has_front_roll_damper=False, has_rear_roll_damper=False`)
- Brake pads: 3 compounds (Low/Medium/High) standard
- Cross weight target ~50% on road tracks
- All run perch-offset RH workflow (GT3 did NOT get the 2026 S2 GTE direct-RH overhaul)
- Bump rubber gap is exposed as a setup parameter on Acura NSX and Mustang/Corvette (and possibly others); not on legacy cars

## Damper click convention — POLARITY VARIES, CRITICAL

| Car | LSC | HSC | LSR | HSR | Polarity |
|---|---|---|---|---|---|
| BMW M4 GT3 | 0–11 | 0–11 | 0–11 | 0–11 | **higher = stiffer** (standard) |
| Mercedes AMG | 0–11 | 0–11 | 0–11 | 0–11 | higher = stiffer |
| Ferrari 296 | 0–11 | 0–11 | 0–11 | 0–11 | higher = stiffer |
| Lambo Huracán | 0–11 | 0–11 | 0–11 | 0–11 | higher = stiffer (assumed) |
| Aston Vantage | PENDING | PENDING | PENDING | PENDING | possibly 5-way real, mapped to 4-way |
| Porsche 992 | PENDING | PENDING | PENDING | PENDING | PENDING |
| Acura NSX | 1–16 | 1–16 | 1–16 | 1–16 | **higher = stiffer** (16 = max) |
| Audi R8 LMS | 2–38 | 0–40 | 2–38 | 0–40 | **lower = stiffer** (INVERTED) |
| McLaren 720S | 0–40 | 0–50 | 0–40 | 0–50 | **lower = stiffer** (INVERTED) |
| Ford Mustang | 0–11 | 0–11 | 0–11 | 0–11 | higher = stiffer (DSSV) |
| Corvette Z06 | 0–30 | 0–22 | 0–30 | 0–22 | **lower = stiffer** (Penske, INVERTED) |

**Solver implication**: damper deltas are not portable across cars. Add `damper_click_polarity` field to `DamperModel` and have the solver dispatch on it.

## TC / ABS index polarity — VARIES

| Car | TC | ABS | Off-position |
|---|---|---|---|
| BMW M4 | 10 positions | 12 | inferred 1=off (PENDING) |
| Mercedes | 12 | 12 | PENDING |
| Ferrari 296 | 12 | 12 | 1=off |
| Lambo | 12 | 12 | PENDING |
| Aston | 12 | 12 | PENDING |
| Porsche 992 | PENDING | PENDING | "0 disables TC" |
| Acura NSX | 12 | 12 | **1=off** |
| Audi R8 | 12 | 12 | **12=off** |
| McLaren | 12 | 12 | 1=off |
| Mustang | 12 | 12 | **1=off** |
| Corvette | 12 | 12 | **0=off** (zero-indexed) |

## ARB blade encoding — VARIES

| Car | Front | Rear | Encoding |
|---|---|---|---|
| BMW M4 | 11 configs (D1-D1 → D6-D6) | 7 configs (D1-D1 → D4-D4) | **paired-blade indices** |
| Mercedes | 6 (D1–D6) | 7 (D1–D7) | single-blade |
| Ferrari 296 | indexed (count PENDING) | indexed | single-blade |
| Lambo | 6 (paired 1-1 → 3-3) | 6 (paired 1-1 → 3-3) | paired |
| Aston | PENDING | PENDING | PENDING |
| Porsche 992 | binary 35/45 mm + blades (PENDING count) | binary 35/45 mm + blades | **2-stage** |
| Acura NSX | 8 (1=soft → 8=stiff) | 5 (1=soft → 5=stiff) | single ascending |
| Audi R8 | 5 (3+3 combos) | 6 (1-1 → 3-3) | paired |
| McLaren | 8 (1 soft → 8 stiff) | 7 (1 soft → 7 stiff) | single ascending |
| Mustang | 5 (1=soft → 5=stiff) | 5 (1=soft → 5=stiff) | single ascending |
| Corvette | 7 (0=stiff → 6=soft) | 7 (0=stiff → 6=soft) | **single descending (INVERTED)** |

## Per-car detail

### BMW M4 GT3 [FR, S58 turbo I6]
- Mass: dry **1285** kg, wet+driver 1411 kg [MANUAL] | WB **2.916** m | L 5014 W 2022 mm
- Power 500 bhp / 644 Nm / 7000 rpm | Fuel **120** L
- Front spring **190–340** N/mm step 10 [MANUAL] | Rear spring **130–250** N/mm step 10
- ARB: 11 front (paired D-codes), 7 rear (paired D-codes) [MANUAL]
- Master cyl: 7 options 15.9–23.8 mm front+rear [MANUAL]
- ABS 12, TC 10 [MANUAL]
- Aero: max-DF wing **+6**, dyn F **35.0±2.5** / R **80.0±2.5** mm; min-drag wing **-2**, F/R 17.5±2.5 [MANUAL]
- Wing-RH: **+1 wing ↔ -1.5 mm F OR +4.5 mm R** [MANUAL]
- Rear static RH: **50–95** mm [MANUAL]; front not stated
- Wing range from parsed aero map: -2 to +6 (9 angles)
- weight_dist_front PENDING_IBT (community estimate ~0.50)
- Damper polarity: standard 0–11 higher=stiff
- 2026 BoP (Patch 3): mass slightly reduced, torque slightly reduced

### Mercedes-AMG GT3 (2020) [front-mid, NA V8 6.2L]
- Mass: **1320** kg dry, 1440 kg wet [MANUAL] | WB **2.630** m
- Power 500 bhp / 625 Nm (BoP) / **7800** rpm | Fuel **106** L [MANUAL]
- Front spring **250–400** N/mm step 25 [MANUAL] | Rear spring **150–275** N/mm step 25
- ARB: 6 front (D1-D6), 7 rear (D1-D7) [MANUAL]
- ABS 12, TC 12 [MANUAL]
- Aero: max-DF wing **+9**, dyn F **40.0±2.5** / R **67.5±2.5** mm; min-drag F/R 17.5±2.5 [MANUAL]
- Wing-RH: +1 wing ↔ +3.6 mm R [MANUAL]
- Rear static RH: 50–90 mm [MANUAL]; front not stated
- Wing range from parsed aero map: -1 to +8 (12 angles)
- 2026 BoP: mass slightly reduced, torque slightly reduced

### Aston Martin Vantage GT3 EVO [FR, twin-turbo V8 4.0L]
- Mass: dry ~**1265** kg (real-world FIA), iRacing 1330 kg wet [MANUAL inline]
- WB ~**2.704** m (PENDING manual decode — V1 PDF is image-based)
- Power 535 bhp / **535 hp** at iRacing [iRacing.com] | Fuel **106** L [iRacing.com]
- Aero: max-DF wing **+10.5**, dyn F **35.0±2.5** / R **70.0±2.5** mm; min-drag wing **+0.5** [MANUAL]
- Wing-RH: +1 wing ↔ +4.5 mm R [MANUAL]
- Real-world dampers: 5-way (iRacing maps to 4-way? PENDING)
- ARB blade counts: PENDING (manual is image-based)
- Wing range from parsed aero map: 5 to 13 (9 angles)
- 2026 BoP: mass slightly increased, torque reduced

### Ferrari 296 GT3 [MR, V6 twin-turbo, hybrid stripped]
- Mass: dry **1350** kg, wet+driver 1508 kg [MANUAL] | WB **2.660** m
- Power 524 bhp / 664 Nm / **8000** rpm | Fuel **104** L [MANUAL]
- **NOT indexed garage controls** (unlike Ferrari 499P GTP) — uses standard N/mm and mm
- 4-way 0–11 dampers [MANUAL]
- ARB blades: indexed scale (count PENDING garage capture)
- 3 brake pad compounds, 3 gear stacks (FIA / IMSA Daytona / IMSA Short)
- Aero: max-DF wing **+10**, dyn F **37.5±2.5** / R **58.5±2.5** mm [MANUAL]
- Wing-RH: +1 wing ↔ -1.0 mm F OR +3.0 mm R [MANUAL]
- Rear static RH: 50.0–92.5 mm [MANUAL]
- Wing range from parsed aero map: -2 to +10 (13 angles)
- 2026 BoP (Patch 3): mass slightly decreased, torque slightly increased, **increased downforce**

### Lamborghini Huracán GT3 EVO [MR, NA V10 5.2L]
- Mass: dry **1285** kg, wet 1480 kg [MANUAL] | WB **2.645** m
- Power 500 bhp / 545 Nm / **8500** rpm
- Spring rates F+R: **160–280** N/mm (30 N/mm steps from 160-250, 15 steps for last 3) [MANUAL]
- ARB: 6 paired blades each axle (1-1 → 3-3) [MANUAL]
- ABS 12, TC 12, throttle shape 3 (linear/hybrid/wet) [MANUAL]
- Aero: max-DF wing **+12**, dyn F **40.0±2.5** / R **62.0±2.5** mm; min-drag wing **+2** [MANUAL]
- Wing range +2 to +12 (10 positions, 1° steps) [MANUAL]
- Wing range from parsed aero map: 2 to 12 (11 angles)
- 2026 BoP: mass slightly increased, torque slightly increased
- weight_dist_front ~0.50 (Coach Dave 50:50)

### McLaren 720S GT3 EVO [MR, twin-turbo V8 4.0L]
- Mass: dry **1300** kg, wet 1494 kg [MANUAL] | WB **2.696** m
- Power 531 bhp / 667 Nm / **8000** rpm
- Damper architecture **INVERTED** (low click = stiff): LSC/LSR 0–40, HSC/HSR 0–50 [MANUAL]
- ARB: 8 front (1 soft → 8 stiff), 7 rear (1 → 7 stiff) [MANUAL]
- ABS 12, TC 12, throttle shape 3 [MANUAL]
- Aero: max-DF wing **+10.5** at F **35.0±2.5** / R **70.0±2.5**; min-drag wing **+0.5** [MANUAL]
- Wing range +0.5 to +10.5 in 0.5° steps (21 positions) [MANUAL]
- Wing range from parsed aero map: 2.5 to 10.5 (8 angles)
- Gear stacks: 2 (FIA / IMSA Short)

### Porsche 911 GT3 R (992) [RR, NA flat-6 4.2L]
- Mass: dry **1250** kg (lightest), wet 1496 kg [MANUAL] | WB **2.507** m (shortest)
- Power **565** bhp / 505 Nm / **9500** rpm (highest in class)
- weight_dist_front **~0.38** (RR) [REAL-WORLD] — **CALIBRATION-CRITICAL for LLTD physics**
- LLTD target via OptimumG formula: 0.38 + (0.20/0.20)*0.05 = **0.43** (lower than MR/FR cars)
- 3 gear stacks: Short Stack / FIA / Daytona [MANUAL]
- ARB: binary 35/45 mm + blade selector (count PENDING IBT)
- Spring perch quirk: **rear "decreasing perch INCREASES preload"** [MANUAL]
- Aero targets: **NOT PUBLISHED in manual** — must derive from IBT or aero map
- Damper click range: PENDING manual decode
- Wing range from parsed aero map: 5.7 to 12.7 (8 angles, 1° steps)
- 2026 BoP (Patch 3): mass reduced, torque increased, **increased aero drag**

### Acura NSX GT3 EVO22 [MR, V6 turbo, hybrid stripped]
- Mass: dry **1320** kg, wet 1485 kg [MANUAL] | WB **2.642** m
- Power 520 bhp / 620 Nm / **7500** rpm
- ARB: **8 front** (1 soft → 8 stiff), **5 rear** (1 → 5 stiff) [MANUAL]
- TC 12 (1=off, 2-7 dry, 8-12 wet), ABS 12 (1=off) [MANUAL]
- Damper clicks **1–16** (16 = max damping) [MANUAL]
- 3 gear stacks: Daytona / FIA / IMSA Short [MANUAL]
- **Spring perch auto-adjusts** when changing spring rate (preserves bump-rubber gap and RH) — Step 1 ↔ Step 3 decoupling
- **Bump rubber gap** exposed as per-corner setup parameter [MANUAL]
- Aero: max-DF wing **+11**, dyn F **40.0±2.5** / R **55.0±2.5** mm (low rake) [MANUAL]
- Wing-RH: +1 wing ↔ -1 mm F OR +2 mm R [MANUAL]
- Wing range from parsed aero map: 1 to 11 (11 angles)
- **DISTINCT from Acura ARX-06 GTP** — different chassis, different damper architecture (per-corner vs heave+roll)
- 2026 BoP (Patch 3): mass reduced, torque slightly reduced

### Audi R8 LMS evo II GT3 [MR, NA V10 5.2L]
- Mass: dry **1320** kg, wet 1479 kg [MANUAL] | WB **2.700** m
- Power 518 bhp / 556 Nm / **8500** rpm
- ARB: front 5 effective (3+3 combos), rear 6 (1-1 → 3-3 paired) [MANUAL]
- TC 12 (12=off, 1-6 dry, 7-11 wet), ABS 12 (12=off) [MANUAL]
- Dampers: LSC/LSR **2–38**, HSC/HSR **0–40** — INVERTED (low = stiff) [MANUAL]
- 6th gear has 2 options: FIA / IMSA Daytona (only 6th, gears 1-5 fixed) [MANUAL]
- Aero: max-DF wing **+7** at F **40.0±2.5** / R **62.0±2.5**; min-drag wing **+2** [MANUAL]
- Wing-RH: +1 wing ↔ -2.4 mm F OR **+7.2 mm R** (highest in class) [MANUAL]
- Wing range +2 to +7 (5 positions) — narrowest range [MANUAL]
- **NO USER AERO MAP** — Audi is the one car of 11 with no xlsx in the user's collection
- 2026 BoP (Patch 3): mass slightly increased, torque slightly increased

### Ford Mustang GT3 [FR, NA V8 5.4L Coyote, Multimatic chassis]
- Mass: dry **1315** kg, wet 1479 kg [MANUAL] | WB **2.777** m (longest in class)
- Power 516 bhp / 584 Nm / **8250** rpm
- ARB: 5 front (1 soft → 5 stiff), 5 rear (1 → 5 stiff) [MANUAL]
- ABS 12 (1=off), TC 12 (1=off) [MANUAL]
- Throttle shape 10 settings (6=linear) [MANUAL]
- Dampers: 4-way 0–11 (Multimatic DSSV), 0=min damping, 11=max [MANUAL]
- Gear stacks: 3 (Short / FIA / Le Mans-Daytona) [MANUAL]
- **Bump rubber gap** per-corner [MANUAL]
- **Spring perch auto-adjusts** on spring change [MANUAL]
- Aero: max-DF wing **+9**, dyn F **37.5±2.5** / R **57.5±2.5** mm (low rake); min-drag wing **+0.5** [MANUAL]
- Wing-RH: +1 wing ↔ -1.5 mm F OR +4.0 mm R [MANUAL]
- **Nordschleife min RH 70 mm** [MANUAL] (only track with explicit min)
- Wing range from parsed aero map: 1 to 9 (8 angles)
- weight_dist_front ~0.50 (Multimatic design intent) [REAL-WORLD]
- 2026 BoP (Patch 3): mass increased, torque reduced, aero downforce reduced

### Chevrolet Corvette Z06 GT3.R [MR, NA flat-plane V8 5.5L LT6, Pratt Miller]
- Mass: dry **1335** kg (heaviest), wet 1494 kg [MANUAL] | WB **2.718** m
- Power 520 bhp / 583 Nm / **8000** rpm
- Track widths asymmetric: front **1648** mm, rear **1586** mm [REAL-WORLD]
- ARB: 7 front (**0=stiff → 6=soft**, INVERTED), 7 rear (same) [MANUAL]
- Brake **pedal ratio** adjustable (Mustang has master cylinders instead) [MANUAL]
- ABS 12 (0-indexed, 0=off), TC 12 (0-indexed, 0=off) [MANUAL]
- Dampers (Penske, **INVERTED**): LSC/LSR 0–30 (0=max damping), HSC/HSR 0–22 (0=max damping) [MANUAL]
- Gear stacks: **4** (IMSA / FIA / Le Mans / Daytona) [MANUAL]
- Bump rubber gap per corner [MANUAL]
- Spring perch auto-adjusts on spring change [MANUAL]
- Aero: max-DF wing **+9.5**, dyn F **37.5±2.5** / R **57.5±2.5** mm [MANUAL]
- Wing-RH: +1 wing ↔ -1.5 mm F OR +4.0 mm R [MANUAL]
- Wing range from parsed aero map: 0.5 to 9.5 (9 angles, 1° steps)
- 2026 BoP (Patch 3): mass slightly increased, torque slightly increased

## Solver implications

1. **Step 1 (rake/RH)** — Each car has a published max-DF target (front-RH, rear-RH, wing) from the manual. These are direct anchors for Step 1's balance optimization. With the GT3 aero map being balance-only (no L/D), Step 1 collapses to: pick (front_RH, rear_RH, wing) such that interpolated balance% matches `default_df_balance_pct` AND falls within the published max-DF/min-drag operating window AND respects the legal RH floor.

2. **Step 2 (heave/third)** — N/A for all 11 cars. Returns `HeaveSolution.null()`.

3. **Step 3 (corner springs)** — Manual-published spring rate ranges available for BMW, Mercedes, Lambo. Others PENDING_IBT garage capture. Acura/Mustang/Corvette have spring-perch auto-adjust → Step 1 ↔ Step 3 partial decoupling.

4. **Step 4 (ARB/LLTD)** — ARB encoding diverges per car (paired/single, ascending/descending, binary stages). Each car needs custom blade-count + label scheme in `ARBModel`. LLTD physics target via OptimumG formula: needs `weight_dist_front` from IBT corner-weight readout. Porsche 992 LLTD target ~0.43 (RR), MR cars ~0.46–0.50, FR cars ~0.50–0.51.

5. **Step 5 (geometry)** — Camber/toe ranges PENDING manual + IBT for every car.

6. **Step 6 (dampers)** — Click polarity and range varies wildly. `DamperModel` needs `click_polarity: Literal["higher_stiffer", "lower_stiffer"]` and per-channel `click_range: tuple[int, int]`. The current solver assumes BMW convention (0–11, higher=stiff) — applies cleanly only to ~5 of 11 GT3 cars.

7. **Indexed controls** — BMW M4 ARBs are paired-blade indices (D-codes); should reuse the `IndexedLookupPoint` pattern from Ferrari 499P (rename for cross-car reuse). Audi/Lambo also paired. Porsche binary 35/45 + blade is a 2-stage encoding needing new pattern.

8. **TC/ABS index polarity** — needs `_indexed_off_position: int` in `setup_registry` per car. Currently solver assumes 1=off; wrong for Audi (12=off) and Corvette (0=off).

9. **Bump rubber gap** is a new garage parameter on Acura/Mustang/Corvette not present on the legacy GTP cars — needs `bump_rubber_gap_*` fields in `setup_registry`.
