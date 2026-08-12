#!/usr/bin/env bash
# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

# Repair or install the CPU PyTorch 2.5.1 + pkasolver stack in the active Python (no extra venv).
# On a fresh install, `pip install -r requirements.txt` already includes these packages.
# Run this script when pKa fails due to a conflicting torch build (e.g. after installing admet-ai).
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
echo "Reinstalling dependencies from requirements.txt..."
python -m pip install -r requirements.txt

echo
echo "Verifying imports..."
python -c "import torch; import torch_geometric; import torch_scatter; import pkasolver; print('OK: torch', torch.__version__, 'pyg', torch_geometric.__version__)"

echo
echo "Done. Use this same Python for python -m molmanager."
