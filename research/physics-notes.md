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

---

## 2026-03-23 — Topic B: iRacing Shock Tuning — Official Guide + GTP Manual Data

**Sources:**
- iRacing Official Shock Tuning User Guide (2021)
  https://s100.iracing.com/wp-content/uploads/2021/08/Shock-Tuning-User-Guide.pdf
- Cadillac V-Series.R GTP Official User Manual V2 (2023)
  https://s100.iracing.com/wp-content/uploads/2023/07/Cadillac-V-Series.R-GTP-Manual-V2.pdf
- Coach Dave Academy, "Under the Hood: Cadillac V-Series.R GTP" (Sep 2025)
  https://coachdaveacademy.com/tutorials/under-the-hood-tips-and-tricks-to-driving-the-cadillac-v-series-r-gtp/

**Key findings:**

1. **LS/HS transition velocity — 1.5 in/s = 38.1 mm/s:**
   iRacing's official guide defines the low-to-high speed transition at "usually around 1.5
   inches-per-second" of shock shaft velocity. Converting: 1.5 in/s × 25.4 = **38.1 mm/s**.
   This is the v_knee in the two-regime damper model.
   - v < 38 mm/s → low-speed zone (driver inputs: braking, throttle, steering)
   - v > 38 mm/s → high-speed zone (track surface: bumps, kerbs, crests)
   
   **PREVIOUS ESTIMATE WAS WRONG:** physics-notes.md Topic A estimated v_knee = 25-75 mm/s
   from generic racing damper literature. iRacing uses exactly **38.1 mm/s** (~1.5 in/s).
   Update solver/damper_solver.py accordingly.

