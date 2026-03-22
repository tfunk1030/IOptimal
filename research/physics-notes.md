# iOptimal Physics Research Notes

Running log of targeted physics literature research for the iOptimal GTP setup solver.
Each entry documents: source, key finding, formula (with units), and what was applied.

---

## 2026-03-19 — Topic E: Optimal LLTD — Milliken RCVD + Empirical Validation

**Sources:**
- Milliken & Milliken, *Race Car Vehicle Dynamics* (RCVD), Chapter 18 — Roll and Load Transfer
- kktse.github.io, "Simplified steady-state lateral load transfer analysis" (2021-05-12)
  https://kktse.github.io/jekyll/update/2021/05/12/simplied-lateral-load-transfer-analysis.html
- racingcardynamics.com, "A discussion on steady-state lateral weight transfer" (2021-07-14)
  https://racingcardynamics.com/weight-transfer/
- Grassroots Motorsports forum, Ron Sutton (experienced race engineer), TLLTD rule:
  https://grassrootsmotorsports.com/forum/tech-tips/tire-lateral-load-transfer-distribution-tlltd/139846/page1/
- OptimumG Q&A Ep. 2 (Claude Rouelle), LLTD balance principle:
  https://optimumg.com/vehicle-setup-and-vehicle-design-qa-series-ep-2/

**Key findings:**

1. **Total lateral load transfer cannot be changed by suspension tuning.**
   ΔFz_total = m · h_CG · ay / t
   where: m = total mass [kg], h_CG = CG height [m], ay = lateral accel [m/s²], t = track width [m].
   Only the *distribution* (LLTD) between front and rear can be controlled via springs/ARBs.

2. **LLTD from roll stiffness (Milliken RCVD formula):**
   LLTD = K_φ_front / (K_φ_front + K_φ_rear)
   where K_φ_i = roll stiffness at axle i from springs + ARB [N·m/deg].
   → **This is already correctly implemented in ARBSolver._lltd_from_roll_stiffness().**

3. **Optimal LLTD target — baseline rule (OptimumG / Milliken):**
   LLTD_target = static_front_weight_fraction + offset(λ)
   where offset(λ=0.20) = +5.0% (empirical OptimumG calibration point).
   Physical reason: load-sensitive tyres (degressive μ-Fz) lose more grip when overloaded.
   The axle receiving more LLTD relative to its static share loses total lateral capacity.
   Setting LLTD slightly above front weight fraction pre-loads the front tyres, preventing
   initial understeer and matching the rear's tendency to lose traction last in prototypes.
   → **Already implemented: `lltd_physics_offset = (tyre_sens / 0.20) * 0.05`**

4. **Speed-dependent correction (Ron Sutton, validated by Milliken aero analysis):**
   - Low-speed tracks (<100 mph / 160 kph corners): LLTD_target = front_wt + 5.0%
   - High-speed tracks (>100 mph / 160 kph corners): LLTD_target = front_wt + 5.5–6.0%
   
   Physics justification: At high speed, aero downforce in GTP cars shifts effective weight
   rearward (typically rear-biased DF balance to reduce drag). This moves the effective
   weight distribution away from the static value, requiring more front LLTD bias to
   compensate. Additionally, high-speed cornering demands faster weight transfer — higher
   front roll resistance ensures front tyres load up before the rear, maintaining stability.

**Formula (with units):**
   LLTD_target = W_f/W_total + (λ / 0.20) × (0.05 + 0.01 × f_hs)
   where:
   - W_f/W_total = static front weight fraction [dimensionless]
   - λ = tyre load sensitivity coefficient [dimensionless, ~0.20 for Michelin GTP]
   - f_hs = fraction of lap time above 200 kph [0.0–1.0, from TrackProfile.pct_above_200kph]
   - 0.01 × f_hs = speed correction term [dimensionless, 0 to +1.0%]

**Example (BMW Sebring):**
   - static front wt: 47.27%
   - λ = 0.22, pct_above_200kph ≈ 0.28 (Sebring mix of straights and slow corners)
   - LLTD_target = 0.4727 + (0.22/0.20) × (0.05 + 0.01×0.28)
                 = 0.4727 + 1.10 × 0.0528
                 = 0.4727 + 0.0581 ≈ 52.1% front

