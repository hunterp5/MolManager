# Repair or install the CPU PyTorch 2.5.1 + pkasolver stack in the *active* Python (no extra venv).
# On a fresh install, `pip install -r requirements.txt` already includes these packages.
# Run this script when pKa fails due to a conflicting torch build (e.g. after installing admet-ai).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing CPU PyTorch 2.5.1 and pkasolver stack into:" (python -c "import sys; print(sys.executable)")
python -m pip install -U pip

Write-Host "`nRemoving ADMET-AI (requires torch>=2.8; conflicts with pkasolver)..."
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m pip uninstall -y admet-ai 2>&1 | Out-Host
$ErrorActionPreference = $prevEap

Write-Host "`nRemoving mismatched torch builds (e.g. 2.8.x breaks torch-sparse)..."
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m pip uninstall -y torch torchvision torchaudio 2>&1 | Out-Host
$ErrorActionPreference = $prevEap

Write-Host "`nReinstalling dependencies from requirements.txt..."
python -m pip install -r requirements.txt

Write-Host "`nVerifying imports..."
python -c @"
import torch
import torch_geometric
import torch_scatter
import pkasolver
print('OK: torch', torch.__version__, 'pyg', torch_geometric.__version__)
"@

Write-Host "`nDone. Run MolManager with this same Python (no .venvs/pka or admet-ai venv)."
