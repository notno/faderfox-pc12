import os
import re
from collections import defaultdict

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

BASELINE_FILE = "Baseline_Ch1.syx"
CC_FILE_PATTERN_PREFIX = "C1_CC"   # expects filenames like C1_CC001.syx

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def load_bytes(path):
    with open(path, "rb") as f:
        return f.read()

def find_cc_from_filename(name):
    """
    Extract CC number from filenames like 'C1_CC001.syx', 'C1_CC074.syx', etc.
    Returns int or None if pattern does not match.
    """
    m = re.search(r"CC(\d+)", name)
    if not m:
        return None
    return int(m.group(1))

def diffs(a, b):
    """Return list of (offset, byte_a, byte_b) where a != b."""
    return [(i, a[i], b[i]) for i in range(len(a)) if a[i] != b[i]]

# Hypothesis for main CC encode function - adjust if needed
def encode_cc_hyp(cc):
    """
    Piecewise hypothesized encoding of CC to main field byte.
    This is what we want to *confirm*.
    """
    if 1 <= cc <= 24:
        return cc + 16
    if 25 <= cc <= 36:
        return cc
    if 37 <= cc <= 48:
        return cc - 16
    if 49 <= cc <= 72:
        return cc - 32
    # Outside the "normal" knob CC range - may need adjustment or just return None
    return None

def fit_affine(xs, ys):
    """
    Try to fit an affine map y = a*x + b over integers (no modulo).
    Returns (a, b) if exact, else None.
    """
    if len(xs) < 2:
        return None
    x0, y0 = xs[0], ys[0]
    for i in range(1, len(xs)):
        x1, y1 = xs[i], ys[i]
        dx = x1 - x0
        dy = y1 - y0
        if dx != 0:
            a = dy / dx
            b = y0 - a * x0
            break
    else:
        # All xs equal - cannot fit a meaningful global affine
        return None
    # Require integer coefficients
    if abs(a - round(a)) > 1e-9 or abs(b - round(b)) > 1e-9:
        return None
    a_int, b_int = int(round(a)), int(round(b))
    # Verify
    for x, y in zip(xs, ys):
        if a_int * x + b_int != y:
            return None
    return a_int, b_int

# ------------------------------------------------------------
# Step 1: Load baseline and CC variant files
# ------------------------------------------------------------

files = [f for f in os.listdir(".") if f.endswith(".syx")]
if BASELINE_FILE not in files:
    raise FileNotFoundError(f"Baseline file {BASELINE_FILE} not found in current directory")

baseline = load_bytes(BASELINE_FILE)
length = len(baseline)
print(f"Loaded baseline {BASELINE_FILE} with {length} bytes")

cc_files = []
for f in files:
    if f == BASELINE_FILE:
        continue
    if f.startswith(CC_FILE_PATTERN_PREFIX):
        cc = find_cc_from_filename(f)
        if cc is not None:
            cc_files.append((cc, f))

if not cc_files:
    raise RuntimeError("No C1_CCxxx.syx files found")

cc_files.sort(key=lambda t: t[0])
print("Found CC variant files for CC values:", [cc for cc, _ in cc_files])

# Sanity check lengths
for cc, fname in cc_files:
    data = load_bytes(fname)
    if len(data) != length:
        raise ValueError(f"Length mismatch: {fname} has {len(data)} bytes, baseline has {length}")

# ------------------------------------------------------------
# Step 2: Find all offsets that change for each CC file
# ------------------------------------------------------------

per_cc_diffs = {}
for cc, fname in cc_files:
    data = load_bytes(fname)
    d = diffs(baseline, data)
    per_cc_diffs[cc] = d

# Intersection of offsets that change for all CC variants:
offset_sets = [set(i for i, _, _ in d) for d in per_cc_diffs.values()]
common_offsets = set.intersection(*offset_sets)
common_offsets = sorted(common_offsets)

print(f"\nOffsets that change for every CC file (candidate main+shadow): count={len(common_offsets)}")
print(common_offsets)

# ------------------------------------------------------------
# Step 3: Build mapping offset -> {cc: byte_value}
# ------------------------------------------------------------

offset_to_ccmap = {off: {} for off in common_offsets}
for cc, fname in cc_files:
    data = load_bytes(fname)
    for off in common_offsets:
        offset_to_ccmap[off][cc] = data[off]

# ------------------------------------------------------------
# Step 4: Identify which offset is the main CC byte
#         by checking against encode_cc_hyp
# ------------------------------------------------------------

main_offset_candidates = []
for off in common_offsets:
    mapping = offset_to_ccmap[off]
    ok = True
    for cc, val in mapping.items():
        pred = encode_cc_hyp(cc)
        if pred is None or pred != val:
            ok = False
            break
    if ok:
        main_offset_candidates.append(off)

print("\nMain CC offset candidates (matching encode_cc_hyp):", main_offset_candidates)

if len(main_offset_candidates) != 1:
    print("Warning: expected exactly 1 main CC offset. You may need to refine encode_cc_hyp or selection logic.")
main_offset = main_offset_candidates[0] if main_offset_candidates else None

if main_offset is not None:
    print(f"\nChosen main CC offset: {main_offset} (0x{main_offset:04X})")
    print("Main CC mapping (CC -> byte):")
    for cc in sorted(offset_to_ccmap[main_offset].keys()):
        val = offset_to_ccmap[main_offset][cc]
        print(f"  CC {cc:3d} -> 0x{val:02X}")
else:
    print("\nNo main CC offset found - cannot proceed cleanly for main CC verification.")

# Shadow offsets are all common offsets except the main one
shadow_offsets = [off for off in common_offsets if off != main_offset]
print(f"\nShadow offsets (count={len(shadow_offsets)}):")
print(shadow_offsets)

# ------------------------------------------------------------
# Step 5: For each shadow offset, build table and try simple fits
# ------------------------------------------------------------

print("\n=== Shadow offset analysis ===\n")

for off in shadow_offsets:
    mapping = offset_to_ccmap[off]
    ccs = sorted(mapping.keys())
    ys = [mapping[cc] for cc in ccs]

    print(f"Offset {off} (0x{off:04X}):")
    print("  Raw mapping CC -> byte:")
    for cc in ccs:
        v = mapping[cc]
        print(f"    CC {cc:3d} -> 0x{v:02X} (hi={v>>4:01X}, lo={v&0xF:01X})")

    # Try affine fit
    affine = fit_affine(ccs, ys)
    if affine is not None:
        a, b = affine
        print(f"  Affine fit: byte = {a} * CC + {b}")
    else:
        print("  No exact affine fit over integers")

    # Check monotone delta pattern
    deltas = [ys[i+1] - ys[i] for i in range(len(ys)-1)]
    unique_deltas = sorted(set(deltas))
    print(f"  Deltas between successive CCs: {deltas}")
    print(f"  Unique deltas: {unique_deltas}")

    print()  # blank line between offsets

print("\nDone. Use the main offset for CC encoding, and inspect shadow offset patterns above.\n")
