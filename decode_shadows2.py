#!/usr/bin/env python3
import pathlib
import re
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

DATA_DIR = pathlib.Path(".")
BASELINE_NAME = "Baseline_Ch1.syx"

OUTPUT_PATH = pathlib.Path("analysis_output.txt")   # <--- NEW

CC_FILE_REGEX = re.compile(r"^C1_CC(\d+)\.syx$", re.IGNORECASE)

def encode_cc_hyp(cc: int) -> Optional[int]:
    if 1 <= cc <= 24:
        return cc + 16
    if 25 <= cc <= 36:
        return cc
    if 37 <= cc <= 48:
        return cc - 16
    if 49 <= cc <= 72:
        return cc - 32
    return None


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def load_bytes(path: pathlib.Path) -> bytes:
    with path.open("rb") as f:
        return f.read()

def find_cc_files() -> Dict[int, pathlib.Path]:
    cc_files: Dict[int, pathlib.Path] = {}
    for p in DATA_DIR.iterdir():
        if not p.name.lower().endswith(".syx"):
            continue
        m = CC_FILE_REGEX.match(p.name)
        if not m:
            continue
        cc_val = int(m.group(1))
        cc_files[cc_val] = p
    return cc_files

def diffs(a: bytes, b: bytes) -> List[Tuple[int, int, int]]:
    if len(a) != len(b):
        raise ValueError(f"Length mismatch: {len(a)} vs {len(b)}")
    return [(i, a[i], b[i]) for i in range(len(a)) if a[i] != b[i]]

def affine_fit(xs: List[int], ys: List[int]) -> Optional[Tuple[int, int]]:
    if len(xs) < 2:
        return None

    x0, y0 = xs[0], ys[0]

    if all(y == y0 for y in ys):
        return 0, y0

    a_num = None
    a_den = None
    for i in range(1, len(xs)):
        if xs[i] == x0:
            continue
        dx = xs[i] - x0
        dy = ys[i] - y0
        a_num = dy
        a_den = dx
        break

    if a_num is None:
        return None

    if a_num % a_den != 0:
        return None
    a = a_num // a_den

    b = ys[0] - a * xs[0]
    for x, y in zip(xs, ys):
        if y != a * x + b:
            return None
    return a, b

def nibble_split(byte_val: int) -> Tuple[int, int]:
    hi = (byte_val >> 4) & 0xF
    lo = byte_val & 0xF
    return hi, lo


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    out = []    # <--- buffer for output text

    def write(s=""):
        out.append(str(s))

    baseline_path = DATA_DIR / BASELINE_NAME
    baseline = load_bytes(baseline_path)
    write(f"Loaded baseline {baseline_path.name} with {len(baseline)} bytes")

    cc_files = find_cc_files()
    if not cc_files:
        write("No C1_CCxxx.syx files found.")
        OUTPUT_PATH.write_text("\n".join(out))
        return

    cc_values = sorted(cc_files.keys())
    write(f"Found CC variant files for CC values: {cc_values}")

    cc_bytes: Dict[int, bytes] = {}
    for cc, path in cc_files.items():
        buf = load_bytes(path)
        if len(buf) != len(baseline):
            raise ValueError(f"Length mismatch: {len(buf)} vs {len(baseline)}")
        cc_bytes[cc] = buf

    per_cc_offsets: Dict[int, List[int]] = {}
    for cc, buf in cc_bytes.items():
        ds = diffs(baseline, buf)
        per_cc_offsets[cc] = [i for i, _, _ in ds]

    all_offsets_sets = [set(offs) for offs in per_cc_offsets.values()]
    common_offsets = sorted(set.intersection(*all_offsets_sets))
    write("")
    write(f"Offsets that change for every CC file: count={len(common_offsets)}")
    write(common_offsets)

    main_candidates = []
    for off in common_offsets:
        bytes_here = [cc_bytes[cc][off] for cc in cc_values]
        ok = True
        for cc, bval in zip(cc_values, bytes_here):
            enc = encode_cc_hyp(cc)
            if enc is None or enc != bval:
                ok = False
                break
        if ok:
            main_candidates.append(off)

    write("")
    write(f"Main CC offset candidates: {main_candidates}")
    if len(main_candidates) == 1:
        write(f"Confirmed main CC offset at {main_candidates[0]} (0x{main_candidates[0]:04X})")

    shadow_offsets = common_offsets
    write("")
    write(f"Shadow offsets (count={len(shadow_offsets)}):")
    write(shadow_offsets)
    write("")
    write("=== Shadow offset analysis ===")
    write("")

    for off in shadow_offsets:
        mapping = [(cc, cc_bytes[cc][off]) for cc in cc_values]
        xs = [cc for cc, _ in mapping]
        ys = [bv for _, bv in mapping]

        write(f"Offset {off} (0x{off:04X}):")
        write("  Raw mapping CC -> byte:")
        for cc, bval in mapping:
            hi, lo = nibble_split(bval)
            write(f"    CC {cc:3d} -> 0x{bval:02X} (hi={hi:X}, lo={lo:X})")

        aff = affine_fit(xs, ys)
        if aff:
            a, b = aff
            write(f"  Whole-byte affine fit: byte = {a} * CC + {b}")
        else:
            write("  No whole-byte affine fit")

        hi_vals = [(b >> 4) & 0xF for b in ys]
        lo_vals = [b & 0xF for b in ys]

        hi_aff = affine_fit(xs, hi_vals)
        lo_aff = affine_fit(xs, lo_vals)

        if hi_aff:
            a, b = hi_aff
            write(f"  High nibble affine fit: hi = {a} * CC + {b}")
        else:
            write(f"  High nibble unique: {sorted(set(hi_vals))}")

        if lo_aff:
            a, b = lo_aff
            write(f"  Low nibble affine fit: lo = {a} * CC + {b}")
        else:
            write(f"  Low nibble unique: {sorted(set(lo_vals))}")

        deltas = [ys[i+1] - ys[i] for i in range(len(ys) - 1)]
        write(f"  Deltas: {deltas}")
        write(f"  Unique deltas: {sorted(set(deltas))}")
        write("")

    OUTPUT_PATH.write_text("\n".join(out))
    print(f"Output written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
