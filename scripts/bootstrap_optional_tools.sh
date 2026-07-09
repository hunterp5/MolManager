#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Installing Python packages from requirements.txt..."
python -m pip install -U pip
python -m pip install -r requirements.txt
python -m pip install -e .

if [[ -f scripts/install_pytorch_pka.sh ]]; then
  echo ""
  echo "If pKa prediction fails (torch version conflict), run:"
  echo "  bash scripts/install_pytorch_pka.sh"
fi

read -r -p "Download GNN-MTL permeability model weights? [y/N] " perm
if [[ "$perm" =~ ^[yY] ]]; then
  python scripts/bootstrap_gnn_mtl_model.py
fi

read -r -p "Install Boltz Python package? [y/N] " boltz
if [[ "$boltz" =~ ^[yY] ]]; then
  python -m pip install "boltz>=2.0.0"
fi

PLAT=linux
[[ "$(uname -s)" == "Darwin" ]] && PLAT=mac
BINDIR="$ROOT/molmanager/resources/bin/$PLAT"
echo ""
echo "Optional executables (copy into): $BINDIR"
echo "  vina / smina  - https://vina.scripps.edu"
echo ""
echo "Or set MOLMANAGER_BUNDLE_DIR. Run: python -m molmanager"
