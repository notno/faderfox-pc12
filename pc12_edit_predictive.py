#!/usr/bin/env python3
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ------------------------------------------------------------
# Configuration - YOU MUST FILL THESE
# ------------------------------------------------------------

# Baseline sysex that you know is valid
BASE_SYX = Path("C1_CC001.syx")

# Sweep results from build_shadow_matrix.py
# Expected format:
#   cc,o0,o1,...,o69
SHADOW_MATRIX_CSV = Path("shadow_matrix.csv")

# Where to write the edited file
OUTPUT_SYX = Path("C1_CC001_P10C_CC127.syx")

# Absolute offsets of the 70 shadow bytes in the .syx file,
# in the same order as the o0..o69 columns in SHADOW_MATRIX_CSV.
# You already have this as SHADOW_OFFSETS from your previous work.
SHADOW_OFFSETS: List[int] = [
    203, 204, 206, 207, 209, 210, 212, 213, 216, 219,
    437, 438, 440, 441, 443, 444, 446, 447, 450, 453,
    671, 672, 674, 675, 677, 678, 680, 681, 684, 687,
    905, 906, 908, 909, 911, 912, 914, 915, 918, 921,
    1139, 1140, 1142, 1143, 1145, 1146, 1148, 1149, 1152, 1155,
    1373, 1374, 1376, 1377, 1379, 1380, 1382, 1383, 1386, 1389,
    1607, 1608, 1610, 1611, 1613, 1614, 1616, 1617, 1620, 1623
]

# Main table layout
# Row stride is confirmed as 0xEA from your research.
ROW_STRIDE = 0xEA

# The absolute offset in the file where the main pot table starts.
# You can determine this from your earlier mapping or by logging from your tools.
MAIN_TABLE_START: int = 26  # TODO: set this

# Base offset per column *within* a row, relative to MAIN_TABLE_START.
# You already inferred MAIN_BASE(col) for columns 1..12.
# Fill this with actual integers.
MAIN_BASE: Dict[int, int] = {
    1: 27,
    2: 42,
    3: 57,
    4: 72,
    5: 87,
    6: 102,
    7: 117,
    8: 132,
    9: 147,
    10: 162,
    11: 177,
    12: 192,
}


# Optional: LUT for main CC encoding, if you have swept that as well.
# If provided, this overrides encode_cc() for any cc present in the LUT.
MAIN_CC_LUT_CSV: Optional[Path] = None  # or Path("main_cc_lut.csv")


# ------------------------------------------------------------
# Data models
# ------------------------------------------------------------

