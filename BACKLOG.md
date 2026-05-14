# ChemManager — actionable backlog (desktop)

Scope: **single-user desktop PyQt5 application** (no multi-tenant server in this backlog). Items are ordered by **priority** within each phase; adjust order if your release target differs.

Legend: **P0** = ship blocker or high risk, **P1** = strong quality/scalability win, **P2** = polish / future-proofing.

**Progress (latest):** **B3**, **C1.1** + **C1.3**, **C1.2** (AST custom calculator + optional `CHEMMANAGER_CUSTOM_CALC_LEGACY_EVAL`), **C2.3**, **D2** (`chemmanager/config.py` + README env table), informational **pip-audit** CI. Next: **B2**, **D1**, **E1**, or deeper **A2** logging pass.

---

## Phase A — Trust & regressions

### A1 — Automated tests + CI **(P0)** — done

- **A1.1** ~~Add `pytest` dev dependency; `tests/` package with `conftest.py` (minimal Qt: `QT_QPA_PLATFORM=offscreen` where applicable).~~ (`requirements-dev.txt`, `tests/conftest.py`, `pytest.ini`)
- **A1.2** ~~Unit tests: `safe_float`, `safe_mol_prop_string`, session document round-trip (`_build_session_document` → parse → key fields equal), `CompoundTableModel` append/set_cell/hidden row assumptions used by filters.~~ (`tests/test_*.py`)
- **A1.3** ~~Smoke test: `import chemmanager` + `ChemicalTableApp()` construction without showing (or short-lived widget) in CI.~~
- **A1.4** ~~CI workflow (e.g. GitHub Actions): install deps from `requirements.txt`, run `pytest`, cache pip.~~ (`.github/workflows/ci.yml`)

**Done when:** CI green on main; README points to “Running tests”. *(README updated; enable Actions on your repo if needed.)*

---

### A2 — Logging & failure visibility **(P1)** — partial

- **A2.1** ~~Configure `logging` in `chemmanager.app:main` (level from env `CHEMMANAGER_LOG_LEVEL`, default INFO).~~
- **A2.2** Partial: export completion fallback signal, UniversalLoadWorker emit fallback, session save/open now log failures; further `except: pass` audit optional.
- **A2.3** ~~User-visible export/load errors already use signals/message boxes — ensure stack traces go to log, not only UI string.~~ (`ExportWorker`, session open/save use `logger.exception`.)

**Done when:** Support can diagnose a failed import/export from a log file without reproducing. *(Extend logging to other dialogs as needed.)*

---

## Phase B — Desktop scale & responsiveness

### B1 — Substructure filter off the UI thread **(P1)** — done

- **B1.1** ~~`QRunnable` (or reuse thread pool) that takes compiled SMARTS + list of `(oid, smiles)` or serialized inputs, returns `set` of oids that match (or bitmask).~~ (`SubstructureFilterWorker` + `SubstructureFilterSignals` in `workers.py`; `CHEMMANAGER_SUBSTRUCTURE_ASYNC_ROWS`.)
- **B1.2** ~~Main thread applies row visibility from result; cancel/replace job when SMARTS changes mid-flight (generation counter like session chunk restore).~~ (`_substructure_job_gen`, `_invalidate_substructure_async_jobs`, stale SMARTS re-triggers `apply_filters`.)
- **B1.3** ~~Progress: indeterminate bar or “Filtering… N rows” in status for large tables.~~ (status line: `Filtering substructure… (N rows)` then normal “Showing …” after apply.)

**Done when:** Typing SMARTS on 10k+ rows does not freeze the window for multi-second spans; cancelling mid-run does not apply stale results. *(Multiple substructure cards still use the synchronous path.)*

---

### B2 — Bounds / filter metadata cost **(P2)**

- **B2.1** Incremental or dirty-flag update for `numeric_bounds_by_column` instead of full recompute on every small edit (optional column-level dirty set).
- **B2.2** Cap or sample very wide tables (e.g. >200 numeric columns) with documented behavior.

**Done when:** Editing cells on wide tables does not cause multi-second stalls from bounds alone.

---

### B3 — SQL & large imports (desktop file / DB) **(P1)** — mostly done

