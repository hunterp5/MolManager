#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Installing core Python packages..."
python -m pip install -U pip
python -m pip install -r requirements.txt

if [[ -f requirements-pka.txt ]]; then
  read -r -p "Install pKa stack (requirements-pka.txt)? [y/N] " pka
  if [[ "$pka" =~ ^[yY] ]]; then
    python -m pip install -r requirements-pka.txt
  fi
fi

if [[ -f requirements-boltz.txt ]]; then
  read -r -p "Install Boltz (requirements-boltz.txt)? [y/N] " boltz
  if [[ "$boltz" =~ ^[yY] ]]; then
    python -m pip install -r requirements-boltz.txt
  fi
fi

if [[ -f requirements-permeability.txt ]]; then
  read -r -p "Install permeability predictor (Chemprop / GNN-MTL)? [y/N] " perm
  if [[ "$perm" =~ ^[yY] ]]; then
    python scripts/bootstrap_gnn_mtl_model.py
    python -m pip install -r requirements-permeability.txt
  fi
fi

PLAT=linux
[[ "$(uname -s)" == "Darwin" ]] && PLAT=mac
BINDIR="$ROOT/molmanager/resources/bin/$PLAT"
echo ""
echo "Optional executables (copy into): $BINDIR"
echo "  vina   - https://vina.scripps.edu"
echo "  boltz  - from your venv bin after: pip install boltz"
echo ""
echo "Or set MOLMANAGER_BUNDLE_DIR. Run: python -m molmanager"