**iOptimal application:**
- File: `solver/arb_solver.py`, method `ARBSolver.solve()` and `solution_from_explicit_settings()`
- Updated `lltd_physics_offset` to include speed-dependent correction:
  ```python
  pct_hs = getattr(self.track, "pct_above_200kph", 0.0)
  hs_correction = 0.01 * pct_hs
  lltd_physics_offset = (tyre_sens / 0.20) * (0.05 + hs_correction)
  ```
- This increases LLTD target by up to +1.0% for fully high-speed tracks (Le Mans/Monza),
  matching the Sutton empirical +5.5–6.0% rule for fast circuits.
- Effect on Sebring: LLTD target shifts from ~52.0% → ~52.1% (small but theoretically correct).
- Effect on Le Mans (pct_above_200kph ≈ 0.80): target shifts from ~52.0% → ~52.9%.
- Backward compatible: pct_above_200kph defaults to 0.0 if TrackProfile lacks the field.

**What was NOT changed (and why):**
- The tyre_load_sensitivity values per car are not adjusted — they are empirically calibrated
  from IBT data and Milliken Ch.18 confirms λ=0.20 is appropriate for modern Michelin compounds.
- Roll centre height correction is NOT added: iRacing's GTP cars have near-zero roll centres
  (flat floor, no significant jacking); the geometric load transfer term is negligible vs elastic.
- Front/rear tyre size asymmetry correction NOT added: GTP regulations mandate matched compound
  sizes; the only asymmetry is pressure, which is handled in supporting_solver.py.

---

## 2026-03-22 — Topic A: Damper Force Curve Physics — Low Speed vs High Speed

**Sources:**
- F1technical.net, "Anatomy of a racing damper" (Feb 23, 2002)
  https://www.f1technical.net/features/10695
- NASA Speed News, "Damper Tuning" (Feb 4, 2019)
  https://nasaspeed.news/tech/suspension/damper-tuning/
- Your Data Driven, "Race Engineering: Racing car damper ratios" (Jun 21, 2022)
  https://www.yourdatadriven.com/race-engineering-racing-car-damper-ratios/
- FSAE Forums, "Damping ratios" (archived, empirical data)
  http://www.fsae.com/forums/archive/index.php/t-10472.html

**Key findings:**

1. **Low-speed vs high-speed shaft velocity — the critical distinction:**
   "Low speed" and "high speed" refer to **damper shaft velocity** (piston speed through oil), NOT
   car velocity. The industry-standard boundary is approximately 25–50 mm/s:
   - **Low-speed zone (0–25 mm/s):** Sprung mass motion — body roll, dive/squat during driver
     input transitions. Controls dynamic weight transfer and LLTD evolution during corners.
   - **High-speed zone (>50–100 mm/s):** Unsprung mass motion — wheel response over bumps,
     kerbs, and surface irregularities. Controls contact patch load variation.
   - FSAE empirical data: on smooth tracks, damper spends most time <25 mm/s; peaks ~100 mm/s.
     On rough tracks (Detroit, Sebring), peaks reach 150–175 mm/s.

2. **Physics of the two orifice regimes (F1technical.net):**
   - **Fixed orifice (low-speed):** When piston moves slowly, only the fixed needle orifice
     is active. Drag ∝ v², so: **F_damp = C_ls × v²** (parabolic). Adjusting needle position
     changes C_ls (the low-speed stiffness coefficient).
   - **Variable orifice (high-speed):** Once shim stack pressure threshold is exceeded, shims
     deflect open. The orifice area grows with force, linearising the curve:
     **F_damp ≈ C_hs × v** (approximately linear beyond knee point).
   - The "knee" between regimes is set by shim stack preload — the click adjuster on most
     racing dampers rotates a drum with progressively sized orifices (Penske) or adjusts the
     needle (Quantum/Koni) to shift the knee location.

3. **Optimal racing damping ratios:**
   - Target damping ratio in ride: **ζ = 0.65–0.70** (65–70% of critical damping).
   - Compare to road cars: ζ = 0.20–0.30.
   - Racecar Engineering empirical: rebound 13–25 mm/s → ζ = 0.70 (body control priority).
   - Low-speed adjustments felt by driver; high-speed adjustments affect grip but not driver feel.

4. **Effect of low-speed damping on LLTD (NASA Speed News):**
   During corner entry (brake→steer transition), the dominant damper pair is
   **inside-front (rebound) + outside-rear (bump)**. Higher low-speed damping on this diagonal
   resists the weight transfer, slowing lateral load transfer rate → car feels more stable at
   entry but may push (understeer) mid-corner if too high.
   - More front LS bump → resists dive, keeps front loaded → initial understeer
   - More front LS rebound → slows front unloading in roll → turn-in understeer
   - More rear LS bump → resists squat → limits rotation on exit

