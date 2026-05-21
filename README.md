## MolManager

Small desktop chemistry manager built with **PyQt5** + **RDKit**.

### Install (Windows)

Create/activate a virtualenv, then either **core only** or **full stack** (pKa + Boltz Python packages):

```bash
pip install -r requirements.txt
# optional full Python stack (see requirements-pka.txt for Windows PyTorch notes first):
pip install -r requirements-all.txt
```

Or editable install: `pip install -e ".[dev]"` (see `pyproject.toml` for extras `pka`, `boltz`, `permeability`).

**Optional CLI tools** (AutoDock Vina, Boltz predict binary): not stored in git. Copy into
`molmanager/resources/bin/win/` (`vina.exe`, `boltz.exe`) or run
`scripts\bootstrap_optional_tools.ps1` for guided setup. See `docs/PACKAGING.md` for installer builds.

### Install (macOS)

Requires **Python 3.10+** (3.11 recommended). From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
# optional full stack (pKa / Boltz — see requirements-pka.txt for PyTorch on Apple Silicon):
# pip install -r requirements-all.txt
```

Or editable install: `pip install -e ".[dev]"` (see `pyproject.toml` for extras `pka`, `boltz`, `permeability`).

**Optional CLI tools:** copy macOS binaries into `molmanager/resources/bin/mac/` (`vina`, `boltz`) or run
`scripts/bootstrap_optional_tools.sh` for guided setup. Boltz is often available on `PATH` after
`pip install boltz` inside the same venv.

If **RDKit** or **PyQtWebEngine** fail via pip, use **conda-forge** for those packages, then pip-install the rest:

```bash
conda create -n molmanager python=3.11
conda activate molmanager
conda install -c conda-forge rdkit pyqt
pip install -r requirements.txt
```

**Apple Silicon:** core MolManager runs natively. Optional **pKa** (`pkasolver` + PyTorch) may need a
CPU/MPS-compatible PyTorch build from [pytorch.org](https://pytorch.org) before `requirements-pka.txt`.

### Install notes (all platforms)

- **RDKit** can be finicky via pip on some setups. If `rdkit-pypi` fails, use **conda** instead:
  - `conda install -c conda-forge rdkit`
- **NumPy 2.x** is not compatible with current `rdkit-pypi` wheels (you may see `_ARRAY_API` / import errors). This project pins **`numpy<2`** in `requirements.txt`; reinstall deps after pulling updates (`pip install -r requirements.txt`).
- **Linux:** same venv flow as macOS; optional tools go in `molmanager/resources/bin/linux/`; use `scripts/bootstrap_optional_tools.sh`.

### Run

```bash
python -m molmanager
```

Optional environment variables:

| Variable | Purpose |
|----------|---------|
| `MOLMANAGER_LOG_LEVEL` | Console log level: `DEBUG`, `INFO`, `WARNING`, … (default `INFO`). |
| `MOLMANAGER_MAX_THREADPOOL` | Main `QThreadPool` max threads (`1`–`64`). If unset, scales with CPU count (capped at `16`). |
| `MOLMANAGER_RENDER_THREADPOOL` | Dedicated 2D render pool max threads (`1`–`32`). If unset, uses `min(main_cap, 8)` with a floor of `2`. |
| `MOLMANAGER_SUBSTRUCTURE_ASYNC_ROWS` | Row count at which substructure filtering uses a worker thread (default `400`, clamped `64`–`500000`). |
| `MOLMANAGER_FILTER_DEBOUNCE_SUBSTRUCTURE_ROWS` / `MOLMANAGER_FILTER_DEBOUNCE_SUBSTRUCTURE_MS` | Row threshold and debounce (ms) when a substructure filter is active (defaults `120` / `85`). |
| `MOLMANAGER_FILTER_DEBOUNCE_DEFAULT_ROWS` / `MOLMANAGER_FILTER_DEBOUNCE_DEFAULT_MS` | Same when no substructure filter (defaults `400` / `55`). |
| `MOLMANAGER_PERF_METRICS` | Enable runtime perf metric aggregation/logging for load/filter/search/export hot paths. |
| `MOLMANAGER_PERF_LOG_EVERY` | Log interval for perf metrics (default `25` samples). |
| `MOLMANAGER_CONFORMER_THREADS` | Parallel workers for conformer generation (`1`–`16`; unset = auto). |
| `MOLMANAGER_DESCRIPTOR_THREADS` | Parallel workers for descriptor calculation (`1`–`32`; unset = auto). |
| `MOLMANAGER_PROTOmer_PROCESSES` | Parallel **processes** for Tools → Generate Protomers (`1`–`8`). `1` = always sequential (one shared model). Unset = auto: **dedupe** identical structures, then use a small process pool when there are **≥4 unique** structures (each process loads its own pkasolver model — faster but more RAM). |
| `MOLMANAGER_SQL_MAX_ROWS_HARD` | Hard ceiling for “Max rows” when loading from SQL (default `2000000`; caps UI spinbox and server `LIMIT`). |
| `MOLMANAGER_SQL_PRECOUNT_WARN` | If a pre-load `COUNT(*)` is ≥ this value, confirm before fetching (default `100000`). |
| `MOLMANAGER_SQLITE_TIMEOUT_S` | SQLite `connect_args["timeout"]` seconds (default `30`, clamped when applied). |
| `MOLMANAGER_PG_CONNECT_TIMEOUT` | Postgres `connect_timeout` seconds (default `30`). |
| `MOLMANAGER_SQLITE_BACKEND_PAGE_SIZE` | Page size for the local SQLite row cache used by text filters and column search (default `5000`). |
| `MOLMANAGER_DISABLE_CUSTOM_CALC` | Set to `1`, `true`, `yes`, or `on` to disable Tools → Custom Calculator (policy lockdown). |
| `MOLMANAGER_CUSTOM_CALC_LEGACY_EVAL` | Set to `1`, `true`, `yes`, or `on` to use legacy restricted `eval` instead of the default AST evaluator. |
| `MOLMANAGER_BUNDLE_DIR` | Directory containing optional bundled executables (`vina`, `boltz`) when not using `molmanager/resources/bin/<platform>/`. |

**Custom calculator:** by default expressions are evaluated with a restricted **AST** interpreter (`+ - * / // % **`, unary `+`/`-`, parentheses, and `math.*` callables in scope). Treat expressions as **trusted input only** (not a full sandbox). Use `MOLMANAGER_DISABLE_CUSTOM_CALC` where policy requires; use `MOLMANAGER_CUSTOM_CALC_LEGACY_EVAL` only if you hit an expression compatibility edge case.

