You are building a physics-based setup solver for iRacing GTP/Hypercar cars. The repo is at this directory. Read `gtp-setup-builder/CLAUDE.md` for architecture.
## CRITICAL PHILOSOPHY
This solver must DERIVE setups from physics equations, NOT reproduce known setups. Real setup files exist in `data/calibration/` — use them ONLY to validate that your physics model is correct. If your physics disagrees with the real setup, that is INTERESTING and worth investigating. Do NOT tune constants to make the output match.
The test: if someone asks "why is front LS comp 7 clicks?", the answer must be a physics derivation (critical damping coefficient, damping ratio, force-per-click), NOT "because the real setup uses 7."
## AVAILABLE DATA
1. **Real IBT telemetry** (`data/telemetry/bmw_sebring_2026-03-06.ibt`) — 48MB, 42K samples @ 60Hz, 299 channels including shock velocities, ride heights, lateral/longitudinal/vertical acceleration, body roll, tyre temps. IBT v2 format: data starts at `session_info_offset + session_info_len` (byte 60801), type 4 = float (not bitfield despite the header label), buf_len = 1152 bytes per sample.
2. **Parsed track profile** (`data/tracks/sebring_international_raceway_international.json`) — already contains real shock velocity distributions, lateral G, body roll, ride heights from the IBT.
3. **Real setups** (`data/calibration/bmw_sebring_s1.ldx` and `bmw_sebring_s2.ldx`) — two different philosophies:
   - S1: soft platform (heave 30, third 320), comp-biased dampers (HS comp > HS rbd)
   - S2: stiff platform (heave 60, third 450), rbd-biased dampers (HS rbd > HS comp)
   - Both use: wing 17, front ARB Soft/1, rear ARB Soft/3, front RH 30mm
4. **33 aero maps** (`data/aero-maps/`) — all 5 GTP cars, all wing angles, 51×46 grids (front RH 25-75mm × rear RH 5-50mm)
5. **Car model** (`car_model/cars.py`) — BMW fully defined. Other 4 cars need params.
## WHAT THE SOLVER DOES NOW
Run: `cd gtp-setup-builder && python3 -m solver.solve --car bmw --track sebring --wing 17 --report-only`
6 steps: rake → heave/third → corner springs → ARBs → wheel geometry → dampers.
Current results vs real S2: 17/25 exact, 7/25 within 2, 1/25 off by >2.
## WHAT NEEDS WORK (think from physics, don't curve-fit)
### A. Damper solver reads real IBT data
The damper solver (`solver/damper_solver.py`) uses the track profile for HS slope calculation but has hardcoded reference velocities. Make it read the REAL shock velocity distributions from the track profile JSON:
- Front: p95=118.3, p99=238.1 mm/s (p99/p95 = 1.658)  
- Rear: p95=149.9, p99=299.5 mm/s (p99/p95 = 1.568)
- Front and rear are DIFFERENT — the rear is 27% more active. The solver should use separate front/rear reference velocities.
### B. Camber from real lateral G distribution
The IBT shows time-weighted lateral G: mean=0.94g, p95=2.02g, max=4.53g. The car spends most cornering time at ~2g, not the theoretical 4.3g peak. Think about what this means for optimal static camber:
- At what roll angle does the car spend 95% of its cornering time?
- What static camber gives zero dynamic camber at THAT roll angle?
- Is zero dynamic camber even the right target, or does tyre physics favor slight negative?
- The real setup uses -2.9° front / -1.8° rear. Don't target these — derive what the physics says and compare.
### C. Body roll model is wrong
Solver predicts max roll = 2.1°. Real IBT shows max = 3.9°, p95 = 1.67°. The roll stiffness calculation is ~2x too stiff. Investigate:
- Is the spring-to-roll-stiffness conversion correct? (check units, motion ratios)
- Is the ARB contribution being double-counted or overestimated?
- What does the real roll vs lateral G relationship look like in the IBT? Extract roll vs lat_accel scatter plot data to derive the actual roll gradient (deg/g).
### D. LLTD and ARB model LLTD achieved = 30.5% vs target 52%. The ARB stiffness values in the model are far too small relative to spring roll stiffness. Either:
- The ARB stiffness constants are wrong (front [5500, 11000, 16500], rear [5000, 10000, 15000] N·m/deg)
- The motion ratio for the ARB is wrong
- The formula is wrong
Extract the REAL roll stiffness distribution from IBT (front vs rear ride height deflection in corners) to derive actual LLTD.
### E. IBT parser pipeline
`track_model/ibt_parser.py` exists but is disconnected. Build a proper flow:
1. `python3 -m track_model.build_profile --ibt data/telemetry/bmw_sebring_2026-03-06.ibt --output data/tracks/sebring.json`
2. Parser extracts: shock velocity histograms (front/rear separate), lateral G distribution, body roll distribution, ride height distributions, speed profile, braking zones
3. The struct-based parsing that works: data_start = session_info_offset + session_info_len, all type 4 channels are float (4 bytes), var headers at offset 144, each 144 bytes wide, name at byte 16 in header.
### F. Remaining car models
Add Cadillac, Ferrari, Porsche, Acura to `car_model/cars.py`. They need mass, weight distribution, wheelbase, track width, spring types/ranges, ARB options, damper ranges, motion ratios. Research iRacing community data or garage screen values. Aero maps are already parsed for all 5 cars.
### G. Output module
Build `output/setup_writer.py`:
- Generate iRacing `.sto` setup XML from solver output (see LDX format in `data/calibration/`)
- Generate engineering report with physics reasoning for each parameter
- Include validation section: predicted vs measured (if IBT available)
## VALIDATION APPROACH
After ANY change to the solver, run it and compare to S2. But don't optimize FOR S2. The goal:
1. Run solver → get physics-derived output
2. Compare to S2 → identify disagreements  
3. For each disagreement, ask: "Is my physics wrong, or is the real setup suboptimal?"
4. If physics is wrong: fix the physics model (equations, not constants)
5. If real setup might be suboptimal: document the reasoning — this is valuable insight
The solver should be able to produce BOTH S1-style and S2-style setups by changing input parameters (e.g., ride philosophy = "compliant" vs "locked platform"), not by hard-coding one philosophy.
## DO NOT
- Tune magic constants to match known setups
- Use lookup tables or "if car == bmw then value = X" 
- Anchor to baseline values from real setups
- Ignore disagreements between physics and real data
- Modify `gtp-telemetry/src/lib/ibt-parser.ts` or `analysis-engine.ts`