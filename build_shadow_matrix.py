#!/usr/bin/env python3
import csv
import pathlib
import re
from typing import Dict, List, Tuple

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

# Directory containing all your C1_CCxxx.syx files
DATA_DIR = pathlib.Path(".")

# Regex to match your sweep files and extract the CC number
# Adjust if your naming is slightly different.
CC_FILE_REGEX = re.compile(r"^C1_CC(\d+)\.syx$", re.IGNORECASE)

# Output CSV for the analysis script
OUTPUT_CSV = pathlib.Path("shadow_matrix.csv")

# IMPORTANT: fill this with your 70 absolute shadow byte indices.
# Example: SHADOW_OFFSETS = [123, 456, 789, ...]  # len == 70
SHADOW_OFFSETS: List[int] = [
    203, 204, 206, 207, 209, 210, 212, 213, 216, 219,
    437, 438, 440, 441, 443, 444, 446, 447, 450, 453,
    671, 672, 674, 675, 677, 678, 680, 681, 684, 687,
    905, 906, 908, 909, 911, 912, 914, 915, 918, 921,
    1139, 1140, 1142, 1143, 1145, 1146, 1148, 1149, 1152, 1155,
    1373, 1374, 1376, 1377, 1379, 1380, 1382, 1383, 1386, 1389,
    1607, 1608, 1610, 1611, 1613, 1614, 1616, 1617, 1620, 1623
]



# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def find_cc_files(data_dir: pathlib.Path) -> List[Tuple[int, pathlib.Path]]:
    """
    Scan DATA_DIR for files matching C1_CCxxx.syx pattern and return
    a list of (cc, path) tuples.
    """
    cc_files: List[Tuple[int, pathlib.Path]] = []
    for path in data_dir.iterdir():
        if not path.is_file():
            continue
        m = CC_FILE_REGEX.match(path.name)
        if not m:
            continue
        cc = int(m.group(1))
        cc_files.append((cc, path))
    cc_files.sort(key=lambda t: t[0])
    return cc_files


def read_shadow_bytes(path: pathlib.Path, offsets: List[int]) -> List[int]:
    """
    Read a .syx file as bytes and extract the given absolute offsets.
    """
    data = path.read_bytes()
    max_off = max(offsets)
    if max_off >= len(data):
        raise ValueError(
            f"File {path} is too short ({len(data)} bytes) "
            f"for max shadow offset {max_off}"
        )
    return [data[o] for o in offsets]


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    if not SHADOW_OFFSETS:
        raise RuntimeError(
            "SHADOW_OFFSETS is empty. Fill it with your 70 shadow byte indices."
        )

    cc_files = find_cc_files(DATA_DIR)
    if not cc_files:
        raise RuntimeError(
            f"No files matching {CC_FILE_REGEX.pattern!r} found in {DATA_DIR}"
        )

    print(f"Found {len(cc_files)} CC files in {DATA_DIR}")
    cc_seen: Dict[int, pathlib.Path] = {}
    rows: List[Tuple[int, List[int]]] = []

    for cc, path in cc_files:
        if cc in cc_seen:
            print(f"Warning: duplicate CC {cc} for files {cc_seen[cc]} and {path}")
        cc_seen[cc] = path

        shadow_vals = read_shadow_bytes(path, SHADOW_OFFSETS)
        rows.append((cc, shadow_vals))

    # Sort by CC just to be sure
    rows.sort(key=lambda t: t[0])

    # Write CSV
    header = ["cc"] + [f"o{i}" for i in range(len(SHADOW_OFFSETS))]

    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for cc, vals in rows:
            writer.writerow([cc] + vals)

    # Simple sanity info
    cc_list = [cc for cc, _ in rows]
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")
    print(f"CC range present: {min(cc_list)}..{max(cc_list)}")
    missing = [c for c in range(min(cc_list), max(cc_list) + 1) if c not in cc_seen]
    if missing:
        print(f"Warning: missing CCs: {missing}")
    else:
        print("All CCs in the observed range are present.")


if __name__ == "__main__":
    main()
