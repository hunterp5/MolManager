# Bundled resources

## Static assets (in repository)

- **3Dmol.js** — `chemmanager/ui/static/3Dmol-min.js` (offline 3D viewer)

## Optional CLI binaries (not in git — place locally or use installer)

ChemManager can run these when installed on the system **or** when copied into:

```
chemmanager/resources/bin/win/     # Windows: vina.exe, boltz.exe
chemmanager/resources/bin/linux/ # Linux
chemmanager/resources/bin/mac/   # macOS
```

Override the search directory with environment variable `CHEMMANAGER_BUNDLE_DIR`.

| Tool | License / size | Install |
|------|----------------|---------|
| **AutoDock Vina** | Academic/free; small binary | [vina.scripps.edu](https://vina.scripps.edu) — copy `vina` / `vina.exe` into `bin/<platform>/` |
| **Boltz** | MIT; large (PyTorch + models) | `pip install boltz` then copy `boltz` from your env’s `Scripts`/`bin`, or rely on PATH |

Python dependencies (RDKit, PyQt5, optional pkasolver/torch) are installed via `pip install -r requirements-all.txt` or `pip install -e ".[all]"` — see root **README** and **docs/PACKAGING.md**.
