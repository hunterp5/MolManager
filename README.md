## ChemManager

Small desktop chemistry manager built with **PyQt5** + **RDKit**.

### Install (Windows)

Create/activate a virtualenv, then:

```bash
pip install -r requirements.txt
```

Notes:
- **RDKit** can be finicky on Windows via pip. If `rdkit-pypi` fails to install, use **conda** instead:
  - `conda install -c conda-forge rdkit`
- **NumPy 2.x** is not compatible with current `rdkit-pypi` wheels (you may see `_ARRAY_API` / import errors). This project pins **`numpy<2`** in `requirements.txt`; reinstall deps after pulling updates (`pip install -r requirements.txt`).

### Run

```bash
python -m chemmanager
```

Optional environment variables:

| Variable | Purpose |
|----------|---------|
| `CHEMMANAGER_LOG_LEVEL` | Console log level: `DEBUG`, `INFO`, `WARNING`, … (default `INFO`). |
| `CHEMMANAGER_MAX_THREADPOOL` | Main `QThreadPool` max threads (`1`–`64`). If unset, scales with CPU count (capped at `16`). |
| `CHEMMANAGER_RENDER_THREADPOOL` | Dedicated 2D render pool max threads (`1`–`32`). If unset, uses `min(main_cap, 8)` with a floor of `2`. |
| `CHEMMANAGER_SUBSTRUCTURE_ASYNC_ROWS` | Row count at which substructure filtering uses a worker thread (default `400`, clamped `64`–`500000`). |
| `CHEMMANAGER_FILTER_DEBOUNCE_SUBSTRUCTURE_ROWS` / `CHEMMANAGER_FILTER_DEBOUNCE_SUBSTRUCTURE_MS` | Row threshold and debounce (ms) when a substructure filter is active (defaults `120` / `85`). |
| `CHEMMANAGER_FILTER_DEBOUNCE_DEFAULT_ROWS` / `CHEMMANAGER_FILTER_DEBOUNCE_DEFAULT_MS` | Same when no substructure filter (defaults `400` / `55`). |
| `CHEMMANAGER_CONFORMER_THREADS` | Parallel workers for conformer generation (`1`–`16`; unset = auto). |
| `CHEMMANAGER_DESCRIPTOR_THREADS` | Parallel workers for descriptor calculation (`1`–`32`; unset = auto). |
| `CHEMMANAGER_SQL_MAX_ROWS_HARD` | Hard ceiling for “Max rows” when loading from SQL (default `2000000`; caps UI spinbox and server `LIMIT`). |
| `CHEMMANAGER_SQL_PRECOUNT_WARN` | If a pre-load `COUNT(*)` is ≥ this value, confirm before fetching (default `100000`). |
| `CHEMMANAGER_SQLITE_TIMEOUT_S` | SQLite `connect_args["timeout"]` seconds (default `30`, clamped when applied). |
| `CHEMMANAGER_PG_CONNECT_TIMEOUT` | Postgres `connect_timeout` seconds (default `30`). |
| `CHEMMANAGER_DISABLE_CUSTOM_CALC` | Set to `1`, `true`, `yes`, or `on` to disable Tools → Custom Calculator (policy lockdown). |
| `CHEMMANAGER_CUSTOM_CALC_LEGACY_EVAL` | Set to `1`, `true`, `yes`, or `on` to use legacy restricted `eval` instead of the default AST evaluator. |

**Custom calculator:** by default expressions are evaluated with a restricted **AST** interpreter (`+ - * / // % **`, unary `+`/`-`, parentheses, and `math.*` callables in scope). Treat expressions as **trusted input only** (not a full sandbox). Use `CHEMMANAGER_DISABLE_CUSTOM_CALC` where policy requires; use `CHEMMANAGER_CUSTOM_CALC_LEGACY_EVAL` only if you hit an expression compatibility edge case.

**SQL URLs in logs:** at `DEBUG`, `load_from_sql` logs the connection URL with credentials redacted via `redact_sqlalchemy_url()` (best-effort).

### Running tests (development)

```bash
pip install -r requirements.txt -r requirements-dev.txt
set QT_QPA_PLATFORM=offscreen
python -m pytest tests/ -v
```

On Linux/macOS, use `export QT_QPA_PLATFORM=offscreen`. CI runs the same suite on pushes and pull requests (see `.github/workflows/ci.yml`).

### Project structure

- `chemmanager/app.py`: application entry point
- `chemmanager/ui/main_window/`: main window package (`ChemicalTableApp` composes session, table UI, ingest/export, and chemistry mixins)
- `chemmanager/ui/sketcher/widget_painting.py`: sketch canvas 2D rendering (mixed into `SketchWidget`)
- `chemmanager/ui/widgets.py`: small reusable widgets
- `chemmanager/ui/dialogs/`: tool dialogs (package; import from `chemmanager.ui.dialogs` as before)
- `chemmanager/workers.py`: background workers (load/render/calc/export)
- `physical_property_calculator.py`: original prototype (kept for compatibility during refactor)

### Stereochemistry and isomerism

ChemManager (RDKit + the 2D sketcher) handles **tetrahedral** stereo (wedge/hash → R/S where CIP applies) and **alkene E/Z** inference from 2D layout (see `docs/STEREO_AND_ISOMERISM.md`). It does **not** automatically enumerate tautomers or atropisomers; each row/sketch is one explicit structure. Read that doc before changing stereo-related code.

**Valence, bond order, aromaticity, atom types:** the sketcher stores bond **order** 1–3 (single/double/triple), sums orders for local valence warnings, maps loaded **aromatic** RDKit bonds to order 1, and exports Kekulé-style bonds to RDKit—see `docs/VALENCE_BONDS_AND_AROMATICITY.md`.

