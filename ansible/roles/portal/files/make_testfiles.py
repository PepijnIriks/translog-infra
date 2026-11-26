#!/usr/bin/env python3
"""Fills the TransLog fileshare with made-up documents (about 2 000 files, ~150 MB): delivery notes, invoices
and scans per customer. Used as realistic test data for back-ups and for the attack test script."""
import os
import random
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/srv/fileshare")
random.seed(42)
folders = ["Vrachtbrieven", "Facturen", "Scans", "Contracten", "Planning"]
count = 0
for klant in range(1, 101):
    for folder in folders:
        d = root / folder / f"klant-{klant:03d}"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(4):
            if folder == "Scans":
                (d / f"scan-{i:03d}.bin").write_bytes(os.urandom(random.randint(40_000, 160_000)))
            else:
                lines = [f"TransLog B.V. - {folder[:-1].lower()} {klant:03d}-{i:03d}",
                         f"Klant: Klant {klant}", f"Bedrag/gewicht: {random.randint(10, 9000)}"]
                lines += [f"Regel {r}: zending TL{random.randint(1, 20000):08d}" for r in range(random.randint(20, 400))]
                (d / f"{folder[:-1].lower()}-{i:03d}.txt").write_text("\n".join(lines) + "\n")
            count += 1
(root / ".testdata").write_text(f"{count} test files made by make_testfiles.py\n")
os.system(f"chown -R translog:translog {root}")
print(f"{count} files in {root}")
