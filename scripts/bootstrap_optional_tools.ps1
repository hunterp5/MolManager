# Install molmanager Python dependencies and show where to place optional CLI binaries.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing core + optional Python packages..."
python -m pip install -U pip
python -m pip install -e .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "scripts\install_pytorch_pka.ps1") {
    Write-Host ""
    Write-Host "pKa uses CPU PyTorch 2.5.1 in the *same* Python as MolManager (no .venvs/pka)."
    $pka = Read-Host "Install PyTorch + pkasolver now? [y/N]"
    if ($pka -match '^[yY]') {
        & "$PSScriptRoot\install_pytorch_pka.ps1"
    }
}

if (Test-Path "requirements-boltz.txt") {
    $boltz = Read-Host "Install Boltz (boltz package)? [y/N]"
    if ($boltz -match '^[yY]') {
        python -m pip install -r requirements-boltz.txt
    }
}

if (Test-Path "requirements-permeability.txt") {
    $perm = Read-Host "Install permeability predictor (Chemprop / GNN-MTL)? [y/N]"
    if ($perm -match '^[yY]') {
        python scripts/bootstrap_gnn_mtl_model.py
        python -m pip install -r requirements-permeability.txt
    }
}

$binDir = Join-Path $Root "molmanager\resources\bin\win"
Write-Host ""
Write-Host "Optional executables (copy into):"
Write-Host "  $binDir"
Write-Host "    vina.exe   - from https://vina.scripps.edu"
Write-Host "    boltz.exe  - from your Python Scripts after: pip install boltz"
Write-Host ""
Write-Host "Or set MOLMANAGER_BUNDLE_DIR to a folder containing those binaries."
Write-Host "Run: python -m molmanager   (or: molmanager)"