@dataclass
class ShadowLUT:
    """Shadow LUT from your sweep - 70 bytes per CC."""
    data: Dict[int, List[int]]  # cc -> [o0..o69]

    @classmethod
    def from_csv(cls, path: Path) -> "ShadowLUT":
        data: Dict[int, List[int]] = {}
        with path.open("r", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            if not header or header[0].lower() != "cc":
                raise ValueError("First column in shadow_matrix.csv must be 'cc'")

            num_cols = len(header) - 1
            for row in reader:
                if not row:
                    continue
                cc = int(row[0])
                vals = [int(x) for x in row[1:]]
                if len(vals) != num_cols:
                    raise ValueError(
                        f"Row for cc={cc} has {len(vals)} values, expected {num_cols}"
                    )
                data[cc] = vals
        return cls(data=data)


@dataclass
class MainCCLUT:
    """Optional LUT for main CC byte, if you swept it."""
    data: Dict[int, int]  # cc -> encoded_byte

    @classmethod
    def from_csv(cls, path: Path) -> "MainCCLUT":
        data: Dict[int, int] = {}
        with path.open("r", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            # expect something like: cc,encoded
            if len(header) < 2 or header[0].lower() != "cc":
                raise ValueError("Main CC LUT CSV must have 'cc' as first column")
            for row in reader:
                if not row:
                    continue
                cc = int(row[0])
                enc = int(row[1])
                data[cc] = enc
        return cls(data=data)


# ------------------------------------------------------------
# Encoding logic
# ------------------------------------------------------------

def encode_cc_theory(cc: int) -> Optional[int]:
    """
    Theoretical encode_cc from your earlier work.
    This is experimentally confirmed for cc in 1..72.
    Outside that range it returns None by default.
    """
    if 1 <= cc <= 24:
        return cc + 16
    if 25 <= cc <= 36:
        return cc
    if 37 <= cc <= 48:
        return cc - 16
    if 49 <= cc <= 72:
        return cc - 32
    return None


def encode_cc(cc: int, main_lut: Optional[MainCCLUT] = None) -> int:
    """
    Encode CC for the main table.
    Priority:
      1) If a measured LUT is provided and has cc, use that.
      2) Otherwise, use the theoretical encode_cc for 1..72.
      3) Otherwise, fall back to identity (cc) but print a warning.
    """
    if main_lut is not None and cc in main_lut.data:
        return main_lut.data[cc]

    enc = encode_cc_theory(cc)
    if enc is not None:
        return enc

    # Fallback - you can change this if you prefer to hard fail.
    print(f"Warning: encode_cc not known for CC {cc}, using identity as fallback.")
    return cc & 0x7F


# ------------------------------------------------------------
# Pot addressing
# ------------------------------------------------------------

ROW_LABELS = ["A", "B", "C", "D", "E", "F"]
ROW_TO_INDEX = {label: i for i, label in enumerate(ROW_LABELS)}


def pot_offset(row_label: str, col_index: int) -> int:
    """
    Compute the absolute file offset of the main CC byte for a given pot.
    row_label: 'A'..'F'
    col_index: 1..12
    """
    row_label = row_label.upper()
    if row_label not in ROW_TO_INDEX:
        raise ValueError(f"Invalid row label: {row_label}. Expected A..F")
    if col_index not in MAIN_BASE:
        raise ValueError(f"MAIN_BASE has no entry for column {col_index}")

    r = ROW_TO_INDEX[row_label]
    base_in_row = MAIN_BASE[col_index]
    return MAIN_TABLE_START + base_in_row + r * ROW_STRIDE


# ------------------------------------------------------------
# High level edit function
# ------------------------------------------------------------

def edit_pot_cc(
        syx_bytes: bytearray,
        row_label: str,
        col_index: int,
        new_cc: int,
        shadow_lut: ShadowLUT,
        main_lut: Optional[MainCCLUT] = None,
) -> None:
    """
    In-place edit of:
      - main CC byte for a given pot (row_label, col_index)
      - all 70 shadow bytes based on measured LUT for new_cc
    """
    if new_cc not in shadow_lut.data:
        raise ValueError(f"Shadow LUT has no entry for CC {new_cc}")

    # 1) Update main CC byte
    main_off = pot_offset(row_label, col_index)
    enc = encode_cc(new_cc, main_lut=main_lut)
    print(f"Setting pot {row_label}{col_index:02d} main CC byte at {main_off} to {enc}")
    syx_bytes[main_off] = enc & 0x7F  # sysex data byte

    # 2) Update shadow bytes
    shadow_vals = shadow_lut.data[new_cc]
    if len(shadow_vals) != len(SHADOW_OFFSETS):
        raise ValueError(
            f"Shadow LUT row has {len(shadow_vals)} values, "
            f"but SHADOW_OFFSETS has {len(SHADOW_OFFSETS)}"
        )

    for i, file_off in enumerate(SHADOW_OFFSETS):
        old_val = syx_bytes[file_off]
        new_val = shadow_vals[i] & 0x7F
        if old_val != new_val:
            print(f"  Shadow o{i} at {file_off}: {old_val:02X} -> {new_val:02X}")
        syx_bytes[file_off] = new_val


# ------------------------------------------------------------
# Main script - example: set P10C to CC 127
# ------------------------------------------------------------

def main() -> None:
    # Load baseline syx
    base_bytes = bytearray(BASE_SYX.read_bytes())

    # Load shadow LUT
    shadow_lut = ShadowLUT.from_csv(SHADOW_MATRIX_CSV)

    # Optional main CC LUT
    main_lut = None
    if MAIN_CC_LUT_CSV is not None:
        main_lut = MainCCLUT.from_csv(MAIN_CC_LUT_CSV)

    # Example: P10C - row C, column 10 - set to CC 127
    target_row = "C"
    target_col = 10
    target_cc = 127

    edit_pot_cc(
        base_bytes,
        row_label=target_row,
        col_index=target_col,
        new_cc=target_cc,
        shadow_lut=shadow_lut,
        main_lut=main_lut,
    )

    # Write output
    OUTPUT_SYX.write_bytes(base_bytes)
    print(f"Wrote edited sysex to {OUTPUT_SYX}")


if __name__ == "__main__":
    main()
