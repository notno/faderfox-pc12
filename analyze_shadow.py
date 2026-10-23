#!/usr/bin/env python3
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Dict, Optional


# ------------------------------------------------------------
# Config
# ------------------------------------------------------------

INPUT_CSV  = Path("shadow_matrix.csv")
REPORT_TXT = Path("shadow_analysis_report.txt")
DETAIL_CSV = Path("shadow_patterns.csv")

# If your CSV does not have a header, set this to False
CSV_HAS_HEADER = True


# ------------------------------------------------------------
# Encode function for reference
# ------------------------------------------------------------

def encode_cc(cc: int) -> Optional[int]:
    """
    Main table encode_cc hypothesis, for correlation tests.
    Returns None if cc is outside the known supported ranges.
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


# ------------------------------------------------------------
# Data model
# ------------------------------------------------------------

@dataclass
class ShadowSweep:
    cc_values: List[int]          # sorted list of CC values, typically 0..127
    offset_ids: List[str]         # names of offsets, for example "o0", "o1", ...
    data: List[List[int]]         # shape [len(cc_values)][len(offset_ids)]

    def column(self, offset_index: int) -> List[int]:
        """Return the sequence of values for a single offset across CC."""
        return [row[offset_index] for row in self.data]


def load_shadow_sweep(csv_path: Path, has_header: bool = True) -> ShadowSweep:
    """
    Load a CSV where each row is:
        cc, o0, o1, ..., o69
    Returns a ShadowSweep object.
    """
    cc_values: List[int] = []
    offset_ids: List[str] = []
    data: List[List[int]] = []

    with csv_path.open("r", newline="") as f:
        reader = csv.reader(f)
        header: Optional[List[str]] = None

        if has_header:
            header = next(reader)
            if not header:
                raise ValueError("CSV header is empty")
            if header[0].lower() != "cc":
                print(f"Warning: first header column is {header[0]!r}, expected 'cc'")
            offset_ids = header[1:]
        else:
            # We will infer offset ids as o0, o1, ...
            first_row = next(reader)
            if not first_row:
                raise ValueError("CSV is empty")
            num_offsets = len(first_row) - 1
            offset_ids = [f"o{i}" for i in range(num_offsets)]
            cc_values.append(int(first_row[0]))
            data.append([int(x) for x in first_row[1:]])

        for row in reader:
            if not row:
                continue
            cc = int(row[0])
            values = [int(x) for x in row[1:]]
            if not cc_values and not has_header:
                # already handled first row above
                pass
            cc_values.append(cc)
            data.append(values)

    # Sort by CC just in case
    zipped = list(zip(cc_values, data))
    zipped.sort(key=lambda x: x[0])
    cc_values_sorted = [c for c, _ in zipped]
    data_sorted = [v for _, v in zipped]

    return ShadowSweep(cc_values=cc_values_sorted, offset_ids=offset_ids, data=data_sorted)


# ------------------------------------------------------------
# Simple pattern detectors
# ------------------------------------------------------------

def is_constant(seq: List[int]) -> Tuple[bool, Optional[int]]:
    v0 = seq[0]
    for v in seq[1:]:
        if v != v0:
            return False, None
    return True, v0


def is_monotonic(seq: List[int]) -> str:
    """
    Return "increasing", "decreasing", "flat", or "none".
    Note: uses non strict comparisons, so repeats are allowed.
    """
    inc = all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
    dec = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
    if inc and not dec:
        return "increasing"
    if dec and not inc:
        return "decreasing"
    if inc and dec:
        return "flat"
    return "none"


def detect_period(seq: List[int], max_period: int = 64) -> Optional[int]:
    """
    Look for a small period p such that seq[i] == seq[i % p] for all i.
    Returns the smallest p found, or None if no period <= max_period.
    """
    n = len(seq)
    for p in range(1, min(max_period, n) + 1):
        ok = True
        for i in range(n):
            if seq[i] != seq[i % p]:
                ok = False
                break
        if ok:
            return p
    return None


def detect_bucket_sizes(seq: List[int]) -> List[Tuple[int, int, int]]:
    """
    Find runs of identical values.
    Returns list of (start_cc_index, end_cc_index_inclusive, value).
    This is just "runs", not semantic buckets, but gives you a picture.
    """
    if not seq:
        return []

    runs = []
    start = 0
    current_val = seq[0]

    for i in range(1, len(seq)):
        if seq[i] != current_val:
            runs.append((start, i - 1, current_val))
            start = i
            current_val = seq[i]
    runs.append((start, len(seq) - 1, current_val))
    return runs


def fit_affine_mod128(xs: List[int], ys: List[int]) -> Optional[Tuple[int, int]]:
    """
    Try to find integers a, b in 0..127 such that:
        ys[i] == (a * xs[i] + b) mod 128
    for all i.
    If such a pair exists, return (a, b), else None.
    Brute force scan over a, solve for b from first point, then verify.
    """
    if not xs:
        return None

    x0, y0 = xs[0], ys[0]
    for a in range(128):
        # y0 == a * x0 + b (mod 128) => b == y0 - a*x0 (mod 128)
        b = (y0 - a * x0) % 128
        ok = True
        for x, y in zip(xs, ys):
            if (a * x + b) % 128 != y % 128:
                ok = False
                break
        if ok:
            return a, b
    return None


def unique_values(seq: List[int]) -> List[int]:
    return sorted(set(seq))


# ------------------------------------------------------------
# Bitplane analysis
# ------------------------------------------------------------

def bitplane_usage(xs: List[int], ys: List[int]) -> Dict[int, Dict[str, int]]:
    """
    For a single offset sequence ys over CC values xs, report usage stats per bit.
    Returns:
        {
          bit_index: {
            "num_set": ...,
            "num_clear": ...,
          },
          ...
        }
    """
    stats: Dict[int, Dict[str, int]] = {}
    for bit in range(7):  # sysex data bytes are 0..127
        s = 0
        c = 0
        for v in ys:
            if v & (1 << bit):
                s += 1
            else:
                c += 1
        stats[bit] = {"num_set": s, "num_clear": c}
    return stats


# ------------------------------------------------------------
# High level analysis
# ------------------------------------------------------------

def analyze_shadow(sweep: ShadowSweep) -> Tuple[str, List[Dict[str, object]]]:
    """
    Analyze each offset column and return:
      - A human readable multi line report string.
      - A list of per offset dicts for CSV export.
    """
    lines: List[str] = []
    rows_for_csv: List[Dict[str, object]] = []

    xs_cc = sweep.cc_values
    xs_enc = [encode_cc(cc) for cc in xs_cc]

    lines.append("PC12 Shadow LUT analysis")
    lines.append(f"Offsets: {len(sweep.offset_ids)}")
    lines.append(f"CC values: {len(xs_cc)} (range {min(xs_cc)}..{max(xs_cc)})")
    lines.append("")

    for idx, name in enumerate(sweep.offset_ids):
        ys = sweep.column(idx)
        const_flag, const_val = is_constant(ys)
        monot = is_monotonic(ys)
        period = detect_period(ys)
        uniq = unique_values(ys)
        num_uniq = len(uniq)

        # Affine fit vs CC and vs encode_cc
        affine_cc = fit_affine_mod128(xs_cc, ys)
        affine_enc = None
        valid_pairs = [(x, y) for x, y in zip(xs_enc, ys) if x is not None]
        if valid_pairs:
            xs_enc_valid = [x for x, _ in valid_pairs]
            ys_enc_valid = [y for _, y in valid_pairs]
            affine_enc = fit_affine_mod128(xs_enc_valid, ys_enc_valid)

        # Bitplane stats (lightweight summary)
        bit_stats = bitplane_usage(xs_cc, ys)

        lines.append(f"Offset {idx} ({name}):")
        lines.append(f"  unique values: {num_uniq}")
        if const_flag:
            lines.append(f"  pattern: constant ({const_val})")
        else:
            lines.append(f"  pattern: non constant, monotonic: {monot}")
        if period is not None and period < len(ys):
            lines.append(f"  period: {period}")
        if affine_cc is not None:
            a, b = affine_cc
            lines.append(f"  affine vs CC: y = ({a} * cc + {b}) mod 128")
        if affine_enc is not None:
            a2, b2 = affine_enc
            lines.append(f"  affine vs encode_cc(cc): y = ({a2} * enc + {b2}) mod 128")

        # Compact bitplane summary
        bit_summary_parts = []
        for bit, st in bit_stats.items():
            if st["num_set"] == 0:
                bit_summary_parts.append(f"b{bit}=always0")
            elif st["num_clear"] == 0:
                bit_summary_parts.append(f"b{bit}=always1")
            else:
                bit_summary_parts.append(
                    f"b{bit}=set{st['num_set']}/clear{st['num_clear']}"
                )
        lines.append("  bit usage: " + ", ".join(bit_summary_parts))

        # Only print some bucket info for offsets that look strongly bucketed
        if num_uniq < 16:
            runs = detect_bucket_sizes(ys)
            # convert run indices into actual CC ranges
            run_desc = []
            for start_idx, end_idx, val in runs:
                cc_start = xs_cc[start_idx]
                cc_end = xs_cc[end_idx]
                run_desc.append(f"{cc_start}-{cc_end}:{val}")
            lines.append("  runs (cc_start-cc_end:value): " + "; ".join(run_desc))

        lines.append("")

        # CSV row
        row = {
            "offset_index": idx,
            "offset_name": name,
            "unique_values": num_uniq,
            "constant": const_flag,
            "constant_value": const_val if const_flag else "",
            "monotonic": monot,
            "period": period if period is not None else "",
            "affine_cc": f"{affine_cc[0]}*cc+{affine_cc[1]}" if affine_cc else "",
            "affine_encode_cc": (
                f"{affine_enc[0]}*enc+{affine_enc[1]}" if affine_enc else ""
            ),
        }
        # Add bit stats in a compact way
        for bit, st in bit_stats.items():
            row[f"bit{bit}_num_set"] = st["num_set"]
            row[f"bit{bit}_num_clear"] = st["num_clear"]
        rows_for_csv.append(row)

    report = "\n".join(lines)
    return report, rows_for_csv


def write_csv_rows(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    sweep = load_shadow_sweep(INPUT_CSV, has_header=CSV_HAS_HEADER)
    report, rows = analyze_shadow(sweep)

    REPORT_TXT.write_text(report, encoding="utf8")
    write_csv_rows(DETAIL_CSV, rows)

    print(f"Wrote report to {REPORT_TXT}")
    print(f"Wrote detailed CSV to {DETAIL_CSV}")


if __name__ == "__main__":
    main()