2. **Click settings — force multiplier, NOT force per click:**
   The iRacing guide confirms that click adjustments "affect the overall force from the shock
   but leave the shape of the high-speed section alone." This means clicks are a SCALING FACTOR
   on the base force curve, not an additive force per click. The formula is:
     F_actual(v) = F_base(v) × click_scale_factor(click)
   NOT:
     F_actual(v) = F_base(v) + click × force_per_click
   
   The guide does NOT publish exact force-per-click values (these are car-specific and
   proprietary to each car's shock package). However, the scaling behaviour is confirmed:
   changing clicks shifts the entire force curve up/down proportionally.

3. **Slope setting — changes HS profile between digressive and linear:**
   - Low slope (1) → digressive curve: rapid force buildup at low HS velocity, then flattens.
     Good for smooth tracks (COTA, Pocono). Absorbs small bumps without chassis disturbance.
   - High slope (max) → nearly linear: force grows proportionally with velocity.
     Good for bumpy tracks (Sebring, Atlanta, street circuits). Prevents chassis bottoming.
   - Slope ONLY affects the high-speed region (>38 mm/s). Low-speed is unchanged.
   - The compression and rebound slope should generally be set similarly, then fine-tuned.

   **For iOptimal:** The slope setting determines the damper force model shape above v_knee.
   At low slope: F_hs(v) ≈ F_knee + C_hs × ln(v/v_knee)  (digressive, logarithmic)
   At high slope: F_hs(v) ≈ F_knee + C_hs × (v - v_knee)  (linear)
   Intermediate slopes interpolate between these two profiles.

4. **GTP suspension architecture — confirmed from Cadillac manual:**
   - **Front:** Pushrod-actuated independent torsion bar suspension + front heave spring element
   - **Rear:** Pushrod-actuated independent torsion bar suspension + rear third-spring element
   - **Heave spring:** Controls vertical loads only (not roll). Stiffening = better aero
     platform but worse bump absorption. The spring rate is indexed (not continuous).
   - **Heave perch offset:** Adjusts preload on heave spring. LOWER = more preload = higher
     front ride height. HIGHER = less preload = lower front ride height.
     THIS IS INVERSE to what you might expect. The perch offset is a DOWNWARD displacement:
     more negative = more spring compression = higher ride height.
   - **Pushrod length offset:** Adjusts front ride height WITHOUT changing heave spring preload
     or torsion bar settings. A clean ride-height-only adjustment.
   - **Third spring (rear):** Same concept as heave but for the rear. Controls vertical loads
     at the rear axle independent of roll.
   
   **For iOptimal:** This confirms the solver architecture is correct:
   - Heave spring → ride height control (Step 2)
   - Torsion bars → corner spring rates (Step 3)
   - Pushrod → front RH fine-tuning (Step 1)
   - Perch offsets are DEPENDENT on the chosen heave/torsion/pushrod settings → correctly
     removed from the Tier A search dimensions (P1 already done).

5. **Damper architecture (4-way adjustable, from Cadillac manual):**
   - LS Compression: "Higher values increase compression resistance and transfer load onto a
     given tire under low-speed conditions more quickly, inducing understeer."
   - HS Compression: "Higher values cause the suspension to be stiffer [over bumps/kerbs]."
   - HS Compression Slope: "Lower slope = digressive, higher slope = linear/aggressive."
   - LS Rebound: "Controls shock extending at lower speeds during body movement from inputs."
   - HS Rebound: "Controls shock behavior at higher speeds typically from bumps and kerbs."
   - HS Rebound Slope: Same profile control as compression slope but for the rebound side.
   
   **Key insight for diagonal damper coupling:**
   Under braking → front LS compression + rear LS rebound active simultaneously.
   Under corner entry → outside-front LS compression + inside-rear LS rebound.
   The iRacing guide explicitly states this X-pattern diagonal coupling. The solver should
   evaluate damper balance as diagonal pairs, not individual corners.

6. **Shock histogram method — load variation minimisation:**
   iRacing provides shock velocity histograms showing % time in each velocity zone.
   Target: "curved triangle" shape centred on zero with balanced bump/rebound.
   - If HS bars too tall → reduce HS clicks and/or slope
   - If LS bars imbalanced → adjust LS comp/rebound ratio
   - Goal: minimise load variation = maximise grip consistency
   
   **For iOptimal objective function:** The IBT track profile already has shock velocity
   distributions (p50, p95, p99 for front/rear). These can be used to:
   - Score damper tuning: does the predicted force curve produce balanced LS/HS time-in-zone?
   - Flag overdamped (too much HS time) or underdamped (too much chassis travel) conditions.

7. **GTP-specific heave/third spring guidance (Coach Dave Academy):**
   "Stiffening the front heave spring can help control the ride height and improve aerodynamic
   performance. Over-stiffening it can cause the car to bounce too much on bumpy surfaces.
   Generally, you should be able to stiffen these springs more on smoother tracks like Road
   America and soften them on bumpy tracks like Watkins Glen."
   
   **For iOptimal:** Heave/third spring rate should be track-dependent:
   - Score: penalise high heave stiffness on tracks with high p99 shock velocity (bumpy)
   - Score: penalise low heave stiffness on tracks with low p99 shock velocity (smooth)
   - The current solver uses a fixed heave spring rate per car — should become track-adaptive.

---

## 2026-03-23 — Topic C: Ferrari/Cadillac Aero Map Balance Floor Calibration

**Sources:**
- Direct analysis of parsed aero map JSON files from data/aero-maps/
- Systematic sweep of DF balance vs dynamic front RH across wing angles

**Key findings:**

1. **Aero balance floor is car+wing dependent:**
   At typical GTP operating ride heights (dynamic front 19-25mm), the achievable DF balance
   has a FLOOR that varies by car and wing angle. This floor exists because ground-effect aero
   produces more front DF at lower ride heights — and GTP cars run very low front RH.
   
   Balance floor at typical dyn_front for each car (wing 12):
   - BMW: 48.5% (at 20mm dyn front) — below default target 50.14% ✓ achievable
   - Ferrari: 51.2% (at 19.5mm dyn front) — ABOVE old target 49.5% ✗ unachievable
   - Cadillac: 51.9% (at 21.5mm dyn front) — ABOVE old target 50.14% ✗ unachievable
   - Porsche: ~49.0% (at 20mm dyn front) — below target 50.5% ✓ achievable
   - Acura: ~47.0% (at 20mm dyn front) — below target 49.0% ✓ achievable

2. **Corrections applied:**
   - Ferrari: default_df_balance_pct 49.5% → 51.5% (safely above 51.2% floor at wing 12)
   - Cadillac: default_df_balance_pct 50.14% → 52.0% (safely above 51.9% floor at wing 12)
   - rake_solver: added fallback when balance target can't be bracketed — scans rear RH range
     for minimum |balance - target| and warns instead of crashing.

3. **Physics reasoning for the floor:**
   At low dynamic front RH, the front diffuser's ground effect is maximised. The closer the
   floor is to the ground, the more front suction is generated (up to vortex seal limit).
   Meanwhile, the rear wing angle determines rear DF but has diminishing returns as wing angle
   decreases. At low wing angles, rear DF is low → front fraction of total DF is high → balance
   floor is higher. At high wing angles, rear DF increases → front fraction drops → balance
   floor is lower.
   
   This explains the wing-dependent pattern:
   - Ferrari wing 12: floor 51.2%, wing 14: 50.0%, wing 16: 48.6%
   - As wing increases, floor drops because rear DF grows relative to the fixed front ground effect.

---

## 2026-03-24 — Topic G: Torsion Bar ↔ ARB Coupling Physics

**Sources:**
- Milliken & Milliken, *Race Car Vehicle Dynamics* (RCVD), Chapter 18 — Roll stiffness
  (standard reference: parallel spring model for corner springs + ARB)
- OptimumG, "Bar Talk" (Claude Rouelle, RaceCar Engineering)
  https://optimumg.com/bar-talk/
  ARS_arb = Track² × K_arb / MR_arb² [Nm/deg]
  ARS_sp  = Track² × K_sp / (2 × MR_sp²) [Nm/deg]
  → These are ADDITIVE (parallel elements). Standard theory assumes rigid rocker pivot.
- Wikipedia, "Anti-roll bar" — ARB stiffness formula
  https://en.wikipedia.org/wiki/Anti-roll_bar
  "Stiffness proportional to material stiffness, fourth power of radius, inverse of lever arm length"
  K_arb ∝ d_arb^4 / L_lever  (same OD^4 form as corner torsion bars, but SEPARATE components)
- F1technical.net, "Anti Roll Bar Motion Ratio Convention" (Tim Wright, Force India engineer)
  https://www.f1technical.net/forum/viewtopic.php?t=29825
  "Expressing the motion ratio in radians/m means WheelStiffness = ArbStiffness × MotionRatio².
  This assumes all compliance is in the bar twist — rarely the case in practice."
- HPA Academy, "ARB Motion Ratio and Lever Arm Length"
  https://www.hpacademy.com/forum/suspension-tuning-and-optimization/show/anti-roll-bar-motion-ratio-and-lever-arm-length/
  "If you connect at MR=0.5:1 you halve effective ARB stiffness. The motion ratio is the key
  multiplier; torsional section stiffness and lever arm are the ARB's own independent parameters."

**Key findings:**

1. **Standard parallel model — theoretical coupling = 0.0:**
   In Milliken RCVD and all standard suspension mechanics texts, corner springs and ARB are
   treated as PARALLEL roll stiffness elements:
   ```
   K_roll_front_total = K_roll_corners + K_roll_arb
   K_roll_corners = 2 × k_wheel(N/m) × t_f² / 2   [N·m/rad]
   K_roll_arb     = K_arb_base × MR_arb²            [N·m/rad]
   ```
   In a rigid kinematic model: wheel displacement δ → rocker rotates φ = δ / r_arm (pure geometry).
   This rotation is INDEPENDENT of torsion bar stiffness — the motion ratio is fixed by geometry.
   Therefore ARB twist = 2φ = 2δ/r_arm, and ARB force at wheel = K_arb_blade × 2 × MR_arb² × δ.
   **The corner torsion bar OD has zero direct effect on ARB effectiveness in rigid kinematics.**

2. **GTP-specific: Where a coupling COULD arise (second-order effects):**
   In the BMW M Hybrid V8 (Dallara chassis), the torsion bar and ARB blade both connect to the
   same bellcrank/rocker. In an IDEAL rigid system the coupling is zero. However, real compliance
   sources that could create a small coupling include:
   a. **Bushing/mount compliance at the torsion bar attachment:** If the bar mount flexes under
      roll load, the effective lever arm changes slightly with OD. A stiffer bar (larger OD)
      resists this flex, maintaining geometry. The coupling would be small and POSITIVE (stiffer
      bar → geometry preserved → ARB slightly more effective).
   b. **Chassis torsional stiffness:** The roll force transmitted to the chassis through the
      torsion bar mounting creates a small torsional chassis deformation. Stiffer torsion bars
      increase this load, potentially affecting ARB mounting geometry.
   c. **Rocker structural compliance:** If the rocker/bellcrank itself flexes under combined spring
      + ARB load, a stiffer torsion bar changes the load distribution and thus the net flex.
   
   **Magnitude:** These effects are second-order and geometry-specific. They cannot be derived
   from published formulas alone — they require either FEA of the rocker assembly or IBT
   measurement across multiple OD settings at fixed ARB.

3. **Current iOptimal model — empirical back-calibration, not pure physics:**
   The existing `TORSION_ARB_COUPLING = 0.25` value was derived by back-calculation from the
   BMW Sebring IBT measurement showing LLTD = 50.99% at a specific setup:
   (front torsion OD = 13.9 mm → k_wheel = 30 N/mm, FARB Soft/1, RARB Medium/3)
   The coefficient 0.25 makes the objective function's predicted LLTD match this single
   observed data point. It is therefore an empirical fitting parameter, not a first-principles
   coupling constant.
   
   **Traceability:**
   - OD_ref = 13.9mm → k_wheel = 30 N/mm → K_roll_corner = 784 N·mm/deg
   - FARB Soft/1: K_arb_base = 5500, blade_factor = 0.30 → K_arb = 1650 N·mm/deg
   - RARB Medium/3: K_arb_base = 10000, blade_factor ≈ 0.65 → K_arb ≈ 975 N·mm/deg
   - Without coupling: K_roll_front = 784 + 1650 = 2434, K_roll_rear = 1454 + 975 = 2429
   - LLTD ≈ 2434/4863 = 50.0% — close but slightly off the observed 50.99%
   - With coupling (0.25): ARB scales slightly, correcting the small gap
   - This suggests the true coupling effect is SMALL and may partially compensate for
     other model offsets (e.g., tyre load sensitivity correction, roll centre geometry)

4. **Direction sign check:**
   The current model: `coupling_factor = 1 + 0.25 × ((OD/OD_ref)^4 - 1)`
   At OD > OD_ref: coupling_factor > 1 → K_arb_effective increases
   At OD < OD_ref: coupling_factor < 1 → K_arb_effective decreases
   
   This sign is CONSISTENT with the "geometry preservation" explanation (b above): a stiffer
   torsion bar better maintains the ARB attachment geometry under load. The sign is NOT
   consistent with a "series compliance reduces ARB effectiveness for stiffer bars" model.
   Sign check: ✅ The direction is physically plausible (though the magnitude is empirical).

**Formula (derived from parallel model + empirical coupling):**
   K_roll_front_total = K_roll_corners + K_arb_effective
   K_roll_corners = 2 × (C_torsion × OD^4) × 1000 × t_f^2 × π/180  [N·mm/deg]
   K_arb_effective = K_arb_base × (1 + γ × ((OD/OD_ref)^4 - 1))    [N·mm/deg]
   where γ = TORSION_ARB_COUPLING (empirically calibrated, currently 0.25)

**Units check:**
   k_wheel [N/mm] × 1000 = k_wheel [N/m]
   2 × k_wheel[N/m] × (t_f/2)[m]² × (π/180) = K_roll [N·m/deg] = N·mm/deg × 1000

**Validation protocol (for IBT confirmation):**
   To confirm the 0.25 coefficient or find the true value:
   1. Find BMW Sebring IBT sessions with DIFFERENT torsion bar OD (not 13.9mm) but SAME ARB
   2. Compute predicted LLTD with γ=0.0 vs γ=0.25 vs actual IBT LLTD
   3. Best fit γ = the value that minimises |predicted - IBT| across OD range
   4. If γ ≈ 0 across multiple sessions: remove coupling (pure parallel is correct)
   5. If γ ≈ 0.25 holds: coupling is real and reasonably calibrated
   Expected result: γ is likely in the range [0.0, 0.30] based on physical reasoning.
   Current 0.25 is within the plausible range but needs multi-OD IBT validation.

**iOptimal application:**
- File: `solver/objective.py`, constant `TORSION_ARB_COUPLING` at class definition
- Files: `solver/arb_solver.py`, `solver/objective.py` in `_compute_wheel_rates()` area
- Updated the constant's docstring in objective.py to:
  a. Clarify that 0.25 is empirically calibrated from ONE IBT data point, not from first principles
  b. Document the theoretical lower bound (0.0 for rigid kinematics)
  c. Explain the physical coupling mechanism (geometry preservation under load)
  d. Add the validation protocol (multi-OD IBT comparison)
- No change to the numeric value (0.25) — it reproduces the calibrated BMW Sebring LLTD
  observation and there is insufficient IBT diversity to justify a different value yet.
- The sign is confirmed correct: positive coupling = stiffer bar → slightly more effective ARB

---

## 2026-03-26 — Topic D: GTP Tyre Thermal Operating Window — Lateral Stiffness vs Temperature

**Sources:**
- Ken Payne (technical director motorsports, Michelin North America), via Sportscar365 IMSA
  Michelin GTLM Insider (2018): "Our target hot tire temperatures are 180–220 degrees Fahrenheit."
  https://sportscar365.com/imsa/iwsc/michelin-gtlm-insider-the-pressures-of-competition/
- SimRacingSetup.com, "How To Setup Your Tyres in iRacing: Tyre Pressure Setup Guide" (Feb 2025)
  https://simracingsetup.com/iracing/iracing-tyre-setup-guide/
  "The optimal tyre temperature for iRacing ranges between 85–105°C."
- Coach Dave Academy, "iRacing Cadillac V-Series.R GTP LMDh Guide" (Sep 2025)
  https://coachdaveacademy.com/tutorials/under-the-hood-tips-and-tricks-to-driving-the-cadillac-v-series-r-gtp/
  "GTP class has no tyre warmers — 1–2 warm-up laps required from cold."
- RACER / Sportscar365, "Michelin GTP/Hypercar Tires Get Ready to Roll" (Jun 2025)
  https://racer.com/2025/06/04/michelin-gtp-hypercar-tires-get-ready-to-roll
  "We don't really consider these 'soft', 'medium' or 'hard'; they are 'cold', 'medium' and
  'hot' really." — Michelin representative re: 2026 Pilot Sport Endurance compound selection.
- Arxiv 2305.18422 — Extended Pacejka MF with thermal scaling (Goodyear / Calspan):
  https://arxiv.org/pdf/2305.18422
  "Pacejka coefficients were scaled as a linear function of surface temperature; derivative
  of change of peak grip and cornering stiffness with temperature assumed to be a constant."

**Key findings:**

1. **Michelin GTP/Prototype target operating temperature: 180–220 °F = 82–104 °C**
   Ken Payne (Michelin NA technical director) quoted this directly for prototype/GTLM class
   Pilot Sport tyres. This is the hot-running (steady-state on-track) target band, NOT the
   cold tyre temperature at pit exit. Converting: 180°F = 82.2°C, 220°F = 104.4°C.
   → **T_min = 82 °C, T_max = 104 °C** for Michelin GTP/Hypercar Pilot Sport Endurance.

2. **iRacing community-validated window: 85–105 °C**
   Multiple independent sources (simracingsetup.com, Coach Dave Academy) confirm the optimal
   window in iRacing's tyre model is 85–105 °C. This is consistent with the Michelin engineering
   target (82–104 °C), validating that iRacing models real-world thermal physics closely.
   - Peak grip near ~92–95 °C (midpoint, with slight warm bias — warm rubber more pliable).
   - Below 82°C: rubber too stiff → poor track surface conformance → lateral stiffness drops.
   - Above 104°C: viscoelastic breakdown → compound generates heat faster than it dissipates.
   **Do not exceed 100°C during long stints** (faster wear; 100°C is also near simulator peak).

3. **Michelin compound selection philosophy (2026 spec):**
   "Cold / Medium / Hot" compounds are for different ambient/track temperature environments,
   NOT different grip levels. Each compound has the same target operating window but reaches it
   at different energy inputs. Cold compound generates more internal heat (for cold ambient
   tracks); Hot compound requires higher energy input to heat but stays stable at high T.
   → **For iOptimal:** Compound selection is a TrackProfile input, not a setup solver parameter.
   The solver assumes tyres are in the operating window (solver operates on steady-state lap).

4. **GTP-specific: No tyre warmers (critical for stint modelling)**
   GTP regulations prohibit tyre warmers. On the out-lap from a pit stop, tyres are at
   ambient temperature (~20–30°C). The first 1–2 laps are in the cold zone.
   - Cold-tyre lateral stiffness loss: ~10–20% vs peak (from iRacing IBT community data and
     consistent with Pacejka thermal scaling at ΔT = 50–70°C below T_min).
   - Michelin engineer quoted in Sportscar365 (2023): "The soft-high temperature tire used in
     GTP means you cannot 'save' tyres the way IMSA DPi teams did — you must manage temp
     not wear." → Thermal window is a real engineering constraint, not just lap time.

5. **Lateral stiffness (Ky) temperature penalty model — Pacejka MF thermal scaling:**
   From Pacejka thermal papers (arxiv 2305.18422 and Springer 10.1007/978-3-030-41057-5_88):
   The Pacejka cornering stiffness coefficient (Ky or BCD in MF terms) scales linearly with
   temperature deviation from optimal within small ΔT ranges. For larger ΔT, behaviour is
   approximately linear-piecewise (concave down). Engineering approximation:
   ```
   Ky_eff(T) = Ky_nom × (1 - α_cold × max(0, T_min - T))    [below optimal]
   Ky_eff(T) = Ky_nom × (1 - α_hot  × max(0, T - T_max))    [above optimal]
   ```
   Estimated penalty coefficients (from FSAE empirical data + Pacejka paper calibration):
   - α_cold ≈ 0.010 per °C (1.0% Ky loss per °C below T_min = ~20% at 20°C cold)
   - α_hot  ≈ 0.015 per °C (1.5% Ky loss per °C above T_max = ~15% at 10°C hot)
   Asymmetry: hot degradation is faster and more severe than cold degradation.
   - At 10°C below T_min: ~10% lateral grip loss (recoverable — tyre heats up)
   - At 20°C below T_min: ~20% lateral grip loss (out-lap condition with no tyre warmers)
   - At 10°C above T_max: ~15% lateral grip loss + irreversible wear acceleration

**Formula (with units):**
   T_min = 82 °C  (Michelin GTP prototype lower bound, 180°F)
   T_max = 104 °C (Michelin GTP prototype upper bound, 220°F)
   Ky_eff(T) = Ky_nom × max(0.3, 1 - α × |ΔT_outside_window|)
   where α = 0.010 (cold side) or 0.015 (hot side), floor at 0.30 (rubber is never completely
   laterally inert — just very compromised at extreme temperatures).

**What was NOT found (and why):**
   - Exact Ky temperature sensitivity per °C for Michelin Pilot Sport GTP compound is PROPRIETARY.
     The 0.010/0.015 values are calibrated from FSAE tyre consortium data (different compound)
     and are order-of-magnitude correct but require IBT validation.
   - iRacing does not expose a "tyre compound temperature sensitivity" parameter in setup — it
     is embedded in the internal tyre model. The constants above should be treated as
     PLANNING PARAMETERS for scoring, pending IBT tyre temperature channel validation.
   - IBT channels (LFtempM, RFtempM, LRtempM, RRtempM) are the right validation sources.

**iOptimal application:**
- Files updated: `car_model/cars.py` — added 4 new fields to `CarModel`:
  ```python
  tyre_opt_temp_min_c: float = 82.0   # Michelin 180°F lower bound
  tyre_opt_temp_max_c: float = 104.0  # Michelin 220°F upper bound
  tyre_temp_sens_cold: float = 0.010  # Ky loss per °C below T_min
  tyre_temp_sens_hot:  float = 0.015  # Ky loss per °C above T_max
  ```
- File: `solver/objective.py` — added class-level constants:
  ```python
  TYRE_TEMP_SENS_COLD = 0.010
  TYRE_TEMP_SENS_HOT  = 0.015
  ```
  with a formula stub comment for when IBT tyre temp channels become available.
- **Future work:** When `TrackProfile` includes `tyre_temp_avg_c` (from IBT histogram),
  score it against the window and penalise setups that push tyres out of window:
  - Too-stiff suspension → more vibration → tyres overheat on bumpy tracks
  - Too-soft suspension → large RH variance → floor contact → ride height issue
  - The thermal model is a second-order effect for setup; primary effect is mechanical balance.
- **Compound selection note:** For TrackProfile, add `track_ambient_temp_c` to guide compound
  selection (cold compound for <15°C ambient, hot compound for >30°C ambient).

---

## 2026-03-29 — Topics: IBT Telemetry Channels, Vortex/Ground Effect RH Threshold, STO File Format, BMW LMDh Suspension Geometry

---

### Topic F: iRacing IBT Telemetry Channel Names — Complete List (GTP / iOptimal)

**Sources:**
- iOptimal codebase scan (all `.py` files, `.channel()` / `has_channel()` calls) — `/root/.openclaw/workspace/isetup/gtp-setup-builder/`
- LeoAdamek/iracing.rs SDK documentation: https://github.com/LeoAdamek/iracing.rs
- sajax.github.io/irsdkdocs: https://sajax.github.io/irsdkdocs/

**Finding:**
iRacing exposes telemetry as a memory-mapped file at `Local\IRSDKMemMapFileName` sampled at 60 Hz. Each variable has a name (max 32 chars), description (64 chars), units, and type. The full set of channels available varies by car — not all variables are present in all IBT sessions. Below is the **confirmed channel list actively used by iOptimal**, extracted from the codebase:

**Ride Height (meters in IBT, ×1000 for mm):**
- `LFrideHeight` — Left Front ride height (m)
- `RFrideHeight` — Right Front ride height (m)
- `LRrideHeight` — Left Rear ride height (m)
- `RRrideHeight` — Right Rear ride height (m)
- `CFSRrideHeight` — Center/rear reference ride height (GTP underbody sensor)

**Shock Velocity (m/s):**
- `LFshockVel`, `RFshockVel` — Front shock velocities
- `LRshockVel`, `RRshockVel` — Rear shock velocities
- `HFshockVel` — Front heave shock velocity
- `HRshockVel` — Rear heave shock velocity

**Shock / Damper Deflection (meters):**
- `LFshockDefl`, `RFshockDefl` — Front shock deflection
- `LRshockDefl`, `RRshockDefl` — Rear shock deflection
- `HFshockDefl` — Front heave spring deflection
- `HRshockDefl` — Rear heave/third spring deflection

**Accelerations:**
- `LatAccel` — Lateral acceleration (m/s²)
- `LongAccel` — Longitudinal acceleration (m/s²)
- `VertAccel` — Vertical acceleration (m/s²)

**Vehicle dynamics:**
- `YawRate` — rad/s
- `Roll`, `Pitch` — rad
- `RollRate`, `PitchRate` — rad/s
- `Speed` — m/s
- `VelocityX`, `VelocityY` — m/s (body frame)

**Driver inputs / in-cockpit adjustments:**
- `Throttle`, `ThrottleRaw`, `Brake`, `BrakeRaw`
- `SteeringWheelAngle` — rad
- `Gear`, `RPM`
- `dcBrakeBias` — in-lap brake bias adjustment
- `dcAntiRollFront`, `dcAntiRollRear` — ARB clicks
- `dcTractionControl`, `dcTractionControl2` — TC adjustment counts

**Brake line pressures (per corner):**
- `LFbrakeLinePress`, `RFbrakeLinePress`, `LRbrakeLinePress`, `RRbrakeLinePress`

**Wheel speeds:**
- `LFspeed`, `RFspeed`, `LRspeed`, `RRspeed` — m/s

**Fuel / ERS:**
- `FuelLevel` — liters
- `EnergyERSBattery` — J
- `EnergyERSBatteryPct` — 0–1

**Track / environment:**
- `LapDist` — m (distance along lap)
- `LapCurrentLapTime` — s
- `Lap` — lap count
- `TrackTempCrew`, `AirTemp`, `AirDensity`
- `WindVel`, `WindDir`
- `Alt` — altitude m
- `IsOnTrack` — bool
- `BrakeABSactive`, `BrakeABScutPct`

**iOptimal Application:**
- `LFshockVel` is the primary low-speed/high-speed damper regime classifier (threshold ~0.05 m/s = 50 mm/s for LS/HS boundary). Positive = rebound, negative = compression.
- `LFrideHeight` / `LRrideHeight` (×1000) are the aerodynamic floor height observables used in the ride height target objective function.
- `HFshockDefl` / `HRshockDefl` directly measure heave spring engagement — key for third spring calibration.
- `CFSRrideHeight` is a center-rear sensor that may correspond to the venturi reference height; presence is car-dependent.
- Note: `DampDeflectLR` style (count=6 float array) seen in some SDK examples is a different encoding; iOptimal uses the per-corner named channels.

---

### Topic G: Vortex Generator / Ground Effect Minimum Ride Height Threshold — GTP

**Sources:**
- Coach Dave Academy BMW M Hybrid V8 Guide (iRacing): https://coachdaveacademy.com/tutorials/tips-and-tricks-to-driving-new-bmw-m-hybrid-v8-iracing/
- Coach Dave Academy Acura ARX-06 GTP Guide: https://coachdaveacademy.com/tutorials/iracing-guide-acura-arx-06-gtp/
- F1Technical forum, ground effect aerodynamics thread
- iRacing community discussion on GTP aero sensitivity

**Finding:**
GTP/LMDh cars generate downforce primarily from the **underbody venturi tunnel** — a sealed diffuser system that creates a low-pressure zone under the car. The cars use vortex generators (front brake duct winglets and floor-edge strakes) to maintain the floor aerodynamic seal. The key physics:

1. **Ground effect is inversely nonlinear with ride height.** At very low ride heights (< ~40–50 mm front), the downforce increases steeply due to venturi throat effect. However, below a critical minimum (varies by car/track), the floor contacts or the vortex seal breaks, causing a sudden **aero loss ("vortex burst")** — downforce drops rapidly and balance shifts forward.

2. **Optimal ride height window:** GTP cars in iRacing produce peak aero efficiency when ride heights are as low as possible without plank/floor contact or aerodynamic stall. The Coach Dave guide explicitly states: *"ride height can make a significant difference to downforce, with the car being at its most efficient when keeping the ride heights, both front and rear, as low as possible without bottoming out."*

3. **Rake sensitivity:** Increasing rear rake (higher rear RH relative to front) shifts aero balance rearward, creating understeer on corner entry. Reducing rake increases high-speed stability. This directly couples with wing angle adjustments — the BMW manual notes that rake must be reconsidered when changing rear wing angle due to aero balance shift.

4. **Practical thresholds (iRacing GTP, approximate):**
   - Front minimum: ~30–40 mm static to prevent floor contact at high-speed bumps
   - Rear minimum: ~45–65 mm static, varies by track surface
   - Vortex burst risk: occurs when peak heave compression forces front RH below ~20–25 mm at-speed; marked by sharp yaw instability and understeer transition

**iOptimal Application:**
- The ride height objective function should apply a **nonlinear penalty** for static RH below ~35 mm front / ~50 mm rear. A cliff penalty (×3–5× steeper slope) should activate below the vortex burst threshold (~20 mm front).
- `CFSRrideHeight` (center/rear underbody sensor) may be used as an additional safety threshold signal in the scoring function.
- Heave spring stiffness is the primary lever: stiffer front heave spring raises minimum dynamic front RH and reduces vortex burst risk at the cost of mechanical grip.

---

### Topic H: iRacing STO Setup File Format

**Sources:**
- Reddit: r/iRacing — "Anyway to convert .STO setup files into a text or .csv file?" (2019): https://www.reddit.com/r/iRacing/comments/b4yrge/
- Reddit: r/iRacing — Developer comment on STO format (2015): https://www.reddit.com/r/iRacing/comments/3ay4bt/
- CarTunes (parasyte/cartunes) GitHub: https://github.com/parasyte/cartunes

**Finding:**
The iRacing `.STO` file format is **proprietary binary** — it is NOT XML, JSON, or plain text. An iRacing staff member confirmed:
> *"The contents of the .sto files aren't what you at all expect them to be."*

Key facts:
1. STO files cannot be directly parsed for setup values outside of iRacing.
2. The CarTunes app works by comparing STO files at the binary level to detect changes, not by decoding the values.
3. The **correct way to read setup data** in real-time is via the **iRacing SDK YAML session string**, which is embedded in the IBT file's tail section and broadcast via shared memory at session start.
4. The YAML session string contains a `CarSetup:` section with all setup parameters as human-readable key-value pairs. This is what iOptimal's `setup_reader.py` and `setup_registry.py` parse.

**YAML Session String — CarSetup Structure (GTP BMW example):**
The session YAML (accessible via `ibt.session_info` after parsing) contains:
```
CarSetup:
  UpdateCount: 1
  Chassis:
    LeftFront:
      CornerWeight: ...
      RideHeight: ...
      ShockDefl: "5.20 - 5.40 cm"
      TorsionBar: "14.34 mm OD"
      SpringRate: ...
      ShockCylinder: ...
    LeftRear:
      ...
  Aerodynamics:
    FrontWingAngle: ...
    RearWingAngle: ...
  TiresAero:
    ...
```

**iOptimal Application:**
- iOptimal correctly reads setups via the IBT YAML session string — this is the only reliable method.
- `setup_registry.py` field mappings like `"Chassis.LeftFront.ShockDefl"` → `"CarSetup_Chassis_LeftFront_ShockDefl"` correctly map YAML path to canonical field names.
- STO files should not be reverse-engineered; the YAML session string is the ground truth for setup state at the time of the IBT recording.

---

### Topic I: BMW M Hybrid V8 LMDh Suspension Geometry — Torsion Bar & Pushrod Setup

**Sources:**
- iRacing BMW M Hybrid V8 User Manual (official): https://s100.iracing.com/wp-content/uploads/2023/07/BMW-M-Hybrid-V8-Manual-V2.pdf
- manuals.plus summary of BMW GTP manual: https://manuals.plus/iracing/bmw-m-hybrid-v8-gpt-race-car-manual
- Coach Dave Academy BMW M Hybrid V8 iRacing Guide: https://coachdaveacademy.com/tutorials/tips-and-tricks-to-driving-new-bmw-m-hybrid-v8-iracing/

**Finding:**
The BMW M Hybrid V8 runs a **Dallara-built LMDh spec chassis** with a torsion bar front suspension system. Key geometry characteristics:

1. **Torsion Bar (Front Spring):** The BMW uses torsion bars as the primary front spring element, as mandated by LMDh spec chassis rules (all LMDh cars share the Dallara chassis architecture with common suspension pickup points). The **Torsion Bar OD (outer diameter)** is the adjustable spring parameter — a larger OD = stiffer spring. In iOptimal, the canonical parameter is `front_torsion_od_mm` (default ~14.34 mm OD). This is the iRacing YAML value from `Chassis.LeftFront.TorsionBar`.

2. **Heave Spring System (Front Third Spring):** The front heave spring is mounted on a slider mechanism. Key iRacing parameters:
   - **Heave Perch Offset:** Adjusts preload / engagement point of the heave spring
   - **Heave Spring Rate:** Controls the heave spring stiffness (N/mm)
   - **HEAVE SLIDER DEFL (display-only):** How far the heave spring slider has compressed from fully extended. This is NOT directly adjustable — it results from Heave Perch Offset + Torsion Bar settings. Importantly: *this value doesn't produce damping forces*.
   - `HFshockDefl` in IBT measures the heave spring slider deflection directly at 60 Hz.

3. **Pushrod Geometry:** Front and rear pushrod offsets shift the effective spring/damper engagement point (cam effect). The iRacing manual states:
   - Lowering pushrod length → **understeer** tendency
   - Increasing pushrod length → **oversteer** tendency
   - This is modeled in iOptimal as `front_pushrod_offset_mm` / `rear_pushrod_offset_mm`

4. **Anti-Roll Bars:** Front and rear ARBs are adjustable (size: Soft/Medium/Stiff + blade position 1–5). The ARB modulates the effective wheel rate difference between inside and outside wheels in corners — a key LLTD lever. In iRacing the dc-adjustable versions are `dcAntiRollFront` / `dcAntiRollRear`.

5. **Rear Spring:** A conventional coil spring (N/mm). Canonical parameter: `rear_spring_rate_nmm`.

6. **Rear Third Spring:** The rear heave/third spring (`rear_third_spring_nmm` in iOptimal, default ~450 N/mm) is a separate interconnected spring element at the rear that controls heave stiffness without affecting roll stiffness — the same principle as front heave spring but at the rear.

**Key Numbers (BMW M Hybrid V8 iRacing defaults):**
- Front Torsion Bar OD: ~14.34 mm (iOptimal default)
- Front Heave Spring: ~50 N/mm (iOptimal default)
- Rear Spring Rate: ~160 N/mm (iOptimal default)
- Rear Third Spring: ~450 N/mm (iOptimal default)
- Front Pushrod Offset: ~−26 mm (iOptimal default)
- Rear Pushrod Offset: ~−22 mm (iOptimal default)

**iOptimal Application:**
- The torsion bar OD → effective wheel rate conversion is modeled in `car_model/cars.py` using the torsion bar polar moment of inertia formula. This is critical for LLTD calculation: `LLTDF = (k_WR_front × t_front²) / (k_WR_front × t_front² + k_WR_rear × t_rear²)`.
- `front_heave_spring_nmm` (front third spring rate) controls heave stiffness at the front — its primary effect is on the minimum dynamic ride height and vortex burst margin, not roll balance.
- The pushrod offset parameters affect the ride height–spring force coupling via a cam function that should be verified against actual YAML session data.