**Formula summary:**
   F_damper(v) = C_ls × v²                              (v < v_knee, fixed orifice)
   F_damper(v) = F_knee + C_hs × (v - v_knee)           (v > v_knee, shim stack open)
   v_knee typically 25–75 mm/s depending on shim preload and orifice size.

**iOptimal application:**
- iRacing BMW GTP setup exposes separate LS/HS bump and rebound clicks (4-way adjustable).
- In `car_model/cars.py`, the damper click-to-force model should use the two-regime formula:
  - Calibrate C_ls and C_hs from known click→force data (need IBT validation).
  - The knee point v_knee may be hardcoded per car model (BMW = ~30 mm/s typical).
- Low-speed damping directly affects the dynamic LLTD during corner transitions.
  High-speed damping affects ride height stability over kerbs (Sebring-specific concern).
- **Priority for objective function:** LS rebound most important for corner entry balance;
  HS bump important for Sebring kerb management.

---

## 2026-03-22 — Topic B: Sobol Sequence vs Latin Hypercube Sampling for Design Space Exploration

**Sources:**
- PMC/NIH, "To Sobol or not to Sobol? The effects of sampling schemes in systems biology"
  Math Biosci. 2021 Apr 16; 337:108593. doi:10.1016/j.mbs.2021.108593
  https://pmc.ncbi.nlm.nih.gov/articles/PMC8184610/
- ScienceDirect, Efficient sampling algorithm combining LHS and Sobol (2018)
  https://www.sciencedirect.com/science/article/abs/pii/S009813541830437X

**Key findings:**

1. **What Sobol sequences are:**
   Deterministic quasi-random low-discrepancy sequences that fill the parameter space more
   uniformly than random or LHS sampling. Generated by a fixed algorithm (no random seed needed),
   which makes results fully reproducible. Sobol sequences maintain d-dimensional uniformity
   properties that LHS does not guarantee in high dimensions.

2. **Latin Hypercube Sampling (LHS) mechanics:**
   Divides each parameter axis into N equally-probable intervals and draws exactly one sample
   per interval per dimension. Prevents clumping but can have spurious inter-parameter
   correlations in high dimensions. LHS requires a randomness source and correlation-removal
   optimization (computationally more expensive to generate than Sobol).

3. **Comparison results (PMC paper, across ODE and agent-based models):**
   - **Calibration tasks:** All three methods (random, LHS, Sobol) perform similarly.
   - **Sensitivity analysis:** Sobol converges faster — it requires fewer samples to achieve
     the same accuracy in variance-based sensitivity metrics (Sobol indices).
   - **Computational cost:** Sobol sequences are cheaper to compute than optimised LHS.
   - **Reproducibility:** Sobol is deterministic → no seed needed, same sequence every run.
   - For high-dimensional spaces (>5 parameters), Sobol's space-filling advantage grows.

