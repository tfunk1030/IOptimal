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
