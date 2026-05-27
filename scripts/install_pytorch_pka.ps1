# Install CPU PyTorch 2.5.1 + PyG scatter/sparse + pkasolver into the *active* Python (no extra venv).
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

Write-Host "`nInstalling pinned CPU PyTorch..."
python -m pip install -r requirements-pytorch-cpu.txt

Write-Host "`nInstalling PyG binary extensions for torch 2.5.1+cpu..."
python -m pip install torch-scatter torch-sparse -f https://data.pyg.org/whl/torch-2.5.1+cpu.html

Write-Host "`nInstalling pkasolver + torch-geometric..."
python -m pip install -r requirements-pka.txt

Write-Host "`nVerifying imports..."
python -c @"
import torch
import torch_geometric
import torch_scatter
import pkasolver
print('OK: torch', torch.__version__, 'pyg', torch_geometric.__version__)
"@

Write-Host "`nDone. Run MolManager with this same Python (no .venvs/pka or admet-ai venv)."
