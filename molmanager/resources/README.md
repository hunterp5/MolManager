# Bundled resources

## Static assets (in repository)

- **3Dmol.js** — `molmanager/ui/static/3Dmol-min.js` (offline 3D viewer)

## Optional CLI binaries (not in git — place locally or use installer)

molmanager can run these when installed on the system **or** when copied into:

```
molmanager/resources/bin/win/     # Windows: vina.exe, smina.exe
molmanager/resources/bin/linux/ # Linux
molmanager/resources/bin/mac/   # macOS
```

Override the search directory with environment variable `MOLMANAGER_BUNDLE_DIR`.

| Tool | License / size | Install |
|------|----------------|---------|
| **AutoDock Vina / smina** | Academic/free; small binary | [vina.scripps.edu](https://vina.scripps.edu) — copy `vina` / `vina.exe` (or `smina`) into `bin/<platform>/` |

Python dependencies (RDKit, PyQt5, optional pkasolver/torch) are installed via `pip install -e .` (optionally `pip install -r requirements-all.txt`) — see root **README** and **docs/PACKAGING.md**.

## GNN-MTL permeability model (optional, not in git)

`molmanager/resources/models/gnn_mtl/model.pt` — see `models/gnn_mtl/README.md`. Download:

```bash
python scripts/bootstrap_gnn_mtl_model.py
pip install -r requirements-permeability.txt
```