**SQL URLs in logs:** at `DEBUG`, `load_from_sql` logs the connection URL with credentials redacted via `redact_sqlalchemy_url()` (best-effort).

### Running tests (development)

```bash
pip install -r requirements.txt -r requirements-dev.txt
set QT_QPA_PLATFORM=offscreen
python -m pytest tests/ -v
```

On Linux/macOS, use `export QT_QPA_PLATFORM=offscreen`. CI runs the test suite on **Ubuntu and macOS** for pushes and pull requests to `main` and `dev` (see `.github/workflows/ci.yml`).

Performance baseline benchmark:

```bash
python scripts/benchmark_large_table.py --runs 3 --scales 10000,50000,100000
```

### Project structure

- `molmanager/app.py`: application entry point
- `molmanager/ui/main_window/`: main window package (`ChemicalTableApp` composes session, table UI, ingest/export, and chemistry mixins)
- `molmanager/ui/sketcher/widget_painting.py`: sketch canvas 2D rendering (mixed into `SketchWidget`)
- `molmanager/ui/widgets.py`: small reusable widgets
- `molmanager/ui/dialogs/`: tool dialogs (package; import from `molmanager.ui.dialogs` as before)
- `molmanager/workers.py`: background workers (load/render/calc/export)
- `physical_property_calculator.py`: original prototype (kept for compatibility during refactor)

### Stereochemistry and isomerism

MolManager (RDKit + the 2D sketcher) handles **tetrahedral** stereo (wedge/hash → R/S where CIP applies) and **alkene E/Z** inference from 2D layout (see `docs/STEREO_AND_ISOMERISM.md`). It does **not** automatically enumerate tautomers or atropisomers; each row/sketch is one explicit structure. Read that doc before changing stereo-related code.

**Valence, bond order, aromaticity, atom types:** the sketcher stores bond **order** 1–3 (single/double/triple), sums orders for local valence warnings, maps loaded **aromatic** RDKit bonds to order 1, and exports Kekulé-style bonds to RDKit—see `docs/VALENCE_BONDS_AND_AROMATICITY.md`.

