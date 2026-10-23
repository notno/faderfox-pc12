from pathlib import Path

ROWS = "ABCDEF"
ROW_STRIDE = 234

MAIN_TABLE_START = 27
MAIN_BASE = {
    1: 27, 2: 42, 3: 57, 4: 72, 5: 87, 6: 102,
    7: 117, 8: 132, 9: 147, 10: 162, 11: 177, 12: 192,
}

def pot_base(row_label, col):
    r = ROWS.index(row_label)
    return MAIN_TABLE_START + MAIN_BASE[col] + r * ROW_STRIDE

def diff_bytes(a, b):
    return [i for i,(x,y) in enumerate(zip(a,b)) if x != y]

def analyze(name, path):
    base = Path("C1_CC001.syx").read_bytes()
    new  = Path(path).read_bytes()
    diffs = diff_bytes(base, new)
    print(f"{name}: changed offsets = {diffs[:20]} ... total {len(diffs)} bytes")

    for row in ROWS:
        for col in range(1,13):
            base_off = pot_base(row, col)
            block = range(base_off, base_off+8)
            hits = [d for d in diffs if d in block]
            if hits:
                print(f"   overlaps pot {row}{col:02d} block @ {base_off}-{base_off+7}: {hits}")

analyze("P01A", "P01A_CC127.syx")
analyze("P12F", "P12F_cc127.syx")