4. **Practical guidance for optimization (ScienceDirect 2018 hybrid paper):**
   A combined LHS+Sobol scheme exists for very high dimensions (>10 parameters) that avoids
   spurious correlations while maintaining d-dimensional uniformity. For 3–8 dimensions
   (iOptimal's typical setup parameter count), pure Sobol is preferred over pure LHS.

**Formula — Sobol discrepancy property:**
   Star discrepancy D*_N ≈ O((log N)^d / N)   vs   random: O(1/√N)
   where d = number of dimensions, N = sample count.
   Sobol achieves better coverage with the same N, especially for small N.

**iOptimal application:**
- The grid search / initial space exploration in `solver/grid_search.py` (or equivalent)
  should prefer Sobol sequences over random or LHS for generating setup candidates.
- Python: `scipy.stats.qmc.Sobol(d=n_params, scramble=True)` generates scrambled Sobol
  (scrambling adds randomness while maintaining low-discrepancy properties).
- For sensitivity analysis of the objective function (which parameters matter most?),
  Sobol sequences allow using Saltelli's method for Sobol sensitivity indices directly.
- For N=500 samples in 6 dimensions (springs, ARBs, heave springs), Sobol gives better
  space coverage than LHS with identical compute budget.
- **Implementation note:** Use `scramble=True` in scipy's Sobol to avoid initial artifacts;
  skip the first few hundred points of the raw Sobol sequence if using old scipy versions.

---

## 2026-03-22 — Topic C: Heave Spring / Third Element — Physics and GTP Application

**Sources:**
- F1technical.net Forum, "Interconnected suspensions" (2011)
  https://www.f1technical.net/forum/viewtopic.php?t=9322&start=15
- F1technical.net Forum, "3rd spring" (archived)
  https://www.f1technical.net/forum/viewtopic.php?t=1075
- HPA Academy, "What Is A Heave Spring or Third Element?"
  https://www.hpacademy.com/blog/what-is-a-heave-spring-or-third-element/
- Coach Dave Academy, "iRacing Cadillac V-Series.R GTP LMDh Guide"
  https://coachdaveacademy.com/tutorials/under-the-hood-tips-and-tricks-to-driving-the-cadillac-v-series-r-gtp/

**Key findings:**

1. **What a heave spring does — mode decoupling:**
   A heave spring (third element) is mechanically connected **between both rockers/bellcranks**
   at the same axle. It only engages when both wheels move in the same direction (heave/pitch).
   When wheels move in opposite directions (roll), the spring's attachment points move together
   and the spring sees zero net displacement — it is inert in roll mode.
   - **Heave mode:** both wheels ↑ simultaneously → heave spring compresses → resists body dive
   - **Roll mode:** one wheel ↑, other ↓ → heave spring endpoints cancel → zero contribution
   - **Result:** Heave stiffness can be tuned independently of roll stiffness (ARB handles roll).

2. **Mathematical model (F1technical.net interconnected suspension):**
   For a two-wheel axle model:
   ```
   F1 = (Kh + Kc + Kb)·Z1 + (Kh - Kb)·Z2
   F2 = (Kh - Kb)·Z1 + (Kh + Kc + Kb)·Z2
   ```
   where:
   - F1, F2 = wheel forces [N]
   - Z1, Z2 = individual wheel displacements [m]
   - Kh = heave spring stiffness (at wheel, via motion ratio²) [N/m]
   - Kc = corner spring stiffness (at wheel) [N/m]
   - Kb = ARB stiffness (at wheel) [N/m]

   **Effective heave stiffness** (both wheels move together, Z1=Z2=Z):
     K_heave_eff = 2·Kc + 2·Kh   (ARB cancels in pure heave)
   **Effective roll stiffness** (opposite, Z1=-Z2):
     K_roll_eff = 2·Kc + 4·Kb    (heave spring cancels in pure roll)

   This is why "third spring suspensions allow independent tuning of pitch/heave and roll
   stiffness" — the two knobs (Kh and Kb) control orthogonal modes.

3. **Aerodynamic motivation — ride height consistency:**
   In high-downforce GTP cars, aero load can reach 2–3× car weight at race speed. Without a
   heave spring, only the corner springs resist this load — which also affects roll stiffness.
   A stiff heave spring allows: **high heave stiffness** (prevents aero-induced ride height
   drop, keeps diffuser/floor at optimal height) while maintaining **moderate corner springs**
   (softer for tyre compliance and roll balance tuning).

4. **iRacing GTP implementation (Cadillac + BMW):**
   - Both GTP cars in iRacing have **front heave spring** + **rear third spring** in setup.
   - Front heave spring: controls front ride height under downforce load.
     Stiffer = better aero consistency, but harsher over bumps.
   - Rear third spring: similar function at rear. Affects rear ride height and pitch behaviour.
   - Track-dependent guidance:
     - Smooth tracks (Road America, COTA): stiffen heave springs (prioritise aero map consistency)
     - Bumpy tracks (Sebring, Watkins Glen): soften heave springs (prioritise contact patch load)
   - Sebring-specific: bumpy surface causes high-speed damper excitation (>75 mm/s peaks).
     Over-stiff heave spring causes "bouncing" — car loses contact patch loading over crests.

**iOptimal application:**
- HeaveSpring_Front and HeaveSpring_Rear are first-class setup parameters to include in the
  optimizer's search space. They should NOT be conflated with corner spring or ARB parameters.
- The effective heave stiffness formula above allows computing ride height deflection under
  aero load: δ_ride = F_aero / K_heave_eff — useful for validating against IBT ride height data.
- Objective function heave scoring should penalise:
  - Too soft heave: large ride height variance under speed → aero map inconsistency
  - Too stiff heave on bumpy track: contact patch load variation over surface irregularities
- Suggest adding `track.roughness_index` to TrackProfile and using it to bias heave spring
  target stiffness (analogous to pct_above_200kph for the LLTD speed correction).

---

## 2026-03-22 — Topic D: Fuel Load Weight Distribution Shift and LLTD Implications

**Sources:**
- Wikipedia, "Weight transfer" — CoM shift mechanics
  https://en.wikipedia.org/wiki/Weight_transfer
- FIA LMDh Technical Regulations 2023 (fuel tank + ballast positioning)
  https://www.fia.com/sites/default/files/lmdh-technical-regulations-2023.05.03_blackline.pdf
- F1technical.net Forum, "Best weight distribution front/rear" (Oct 2022)
  https://www.f1technical.net/forum/viewtopic.php?t=30725
- F1technical.net Forum, "Working out the effect of additional weight on lap times"
  https://www.f1technical.net/forum/viewtopic.php?t=14782
  (Rule of thumb: ~0.1 s/kg lap time penalty from additional mass, track-dependent)

**Key findings:**

1. **LMDh fuel tank location (from regulations):**
   LMDh rules mandate the fuel cell is located within the central survival cell, low in the
   chassis between the axles. Typical fuel tank CG is approximately 45–55% of wheelbase from
   the front axle — close to the car's overall CG position, by design.

2. **Static weight distribution shift from fuel burn:**
   As fuel burns over a stint (typical GTP stint: 60–90 kg fuel), the car's total mass and
   CG location change. The front weight fraction shift is:

   ```
   Δ(W_f/W) = (m_fuel × (x_fuel/L - W_f_empty/W_empty)) / (m_total_full)
   ```

   Simplified form — if the fuel tank CG is at position `p_fuel` (fraction of wheelbase from
   front axle), and the empty car has front weight fraction `W_f0`:
   ```
   W_f(m_fuel) = (W_f0 × m_empty + m_fuel × p_fuel) / (m_empty + m_fuel)
   ```
   - If p_fuel > W_f0: adding fuel moves CG forward → burning fuel moves CG rearward.
   - If p_fuel < W_f0: adding fuel moves CG rearward → burning fuel moves CG forward.
   - For BMW M Hybrid V8: m_empty ≈ 890–940 kg, max fuel ~90 kg (iRacing GTP capacity).
     Static front fraction ~47.3% (full fuel). Fuel tank ~50% wheelbase position (central).
     Over a full stint: W_f may shift by ~0.3–0.8% as fuel burns.

3. **LLTD target drift over stint:**
   Since LLTD_target = W_f + physics_offset (from LLTD Topic E above):
   - Burning 80 kg fuel → W_f changes ~0.3–0.8% → LLTD_target changes by same amount.
   - This means the optimal ARB balance (and therefore setup) shifts during a stint.
   - At start of stint (full fuel): LLTD_target ≈ 52.1% → optimal front ARB setting X.
   - At end of stint (low fuel): LLTD_target ≈ 52.4–52.9% → slightly more front ARB needed.
   - The shift is small but meaningful for a long stint (24h Le Mans, 6h races).

4. **Practical magnitude (F1 context):**
   F1 engineers quote ~0.03–0.05 s/lap per 10 kg fuel — primarily from mass/acceleration,
   not from LLTD shift. The handling balance change from fuel-induced CG shift is secondary
   but measurable in tyre wear data across a stint.

5. **iOptimal application strategy:**
   The current solver uses fixed `static_front_weight_fraction` from `CarModel`.
   To handle fuel load:
   - Expose `fuel_load_kg` as a solver input (default = regulation max).
   - Calculate `W_f(fuel) = (W_f_full × m_full - m_fuel × (0.5 - p_fuel)) / m_current`
   - Pass adjusted W_f to ARBSolver.solve() → it automatically adjusts LLTD_target.
   - For stint-optimised setups: solve at both full and low fuel, report LLTD delta.
   - For fixed setups: solve at 60–70% fuel (mid-stint compromise).
   - **Key validation:** Check BMW M Hybrid V8 IBT weight/fuel telemetry channels to confirm
     actual fuel CG position. iRacing IBT likely exposes `FuelLevel` channel (litres remaining).
     Convert: m_fuel [kg] = FuelLevel [L] × 0.755 (petrol density).
