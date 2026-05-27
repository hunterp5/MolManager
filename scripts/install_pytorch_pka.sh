#!/usr/bin/env bash
# Install CPU PyTorch 2.5.1 + PyG scatter/sparse + pkasolver into the active Python (no extra venv).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Installing CPU PyTorch 2.5.1 and pkasolver stack into: $(python -c 'import sys; print(sys.executable)')"
python -m pip install -U pip

echo
echo "Removing ADMET-AI (requires torch>=2.8; conflicts with pkasolver)..."
python -m pip uninstall -y admet-ai 2>/dev/null || true

echo
echo "Removing mismatched torch builds..."
python -m pip uninstall -y torch torchvision torchaudio 2>/dev/null || true

echo
echo "Installing pinned CPU PyTorch..."
python -m pip install -r requirements-pytorch-cpu.txt

echo
echo "Installing PyG binary extensions (adjust URL if not on CPU)..."
python -m pip install torch-scatter torch-sparse -f https://data.pyg.org/whl/torch-2.5.1+cpu.html

echo
echo "Installing pkasolver + torch-geometric..."
python -m pip install -r requirements-pka.txt

echo
echo "Verifying imports..."
python -c "import torch; import torch_geometric; import torch_scatter; import pkasolver; print('OK: torch', torch.__version__, 'pyg', torch_geometric.__version__)"

echo
echo "Done. Use this same Python for python -m molmanager."
