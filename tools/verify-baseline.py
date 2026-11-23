#!/usr/bin/env python3
"""Check that the repo files of the measured v1 pack are still byte-identical to the baseline.

    python3 tools/verify-baseline.py          # exit 0: all identical; exit 1: something changed

docs/baseline/v1-SHA256SUMS.txt lists the 15 files of the v1 pack by their path INSIDE the pack
(the same layout as ~/.pi/agent). In this repo four of them live elsewhere:

    AGENTS.md, settings.json, presets.json  ->  config/
    install.sh                              ->  tools/legacy/
    README.md                               ->  docs/baseline/v1-README.md

A difference is not an error by itself: v1.1+ changes some of these files on purpose (for example
extensions/preset.ts). It only means "this change must be declared in CHANGELOG.md".
Stdlib only, read-only, no network.
"""
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUMS = REPO / "docs" / "baseline" / "v1-SHA256SUMS.txt"
WHERE = {
    "AGENTS.md": "config/AGENTS.md",
    "settings.json": "config/settings.json",
    "presets.json": "config/presets.json",
    "install.sh": "tools/legacy/install.sh",
    "README.md": "docs/baseline/v1-README.md",
}


def main() -> int:
    changed = 0
    for line in SUMS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        want, _, rel = line.partition("  ")
        rel = rel.strip()
        rel = rel[2:] if rel.startswith("./") else rel
        path = REPO / WHERE.get(rel, rel)
        if not path.is_file():
            print("MISSING  %s  (expected at %s)" % (rel, path.relative_to(REPO)))
            changed += 1
            continue
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got == want:
            print("same     %s" % rel)
        else:
            print("CHANGED  %s  (now %s..., baseline %s...)" % (rel, got[:12], want[:12]))
            changed += 1
    print("%d of the baseline files differ from v1.0.0" % changed if changed else "all baseline files are byte-identical to v1.0.0")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
