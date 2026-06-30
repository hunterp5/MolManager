# Packaging molmanager for distribution

This document supports building an installer (PyInstaller, Inno Setup, MSI, etc.) so users can run molmanager without managing Python manually.

## What is included

| Component | How it ships |
|-----------|----------------|
| molmanager app (`molmanager` package) | PyInstaller one-folder/one-file, or `pip install -e .` |
| Python dependencies | `pip install -e .` (or `requirements.txt` + `pip install -e .`) |
| 3Dmol.js | Already in `molmanager/ui/static/` |
| AutoDock Vina | **Optional** binary in `molmanager/resources/bin/<platform>/` (not redistributed in git) |
| Boltz-2 CLI | **`pip install boltz`** (or bundled copy in `resources/bin/`) |

## Recommended install commands (source / CI)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -U pip
pip install -e .
```

Optional pKa stack: install into the **same** venv as MolManager (no `.venvs/pka`):

```bash
# Windows
scripts\install_pytorch_pka.ps1
# macOS / Linux
bash scripts/install_pytorch_pka.sh
pip install -e ".[pka,boltz]"
```

Editable install with extras (install PyTorch via the script above before `[pka]`):

```bash
pip install -e ".[dev]"
pip install -e ".[pka]"
pip install -e ".[boltz]"
pip install -e ".[permeability]"   # after PyTorch 2.5.1 + pKa script
```

## Bundling external tools

1. Run `scripts\bootstrap_optional_tools.ps1` (Windows) or `scripts/bootstrap_optional_tools.sh` (Linux/macOS) to install Python extras and print where to place Vina/Boltz binaries.
2. Copy `vina.exe` / `boltz.exe` into `molmanager/resources/bin/win/` (or set `MOLMANAGER_BUNDLE_DIR`).
3. Tools → Dock (Vina) and Tools → Boltz-2 default to bundled paths when present.

## PyInstaller (starter)

A minimal spec lives in `packaging/molmanager.spec`. Build (from repo root, venv activated):

```bash
pip install pyinstaller
pyinstaller packaging/molmanager.spec
```

Output under `dist/molmanager/`. You still need to ship:

- Qt platform plugins (PyInstaller usually collects these)
- Optional `resources/bin/` for Vina
- Boltz models/cache if users run Boltz offline (see Boltz docs)

Tune `packaging/molmanager.spec` hidden imports as you enable more Tools menu features.

## Version

Application version: `molmanager.__version__` (shown in About when wired).

## Performance release gate (100k rows)

Run before tagging a production build:

```bash
python scripts/benchmark_large_table.py --runs 3 --scales 10000,50000,100000
python scripts/perf_gate.py
pytest tests/test_compound_table_model_batch.py tests/test_sqlite_table_store.py tests/test_substructure_filter_worker.py -q
```

Target SLA guidance for enterprise-ready builds on a typical developer workstation:

- 100k row batch ingest to model: p95 under 5s
- Numeric bounds scan on 100k rows: p95 under 3s
- In-memory sort on 100k rows: p95 under 2s
- Substructure filter should use prebuilt mol targets (no per-row reparsing in worker)

If any metric regresses by more than 20% against your previous release baseline, block release and investigate.
