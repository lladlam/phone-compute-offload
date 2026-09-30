"""Tiny explicit task: hash a file. Used by the README demo."""

import hashlib
from pathlib import Path

data = Path("input.bin").read_bytes()
Path("output.txt").write_text(hashlib.sha256(data).hexdigest() + "\n")
print(f"hashed {len(data)} bytes")
