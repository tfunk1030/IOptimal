"""Apply targeted edits to solver/objective.py preserving byte-exact encoding."""
import pathlib

p = pathlib.Path(r"C:\Users\VYRAL\IOptimal\solver\objective.py")
raw = p.read_bytes()

# Fix 1: LLTD penalty coefficient 8.0 -> 2.5
raw = raw.replace(
    b"lltd_error * 100.0 * 8.0",
    b"lltd_error * 100.0 * 2.5",
)

# Fix 2: LLTD cap 40 -> 25
raw = raw.replace(
    b"min(40.0, lltd_penalty)",
    b"min(25.0, lltd_penalty)",
)

# Fix 3: LLTD comment
raw = raw.replace(
    b"Each 1% LLTD error costs ~8ms (balance is important but not everything)",
    b"Each 1% LLTD error costs ~2.5ms (tuned: old 8.0 amplified ARB blades via LLTD)",
)

# Fix 4: DF balance coefficient 45 -> 20
raw = raw.replace(
    b"df_balance_error_pct * 45.0",
    b"df_balance_error_pct * 20.0",
)

# Fix 5: DF comment
raw = raw.replace(
    b"Each 0.1% DF balance error costs ~4.5ms at high-speed tracks",
    b"Each 0.1% DF balance error costs ~2ms (tuned from 45 to 20)",
)

p.write_bytes(raw)
print("Done - edits applied")
