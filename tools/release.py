"""Bump the version in the VERSION file. Pushing that change to main releases it.

Usage: python tools/release.py patch|minor|major
"""
import sys
from pathlib import Path

PARTS = ("major", "minor", "patch")
path = Path(__file__).resolve().parent.parent / "VERSION"

if len(sys.argv) != 2 or sys.argv[1] not in PARTS:
    raise SystemExit(__doc__)

numbers = [int(n) for n in path.read_text(encoding="utf-8").strip().split(".")]
index = PARTS.index(sys.argv[1])
numbers[index] += 1
numbers[index + 1:] = [0] * (2 - index)
version = ".".join(str(n) for n in numbers)
path.write_text(version + "\n", encoding="utf-8", newline="\n")
print(version)