- **B3.1** ~~Document and enforce a **default max row** for `load_from_sql` (user override in dialog + hard ceiling).~~ (`CHEMMANAGER_SQL_MAX_ROWS_HARD`, dialog spinbox cap, clamp in `load_from_sql`.)
- **B3.2** Optional chunked `read_sql` / iterator path for supported drivers to cap peak memory. *(Deferred — document larger loads via lower Max rows.)*
- **B3.3** ~~User-facing warning when result set exceeds threshold before loading.~~ (Pre-load `COUNT(*)` when ≥ `CHEMMANAGER_SQL_PRECOUNT_WARN`; post-load note when row count hits limit.)

**Done when:** Accidental “SELECT * FROM huge_table” cannot exhaust RAM without an explicit confirmation. *(Precount can fail on exotic SQL; then only the post-load truncation message applies.)*

---

## Phase C — Safety (desktop threat model)

### C1 — Custom calculator **(P1)** — done

- **C1.1** ~~Settings or env: `CHEMMANAGER_DISABLE_CUSTOM_CALC=1` for locked-down deployments.~~ (Env + greyed menu action + dialog guard.)
- **C1.2** ~~Replace default ``eval`` with AST-limited numeric evaluation (`chemmanager/safe_calc.py`); ``CHEMMANAGER_CUSTOM_CALC_LEGACY_EVAL`` restores the old path.~~
- **C1.3** ~~Document in README: not a sandbox; trusted expressions only.~~

**Done when:** Org policy can disable feature; default path avoids ``eval``; legacy flag documented for edge cases.

---

### C2 — Session & file safety **(P2)**

- **C2.1** Validate session JSON schema version strictly; reject unknown fields with clear error (optional forward compatibility table).
- **C2.2** Optional **password-based encryption** for `.cms` (defer if low demand; document “store sessions on encrypted disk” as alternative).
- **C2.3** ~~Redact SQL connection URLs in logs (password fragment).~~ (`redact_sqlalchemy_url` in `utils.py`; `load_from_sql` logs redacted URL at `DEBUG` only.)

**Done when:** No credentials in plain log lines by default. *(Avoid `DEBUG` in production if URLs must not appear at all.)*

---

## Phase D — Modularity (maintainability, still desktop)

### D1 — Extract non-Qt services **(P2)**

- **D1.1** `chemmanager/services/session_io.py` — build/parse session dict, CSV helpers (no Qt).
- **D1.2** `chemmanager/services/structure_io.py` — SMILES/InChI → mol, field population from mol (used by mixins + workers).
- **D1.3** Thin mixin methods that delegate to services (behavior unchanged, tests hit services).

**Done when:** New contributor can change session format without opening 500-line mixin files.

---

### D2 — Central configuration **(P2)** — done

- **D2.1** ~~`chemmanager/config.py` + ``ChemManagerConfig`` / ``load_config()``: thread pools, filter debounce, SQL caps/timeouts, log level, calc flags, worker parallelism.~~
- **D2.2** ~~README table of env vars.~~

**Done when:** One module lists tunables; README documents them. *(Call sites read ``load_config()``; no global cache so tests can monkeypatch env.)*

---

## Phase E — Packaging & distribution (desktop enterprise)

### E1 — Installer & updates **(P2)**

- **E1.1** PyInstaller/cx_Freeze (or brief) recipe + documented build steps.
- **E1.2** Version string in UI title or About dialog; single source of truth (`__version__`).

**Done when:** IT can install a pinned build without a Python toolchain on target machines.

---

### E2 — Dependency hygiene **(P1)** — partial

- **E2.1** Pin upper bounds in `requirements.txt` or migrate to `requirements.lock` / Poetry for reproducible builds.
- **E2.2** ~~Quarterly process note: run `pip-audit` / dependabot.~~ (`pip-audit` in CI, `continue-on-error` so findings are visible without blocking merges.)

**Done when:** CI or release checklist includes vulnerability scan. *(Tighten pins / fail build on critical CVEs when ready.)*

---

## Suggested sequencing (milestones)

| Milestone | Includes | Outcome |
|-----------|----------|---------|
| **M1 — Safe baseline** | A1, A2, E2 | Regressions caught; logs useful; deps controlled |
| **M2 — Large desktop tables** | B1, B3 | UI stays usable on big data |
| **M3 — Policy & hardening** | C1, C2 (partial) | Meets stricter org policies |
| **M4 — Maintainability** | D1, D2, B2, E1 | Cheaper to extend and ship |

---

## Explicitly out of scope (this backlog)

- Multi-user server, SSO, web client, centralized project database.
- Real-time collaboration.

Revisit if the product later adds a sync or “team project” story.
