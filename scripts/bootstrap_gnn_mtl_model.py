#!/usr/bin/env python3
"""Download the GNN-MTL Chemprop model (Zenodo 10.5281/zenodo.16948542) into resources."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "molmanager" / "resources" / "models" / "gnn_mtl" / "model.pt"
URL = "https://zenodo.org/api/records/16948542/files/model.pt/content"


def main() -> int:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.is_file() and DEST.stat().st_size > 0:
        print(f"Already present: {DEST} ({DEST.stat().st_size} bytes)")
        return 0
    print(f"Downloading GNN-MTL model to {DEST} …")
    urllib.request.urlretrieve(URL, DEST)
    print(f"Done ({DEST.stat().st_size} bytes).")
    print("Install Chemprop stack: pip install -r requirements-permeability.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
