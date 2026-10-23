#!/usr/bin/env python3
import argparse
from pathlib import Path

ROW_MAP = {
    "A": 0,
    "B": 1,
    "C": 2,
    "D": 3,
    "E": 4,
    "F": 5,
}

ROW_STRIDE = 234   # bytes between rows
COL_STRIDE = 15    # bytes between columns
IDENTITY_BASE = 27 # identity offset for P01A (col 1, row A)


def compute_identity_offset(row: str, column: int) -> int:
    row = row.upper()
    if row not in ROW_MAP:
        raise ValueError(f"Row must be A–F, got {row!r}")
    if not (1 <= column <= 12):
        raise ValueError(f"Column must be 1–12, got {column}")

    row_index = ROW_MAP[row]
    col_index = column - 1

    offset = IDENTITY_BASE + (col_index * COL_STRIDE) + (row_index * ROW_STRIDE)
    return offset


def change_pot_cc(input_path: Path, output_path: Path, row: str, column: int, cc_value: int):
    if not (0 <= cc_value <= 127):
        raise ValueError(f"CC value must be 0–127, got {cc_value}")

    data = bytearray(input_path.read_bytes())

    offset = compute_identity_offset(row, column)

    if offset >= len(data):
        raise RuntimeError(
            f"Computed offset {offset} is outside the file (length {len(data)}). "
            "This likely means the layout assumptions do not match this sysex."
        )

    old_cc = data[offset]
    print(f"Target pot: P{column:02d}{row.upper()}")
    print(f"Identity offset: {offset}")
    print(f"Old CC value at that byte: {old_cc}")
    print(f"New CC value: {cc_value}")

    data[offset] = cc_value

    output_path.write_bytes(data)
    print(f"Wrote modified sysex to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Change CC value for a specific pot (row/column) in a PC-12 sysex dump."
    )
    parser.add_argument("input_syx", type=Path, help="Input .syx file (baseline dump)")
    parser.add_argument("output_syx", type=Path, help="Output .syx file (modified)")
    parser.add_argument("--row", "-r", required=True, help="Row letter A–F (e.g. C)")
    parser.add_argument("--col", "-c", required=True, type=int, help="Column 1–12 (e.g. 4)")
    parser.add_argument("--cc", required=True, type=int, help="New CC value 0–127")

    args = parser.parse_args()
    change_pot_cc(args.input_syx, args.output_syx, args.row, args.col, args.cc)


if __name__ == "__main__":
    main()
