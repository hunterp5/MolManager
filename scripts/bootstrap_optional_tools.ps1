# Install molmanager Python dependencies and show where to place optional CLI binaries.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing Python packages from requirements.txt..."
python -m pip install -U pip
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m pip install -e .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "scripts\install_pytorch_pka.ps1") {
    Write-Host ""
    Write-Host "If pKa prediction fails (torch version conflict), run:"
    Write-Host "  scripts\install_pytorch_pka.ps1"
}

$perm = Read-Host "Download GNN-MTL permeability model weights? [y/N]"
if ($perm -match '^[yY]') {
    python scripts/bootstrap_gnn_mtl_model.py
}

$boltz = Read-Host "Install Boltz Python package? [y/N]"
if ($boltz -match '^[yY]') {
    python -m pip install "boltz>=2.0.0"
}

$binDir = Join-Path $Root "molmanager\resources\bin\win"
Write-Host ""
Write-Host "Optional executables (copy into):"
Write-Host "  $binDir"
Write-Host "    vina.exe / smina.exe  - from https://vina.scripps.edu"
Write-Host ""
Write-Host "Or set MOLMANAGER_BUNDLE_DIR to a folder containing those binaries."
Write-Host "Run: python -m molmanager"
