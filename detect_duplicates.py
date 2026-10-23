from pathlib import Path
import hashlib
from collections import defaultdict

def file_hash(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def find_duplicate_sysex(root: Path):
    hashes = defaultdict(list)

    for path in root.rglob("*.syx"):
        h = file_hash(path)
        hashes[h].append(path)

    # Only keep entries that have more than one file
    dupes = {h: paths for h, paths in hashes.items() if len(paths) > 1}
    return dupes

def main():
    dupes = find_duplicate_sysex(Path("."))
    for h, paths in dupes.items():
        print(f"Hash {h}:")
        for p in paths:
            print("   ", p)

if __name__ == "__main__":
    main()