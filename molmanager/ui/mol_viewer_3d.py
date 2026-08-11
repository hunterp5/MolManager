"""Interactive molecular viewers using RDKit + bundled 3Dmol.js (3D conformers or 2D coordinates, offline)."""

from __future__ import annotations

import base64
import json
import logging
import shutil
from pathlib import Path

from PyQt5.QtCore import QTemporaryDir, QTimer, QUrl, Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rdkit import Chem
from rdkit.Chem import AllChem

from ..confs_codec import conformer_mol_blocks_b64_json
from .qt_widget_utils import make_window_minimizable

logger = logging.getLogger(__name__)

# Vendored build (https://3dmol.org — BSD). See molmanager/ui/static/3Dmol-min.js
_BUNDLED_3DMOL = Path(__file__).resolve().parent / "static" / "3Dmol-min.js"


def bundled_3dmol_available() -> bool:
    """True if the vendored 3Dmol script is present (offline-capable viewer)."""
    try:
        return _BUNDLED_3DMOL.is_file() and _BUNDLED_3DMOL.stat().st_size > 10_000
    except OSError:
        return False


def _wire_webengine_console_logger(web) -> None:
    """
    Forward Qt WebEngine JavaScript console output to Python logging.

    Many lines users see in DevTools are **benign** Chromium hints (e.g. non-passive ``wheel``
    listeners inside 3Dmol.js) or **deprecation notices** from the bundled library; those are
    downgraded to DEBUG. **Real JS errors** still surface as WARNING so they can be investigated.
    WebGL/driver warnings are outside the app and cannot be fixed from Python.
    """
    try:
        from PyQt5.QtWebEngineWidgets import QWebEnginePage

        def on_js_console(level, message, line, source):
            msg = (message or "").strip()
            if not msg:
                return
            low = msg.lower()
            if "violation" in low and "non-passive" in low:
                logger.debug("3D viewer (benign): %s", msg)
                return
            if "deprecated" in low or "deprecation" in low:
                logger.debug("3D viewer (deprecation): %s", msg)
                return
            if "webgl" in low and ("lost" in low or "context" in low):
                logger.info("3D viewer (GPU/WebGL): %s", msg)
                return
            if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                logger.warning("3D viewer JS error: %s (line %s, %s)", msg, line, source)
            elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
                logger.info("3D viewer JS warning: %s", msg)
            else:
                logger.debug("3D viewer JS: %s", msg)

        web.page().javaScriptConsoleMessage.connect(on_js_console)
    except Exception:
        logger.debug("3D viewer: could not attach JS console logger", exc_info=True)


def _atom_info_panel_html() -> str:
    """Fixed panel for hover preview and click-selected atom details (3Dmol click/hover callbacks)."""
    return """
<div id="chem-atom-panel" style="position:fixed;top:8px;left:8px;z-index:21;max-width:min(340px,calc(100vw - 16px));font:12px/1.4 system-ui,Segoe UI,sans-serif;background:rgba(255,255,255,0.95);border:1px solid #c8c8c8;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.12);padding:8px 10px;pointer-events:none;">
  <div style="font-weight:600;margin-bottom:4px;">Atom</div>
  <div id="chem-atom-hover" style="font-size:11px;color:#555;min-height:1.2em;"></div>
  <div id="chem-atom-detail" style="margin-top:6px;font-family:ui-monospace,Consolas,monospace;white-space:pre-wrap;font-size:11px;color:#111;"></div>
</div>
"""


def _viewer_init_script_fragment(mol_b64: str, *, flat: bool) -> str:
    """JavaScript to create a 3Dmol viewer (no atom-info panel / mouse-help overlay)."""
    flat_js = "true" if flat else "false"
    tmpl = r"""  <script>
    function molmanagerInitView() {
      try {
        const data = atob("__MOLB64__");
        const flat = __FLAT__;
        const opts = flat ? { backgroundColor: "white", orthographic: true } : { backgroundColor: "white" };
        const viewer = $3Dmol.createViewer("v", opts);
        viewer.addModel(data, "mol");
        var stickR = flat ? 0.1 : 0.12;
        var sph = flat ? 0.18 : 0.22;
        viewer.setStyle({}, { stick: { radius: stickR }, sphere: { scale: sph } });
        try { viewer.resize(); } catch (e0) {}
        viewer.zoomTo();
        try { viewer.zoom(0.88); } catch (e1) {}
        viewer.render();
      } catch (e) {
        document.body.innerHTML = "<pre style='padding:12px;font-family:monospace'>3Dmol error: " + e + "</pre>";
      }
    }
  </script>"""
    return tmpl.replace("__MOLB64__", mol_b64).replace("__FLAT__", flat_js)


def _viewer_embed_init_script_fragment(mol_b64: str = "") -> str:
    """Minimal 3Dmol init for sketcher side panel: no atom pick UI; live ``molmanagerSetMolB64``."""
    tmpl = r"""  <script>
    function molmanagerFitView(v) {
      if (!v) return;
      try { v.resize(); } catch (e0) {}
      v.zoomTo();
      /* Pull back slightly so the whole model sits inside the frame with padding. */
      try { v.zoom(0.88); } catch (e1) {}
      v.render();
    }
    function molmanagerInitView() {
      try {
        const opts = { backgroundColor: "white" };
        const viewer = $3Dmol.createViewer("v", opts);
        window.molmanagerViewer = viewer;
        window.molmanagerRefit = function () { molmanagerFitView(window.molmanagerViewer); };
        window.molmanagerSetMolB64 = function (b64) {
          if (!window.molmanagerViewer) return;
          var v = window.molmanagerViewer;
          v.clear();
          if (b64) {
            v.addModel(atob(b64), "mol");
            v.setStyle({}, { stick: { radius: 0.12 }, sphere: { scale: 0.22 } });
            molmanagerFitView(v);
          } else {
            v.render();
          }
        };
        window.molmanagerSetMolB64("__MOLB64__");
        window.addEventListener("resize", function () {
          if (window.molmanagerRefit) window.molmanagerRefit();
        });
      } catch (e) {
        document.body.innerHTML = "<pre style='padding:12px;font-family:monospace'>3Dmol error: " + e + "</pre>";
      }
    }
  </script>"""
    return tmpl.replace("__MOLB64__", mol_b64 or "")


def _assemble_viewer_page(
    mol_b64: str,
    *,
    flat: bool,
    script_src: str,
    show_atom_panel: bool = False,
    show_mouse_help: bool = False,
) -> str:
    init = _viewer_init_script_fragment(mol_b64, flat=flat)
    help_html = _viewer_help_overlay_html() if show_mouse_help else ""
    atom_panel = _atom_info_panel_html() if show_atom_panel else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>html,body,#v{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;}}</style>
</head>
<body>
  <div id="v"></div>
{atom_panel}
{init}
  <script src="{script_src}" onload="molmanagerInitView()"></script>
{help_html}
</body>
</html>"""


def _assemble_embed_viewer_page(mol_b64: str, *, script_src: str) -> str:
    """Sketcher-embedded page: no atom boxes or mouse-controls overlay."""
    init = _viewer_embed_init_script_fragment(mol_b64)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>html,body,#v{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#fff;}}</style>
</head>
<body>
  <div id="v"></div>
{init}
  <script src="{script_src}" onload="molmanagerInitView()"></script>
</body>
</html>"""


def _offline_index_html(mol_b64: str, *, flat: bool = False) -> str:
    """Minimal page next to ``3Dmol-min.js`` (same directory)."""
    return _assemble_viewer_page(mol_b64, flat=flat, script_src="3Dmol-min.js")


def _offline_embed_index_html(mol_b64: str = "") -> str:
    return _assemble_embed_viewer_page(mol_b64, script_src="3Dmol-min.js")


def _offline_index_html_multiconf(
    blocks_json_b64: str,
    *,
    initial_superpose: bool = False,
    strain_overlay_json_b64: str = "",
    initial_conf_index: int = 0,
) -> str:
    return _assemble_viewer_page_multiconf(
        blocks_json_b64,
        script_src="3Dmol-min.js",
        initial_superpose=initial_superpose,
        strain_overlay_json_b64=strain_overlay_json_b64,
        initial_conf_index=initial_conf_index,
    )


def _cdn_fallback_html(mol_b64: str, *, flat: bool = False) -> str:
    """Same as offline page but loads 3Dmol from the network (only if the bundle is missing)."""
    return _assemble_viewer_page(mol_b64, flat=flat, script_src="https://3dmol.org/build/3Dmol-min.js")


def _cdn_embed_fallback_html(mol_b64: str = "") -> str:
    return _assemble_embed_viewer_page(mol_b64, script_src="https://3dmol.org/build/3Dmol-min.js")


def _cdn_fallback_html_multiconf(
    blocks_json_b64: str,
    *,
    initial_superpose: bool = False,
    strain_overlay_json_b64: str = "",
    initial_conf_index: int = 0,
) -> str:
    return _assemble_viewer_page_multiconf(
        blocks_json_b64,
        script_src="https://3dmol.org/build/3Dmol-min.js",
        initial_superpose=initial_superpose,
        strain_overlay_json_b64=strain_overlay_json_b64,
        initial_conf_index=initial_conf_index,
    )


def _viewer_help_overlay_html() -> str:
    """On-page reference for 3Dmol.js default GLViewer mouse bindings (matches bundled 3Dmol)."""
    return """
<details id="chem3d-help" style="position:fixed;bottom:6px;right:6px;z-index:20;max-width:min(380px,calc(100vw - 12px));font:12px/1.45 system-ui,Segoe UI,sans-serif;background:rgba(255,255,255,0.94);border:1px solid #c8c8c8;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.12);padding:0;">
  <summary style="cursor:pointer;list-style:none;padding:8px 12px;font-weight:600;user-select:none;">Mouse controls</summary>
  <div style="padding:0 12px 10px 12px;border-top:1px solid #e0e0e0;">
    <ul style="margin:0;padding-left:1.1em;">
      <li><b>Click</b> an atom — select &amp; show details (top-left)</li>
      <li><b>Click</b> empty background — clear selection</li>
      <li><b>Hover</b> an atom — quick label under “Atom”</li>
      <li><b>Left drag</b> — rotate the model</li>
      <li><b>Scroll wheel</b> — zoom in / out</li>
      <li><b>Ctrl + wheel</b> — zoom (reversed direction)</li>
      <li><b>Shift + left drag</b> — zoom (vertical drag)</li>
      <li><b>Middle drag</b> — pan (translate)</li>
      <li><b>Ctrl + left drag</b> — pan (translate)</li>
      <li><b>Right drag</b> — zoom (vertical drag)</li>
      <li><b>Ctrl + right drag</b> — adjust front/back clipping (slab)</li>
    </ul>
  </div>
</details>
<style>
#chem3d-help summary::-webkit-details-marker { display: none; }
#chem3d-help[open] summary { border-bottom: 1px solid #e0e0e0; }
</style>
"""


def build_3dmol_html(mol_b64: str) -> str:
    """Return a self-contained HTML document (offline bundle when available, else CDN)."""
    return _offline_index_html(mol_b64, flat=False) if bundled_3dmol_available() else _cdn_fallback_html(mol_b64, flat=False)


def prepare_mol_3d(mol: Chem.Mol) -> Chem.Mol | None:
    """
    Return a copy of *mol* with 3D coordinates (ETKDG embed + MMFF/UFF), or ``None`` on failure.

    Sketch-built molecules often carry Kekulé bond orders and a flat 2D conformer.
    Sanitize (aromatize) and clear conformers first so ETKDG uses aromatic ring
    templates — otherwise heterocycles can embed as non-planar.
    """
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    try:
        m = Chem.Mol(mol)
    except Exception:
        return None
    try:
        Chem.SanitizeMol(m)
    except Exception:
        try:
            m.UpdatePropertyCache(strict=False)
            Chem.GetSymmSSSR(m)
            Chem.SetAromaticity(m)
        except Exception:
            try:
                m.UpdatePropertyCache(strict=False)
            except Exception:
                pass
    try:
        m.RemoveAllConformers()
    except Exception:
        pass
    try:
        m = Chem.AddHs(m, addCoords=False)
    except TypeError:
        try:
            m = Chem.AddHs(m)
        except Exception:
            return None
    except Exception:
        return None
    params = None
    for name in ("ETKDGv3", "ETKDGv2", "ETKDG"):
        factory = getattr(AllChem, name, None)
        if factory is None:
            continue
        try:
            params = factory()
            break
        except Exception:
            continue
    if params is None:
        return None
    try:
        # Ignore any leftover coords; embed from distance geometry + ring templates.
        if hasattr(params, "clearConfs"):
            params.clearConfs = True
        if hasattr(params, "useRandomCoords"):
            params.useRandomCoords = True
    except Exception:
        pass
    try:
        cid = AllChem.EmbedMolecule(m, params)
    except Exception:
        cid = -1
    if cid != 0:
        try:
            cid = AllChem.EmbedMolecule(m, randomSeed=0xC0FFEE)
        except Exception:
            cid = -1
    if cid != 0:
        logger.warning("RDKit could not embed a 3D conformer for this structure.")
        return None
    try:
        AllChem.MMFFOptimizeMolecule(m, maxIters=200)
    except Exception:
        try:
            AllChem.UFFOptimizeMolecule(m, maxIters=200)
        except Exception:
            pass
    try:
        m = Chem.RemoveHs(m)
    except Exception:
        pass
    return m


def prepare_mol_2d(mol: Chem.Mol) -> Chem.Mol | None:
    """Return a copy of *mol* with 2D coordinates for a flat depiction, or ``None`` on failure."""
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    try:
        m = Chem.Mol(mol)
    except Exception:
        return None
    try:
        from rdkit.Chem import rdDepictor

        if rdDepictor.Compute2DCoords(m) != 0:
            raise RuntimeError("rdDepictor.Compute2DCoords failed")
    except Exception:
        try:
            if AllChem.Compute2DCoords(m) != 0:
                return None
        except Exception:
            return None
    return m


def _mol_block_b64(mol: Chem.Mol) -> str:
    block = Chem.MolToMolBlock(mol)
    return base64.b64encode(block.encode("utf-8")).decode("ascii")


def _viewer_controls_multiconf_html() -> str:
    return """
<div id="chem-strain-overlay" style="display:none;position:fixed;top:8px;left:8px;z-index:25;pointer-events:none;max-width:min(320px,calc(100vw - 16px));padding:8px 10px;font:12px/1.4 system-ui,Segoe UI,sans-serif;color:#1a1a1a;background:rgba(255,255,255,0.94);border:1px solid #c8c8c8;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.12);">
  <div id="chem-strain-abs" style="font-weight:600;"></div>
  <div id="chem-strain-delta" style="margin-top:2px;"></div>
  <div id="chem-strain-rmsd" style="margin-top:2px;"></div>
  <div id="chem-strain-meta" style="margin-top:4px;color:#555;font-size:11px;"></div>
</div>
<div id="chem-conf-bar" style="position:fixed;left:50%;bottom:52px;transform:translateX(-50%);width:min(480px,calc(100vw - 20px));z-index:22;pointer-events:auto;box-sizing:border-box;display:flex;flex-direction:column;gap:14px;padding:10px 12px;font:12px/1.35 system-ui,Segoe UI,sans-serif;background:rgba(255,255,255,0.97);border:1px solid #c0c0c0;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.14);">
  <div style="display:flex;flex-wrap:wrap;align-items:center;column-gap:12px;row-gap:6px;width:100%;">
    <span style="font-weight:600;color:#222;">Conformers</span>
    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;"><input type="radio" name="chem-conf-view" id="chem-view-one" checked="checked"/> Single</label>
    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;"><input type="radio" name="chem-conf-view" id="chem-view-super"/> Superpose</label>
    <span id="chem-conf-label" style="margin-left:auto;font-weight:600;color:#333;white-space:nowrap;padding-left:8px;"></span>
  </div>
  <div id="chem-conf-nav" style="display:flex;align-items:center;gap:8px;width:100%;box-sizing:border-box;margin-top:2px;">
    <button type="button" id="chem-conf-prev" style="flex:0 0 auto;padding:5px 12px;cursor:pointer;font:inherit;">Prev</button>
    <input type="range" id="chem-conf-slider" min="0" max="0" value="0" step="1" style="flex:1 1 auto;min-width:0;width:0;height:22px;cursor:pointer;"/>
    <button type="button" id="chem-conf-next" style="flex:0 0 auto;padding:5px 12px;cursor:pointer;font:inherit;">Next</button>
  </div>
</div>
"""


def _viewer_init_script_multiconf(
    blocks_json_b64: str,
    *,
    initial_superpose: bool = False,
    strain_overlay_json_b64: str = "",
    initial_conf_index: int = 0,
) -> str:
    """Multi-conformer 3Dmol page: one-at-a-time vs superpose-all (no atom pick panel; mouse = rotate/zoom)."""
    init_sp = "true" if initial_superpose else "false"
    energy_b64 = (strain_overlay_json_b64 or "").strip()
    start_idx = max(0, int(initial_conf_index))
    tmpl = r"""  <script>
    function molmanagerInitView() {
      try {
        var initialSuperpose = __INIT_SP__;
        var startIdx = __START_IDX__;
        const blocks = JSON.parse(atob("__BLOCKSJSONB64__"));
        if (!blocks || blocks.length === 0) {
          document.body.innerHTML = "<pre style='padding:12px'>No conformers to display.</pre>";
          return;
        }
        if (blocks.length < 2) initialSuperpose = false;
        startIdx = Math.max(0, Math.min(startIdx, blocks.length - 1));
        var energyInfo = null;
        try {
          var _eb = "__ENERGYJSONB64__";
          if (_eb && _eb.length > 4) energyInfo = JSON.parse(atob(_eb));
        } catch (_ee) { energyInfo = null; }
        const viewer = $3Dmol.createViewer("v", { backgroundColor: "white" });
        var curIdx = startIdx;
        var superposed = false;
        var palette = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#f39c12", "#16a085", "#d35400", "#34495e"];

        function fmtKcal(v) {
          if (typeof v !== "number" || !isFinite(v)) return "—";
          var s = (Math.round(v * 1000) / 1000).toFixed(3);
          return s;
        }

        function updateStrainOverlay(modeIdx) {
          var el = document.getElementById("chem-strain-overlay");
          if (!el) return;
          if (!energyInfo || !energyInfo.energies || !energyInfo.deltas) {
            el.style.display = "none";
            return;
          }
          el.style.display = "block";
          var absEl = document.getElementById("chem-strain-abs");
          var dEl = document.getElementById("chem-strain-delta");
          var rmsEl = document.getElementById("chem-strain-rmsd");
          var mEl = document.getElementById("chem-strain-meta");
          var ff = energyInfo.ff || "";
          var refIdx = (typeof energyInfo.ref_idx === "number") ? energyInfo.ref_idx : 0;
          var hasRms = energyInfo.rmsds && energyInfo.rmsds.length;
          if (superposed) {
            var eRef = energyInfo.e_ref;
            var maxDe = energyInfo.strain_max;
            if (absEl) absEl.textContent = "E_ref = " + fmtKcal(eRef) + " kcal/mol";
            if (dEl) dEl.textContent = "max ΔE = " + fmtKcal(maxDe) + " kcal/mol";
            if (rmsEl) {
              if (hasRms && typeof energyInfo.rmsd_max === "number") {
                rmsEl.style.display = "block";
                rmsEl.textContent = "max RMSD = " + fmtKcal(energyInfo.rmsd_max) + " Å";
              } else {
                rmsEl.style.display = "none";
                rmsEl.textContent = "";
              }
            }
            if (mEl) mEl.textContent = (ff ? (ff + " · ") : "") + "ref conf " + (refIdx + 1);
            return;
          }
          var i = Math.max(0, Math.min(modeIdx, energyInfo.energies.length - 1));
          var eAbs = energyInfo.energies[i];
          var dE = energyInfo.deltas[i];
          if (absEl) absEl.textContent = "E = " + fmtKcal(eAbs) + " kcal/mol";
          if (dEl) dEl.textContent = "ΔE vs ref = " + fmtKcal(dE) + " kcal/mol";
          if (rmsEl) {
            if (hasRms) {
              var ri = Math.max(0, Math.min(i, energyInfo.rmsds.length - 1));
              rmsEl.style.display = "block";
              rmsEl.textContent = "RMSD vs ref = " + fmtKcal(energyInfo.rmsds[ri]) + " Å";
            } else {
              rmsEl.style.display = "none";
              rmsEl.textContent = "";
            }
          }
          if (mEl) mEl.textContent = (ff ? (ff + " · ") : "") + "ref conf " + (refIdx + 1);
        }

        function baseRadii() {
          return { stickR: 0.12, sph: 0.22 };
        }

        function applySingleStyle() {
          var br = baseRadii();
          viewer.setStyle({}, {
            stick: { radius: br.stickR },
            sphere: { scale: br.sph }
          });
        }

        function restyleSuperpose() {
          for (var mi = 0; mi < blocks.length; mi++) {
            var c = palette[mi % palette.length];
            viewer.setStyle({ model: mi }, {
              stick: { radius: 0.09, color: c },
              sphere: { scale: 0.17, color: c }
            });
          }
        }

        function showSuperpose() {
          superposed = true;
          viewer.clear();
          for (var i = 0; i < blocks.length; i++) {
            viewer.addModel(atob(blocks[i]), "mol");
          }
          restyleSuperpose();
          viewer.zoomTo();
          viewer.render();
          var lab = document.getElementById("chem-conf-label");
          if (lab) lab.textContent = blocks.length + " superposed";
          var nav = document.getElementById("chem-conf-nav");
          if (nav) nav.style.display = "none";
          updateStrainOverlay(curIdx);
        }

        function loadConf(i) {
          superposed = false;
          i = Math.max(0, Math.min(i, blocks.length - 1));
          curIdx = i;
          viewer.clear();
          viewer.addModel(atob(blocks[i]), "mol");
          applySingleStyle();
          viewer.zoomTo();
          viewer.render();
          var lab = document.getElementById("chem-conf-label");
          if (lab) lab.textContent = (i + 1) + " / " + blocks.length;
          var sl = document.getElementById("chem-conf-slider");
          if (sl) sl.value = String(i);
          var nav = document.getElementById("chem-conf-nav");
          if (nav) nav.style.display = "flex";
          updateStrainOverlay(i);
        }

        function updateViewMode() {
          var rSuper = document.getElementById("chem-view-super");
          var wantSuper = rSuper && rSuper.checked && blocks.length >= 2;
          if (wantSuper) showSuperpose();
          else {
            var rOne = document.getElementById("chem-view-one");
            if (rOne) rOne.checked = true;
            loadConf(curIdx);
          }
        }

        var slider = document.getElementById("chem-conf-slider");
        if (slider) {
          slider.max = String(Math.max(0, blocks.length - 1));
          slider.addEventListener("input", function (ev) {
            ev.stopPropagation();
            var rOne = document.getElementById("chem-view-one");
            if (rOne) rOne.checked = true;
            loadConf(parseInt(slider.value, 10) || 0);
          });
        }
        var prev = document.getElementById("chem-conf-prev");
        if (prev) prev.addEventListener("click", function () {
          var rOne = document.getElementById("chem-view-one");
          if (rOne) rOne.checked = true;
          loadConf(curIdx - 1);
        });
        var next = document.getElementById("chem-conf-next");
        if (next) next.addEventListener("click", function () {
          var rOne = document.getElementById("chem-view-one");
          if (rOne) rOne.checked = true;
          loadConf(curIdx + 1);
        });

        var radioOne = document.getElementById("chem-view-one");
        var radioSuper = document.getElementById("chem-view-super");
        if (radioOne) radioOne.addEventListener("change", updateViewMode);
        if (radioSuper) radioSuper.addEventListener("change", updateViewMode);
        if (blocks.length < 2 && radioSuper) {
          radioSuper.disabled = true;
          radioSuper.title = "Need at least two conformers";
        }

        var bar = document.getElementById("chem-conf-bar");
        if (bar) {
          bar.addEventListener("mousedown", function (e) { e.stopPropagation(); }, false);
          bar.addEventListener("wheel", function (e) { e.stopPropagation(); }, { passive: true });
        }

        if (initialSuperpose && radioSuper && blocks.length >= 2) {
          radioSuper.checked = true;
          updateViewMode();
        } else {
          loadConf(startIdx);
        }
        window.molmanagerConfState = function () {
          return { idx: curIdx, superposed: !!superposed, n: blocks.length };
        };
      } catch (e) {
        document.body.innerHTML = "<pre style='padding:12px;font-family:monospace'>3Dmol error: " + e + "</pre>";
      }
    }
  </script>"""
    return (
        tmpl.replace("__BLOCKSJSONB64__", blocks_json_b64)
        .replace("__INIT_SP__", init_sp)
        .replace("__ENERGYJSONB64__", energy_b64)
        .replace("__START_IDX__", str(start_idx))
    )


def _assemble_viewer_page_multiconf(
    blocks_json_b64: str,
    *,
    script_src: str,
    initial_superpose: bool = False,
    strain_overlay_json_b64: str = "",
    initial_conf_index: int = 0,
) -> str:
    init = _viewer_init_script_multiconf(
        blocks_json_b64,
        initial_superpose=initial_superpose,
        strain_overlay_json_b64=strain_overlay_json_b64,
        initial_conf_index=initial_conf_index,
    )
    conf_bar = _viewer_controls_multiconf_html()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>html,body,#v{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;}}</style>
</head>
<body>
  <div id="v"></div>
{conf_bar}
{init}
  <script src="{script_src}" onload="molmanagerInitView()"></script>
</body>
</html>"""


class Molecule3DEmbedView(QWidget):
    """
    Sketcher side-panel 3Dmol view: no atom-info boxes or mouse-controls overlay.

    Call ``set_molecule`` to refresh after sketch edits (ETKDG embed + MMFF/UFF).
    The WebEngine page is created lazily on first show so QtWebEngine can preload at app start.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._viewer_tmp: QTemporaryDir | None = None
        self._web_ready = False
        self._pending_b64: str | None = None
        self._web = None
        self._bootstrapped = False
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(50)
        self._refit_timer.timeout.connect(self.refit_view)
        self._status = QLabel("3D preview", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("color: palette(mid); padding: 8px;")

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._root.addWidget(self._status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_web()
        self.schedule_refit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._web_ready:
            self.schedule_refit()

    def schedule_refit(self) -> None:
        """Debounce resize/zoom so the model fills the frame after layout settles."""
        self._refit_timer.start()

    def refit_view(self) -> None:
        """Resize the WebGL canvas and zoom so the whole structure fits with padding."""
        if self._web is None or not self._web_ready:
            return
        try:
            self._web.page().runJavaScript(
                "if (window.molmanagerRefit) window.molmanagerRefit();"
            )
        except Exception:
            logger.debug("3D embed refit failed", exc_info=True)
    def _ensure_web(self) -> None:
        if self._bootstrapped:
            return
        self._bootstrapped = True
        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView

            web = QWebEngineView(self)
            _wire_webengine_console_logger(web)
            try:
                s = web.settings()
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
                s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
            except Exception:
                pass
            web.loadFinished.connect(self._on_load_finished)
            if bundled_3dmol_available():
                self._viewer_tmp = QTemporaryDir()
                if not self._viewer_tmp.isValid():
                    raise OSError("Could not create a temporary directory for the 3D viewer.")
                tmp = Path(self._viewer_tmp.path())
                shutil.copy2(_BUNDLED_3DMOL, tmp / "3Dmol-min.js")
                index = tmp / "index.html"
                index.write_text(_offline_embed_index_html(""), encoding="utf-8")
                web.load(QUrl.fromLocalFile(str(index.resolve())))
            else:
                web.setHtml(_cdn_embed_fallback_html(""), QUrl("https://3dmol.org/"))
            self._web = web
            self._status.hide()
            self._root.addWidget(web, 1)
        except Exception as e:
            logger.warning("Sketcher 3D embed unavailable: %s", e, exc_info=True)
            self._status.setText(
                "3D preview unavailable.\nInstall matching PyQtWebEngine and restart with "
                "`python -m molmanager`."
            )

    def _on_load_finished(self, ok: bool) -> None:
        self._web_ready = bool(ok)
        if self._web_ready and self._pending_b64 is not None:
            b64 = self._pending_b64
            self._pending_b64 = None
            self._run_set_mol_b64(b64)
        if self._web_ready:
            self.schedule_refit()
            QTimer.singleShot(200, self.refit_view)

    def _run_set_mol_b64(self, b64: str) -> None:
        self._ensure_web()
        if self._web is None:
            return
        if not self._web_ready:
            self._pending_b64 = b64
            return
        js = f"if (window.molmanagerSetMolB64) window.molmanagerSetMolB64({json.dumps(b64)});"
        try:
            self._web.page().runJavaScript(js)
        except Exception:
            logger.debug("3D embed set_mol failed", exc_info=True)

    def clear(self) -> None:
        """Clear the displayed model."""
        self._run_set_mol_b64("")

    def set_molecule(self, mol: Chem.Mol | None) -> None:
        """Embed *mol* in 3D and display it, or clear when *mol* is empty/invalid."""
        if mol is None or mol.GetNumAtoms() == 0:
            self.clear()
            return
        m3 = prepare_mol_3d(mol)
        if m3 is None:
            self.clear()
            return
        try:
            self._run_set_mol_b64(_mol_block_b64(m3))
            self.schedule_refit()
            QTimer.singleShot(150, self.refit_view)
        except Exception:
            logger.debug("3D embed encode failed", exc_info=True)
            self.clear()


class Molecule3DViewerDialog(QDialog):
    """Modeless dialog: interactive structure in 3Dmol (3D conformer or 2D / flat layout)."""

    def __init__(
        self,
        mol: Chem.Mol,
        parent: QWidget | None = None,
        *,
        window_title: str = "View in 3D",
        flat: bool = False,
        multi_conf_blocks_json_b64: str | None = None,
        multi_conf_initial_superpose: bool = False,
        multi_conf_strain_overlay_json_b64: str = "",
        multi_conf_initial_index: int = 0,
        export_parent_oid: int | None = None,
        export_confs_column: str = "confs",
        strain_overlay: dict | None = None,
    ):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle(window_title)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(920, 720)

        self._multi_conf_blocks_b64 = multi_conf_blocks_json_b64
        self._export_parent_oid = export_parent_oid
        self._export_confs_column = (export_confs_column or "confs").strip() or "confs"
        self._strain_overlay = dict(strain_overlay) if strain_overlay else None
        if self._strain_overlay is None and multi_conf_strain_overlay_json_b64:
            try:
                self._strain_overlay = json.loads(
                    base64.b64decode(multi_conf_strain_overlay_json_b64.encode("ascii"))
                )
            except Exception:
                self._strain_overlay = None

        mol_b64 = _mol_block_b64(mol) if multi_conf_blocks_json_b64 is None else ""
        self._viewer_tmp: QTemporaryDir | None = None

        root = QVBoxLayout(self)

        web = None
        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView

            web = QWebEngineView(self)
            _wire_webengine_console_logger(web)
            try:
                s = web.settings()
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
                s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
            except Exception:
                pass
            if bundled_3dmol_available():
                self._viewer_tmp = QTemporaryDir()
                if not self._viewer_tmp.isValid():
                    raise OSError("Could not create a temporary directory for the 3D viewer.")
                tmp = Path(self._viewer_tmp.path())
                shutil.copy2(_BUNDLED_3DMOL, tmp / "3Dmol-min.js")
                index = tmp / "index.html"
                if multi_conf_blocks_json_b64 is not None:
                    index.write_text(
                        _offline_index_html_multiconf(
                            multi_conf_blocks_json_b64,
                            initial_superpose=multi_conf_initial_superpose,
                            strain_overlay_json_b64=multi_conf_strain_overlay_json_b64,
                            initial_conf_index=multi_conf_initial_index,
                        ),
                        encoding="utf-8",
                    )
                else:
                    index.write_text(_offline_index_html(mol_b64, flat=flat), encoding="utf-8")
                web.load(QUrl.fromLocalFile(str(index.resolve())))
            else:
                if multi_conf_blocks_json_b64 is not None:
                    web.setHtml(
                        _cdn_fallback_html_multiconf(
                            multi_conf_blocks_json_b64,
                            initial_superpose=multi_conf_initial_superpose,
                            strain_overlay_json_b64=multi_conf_strain_overlay_json_b64,
                            initial_conf_index=multi_conf_initial_index,
                        ),
                        QUrl("https://3dmol.org/"),
                    )
                else:
                    web.setHtml(_cdn_fallback_html(mol_b64, flat=flat), QUrl("https://3dmol.org/"))
            web.loadFinished.connect(lambda _ok: self._refit_standalone_viewer(web))
            root.addWidget(web, 1)
            self._standalone_web = web
        except Exception as e:
            web = None
            self._standalone_web = None
            err = f"{type(e).__name__}: {e}"
            logger.warning("Embedded 3Dmol viewer unavailable: %s", err, exc_info=True)
            msg = (
                "The embedded viewer could not start.\n\n"
                f"{err}\n\n"
                "Typical fixes:\n"
                "• Install PyQtWebEngine with the same major.minor version as PyQt5 (e.g. both 5.15.x).\n"
                "• Start molmanager with `python -m molmanager` so QtWebEngine loads before the GUI initializes.\n\n"
                "Close this window when you are done."
            )
            root.addWidget(QLabel(msg), 1)

        if multi_conf_blocks_json_b64 is not None:
            btn_row = QHBoxLayout()
            self._viewer_status = QLabel("")
            self._viewer_status.setWordWrap(True)
            self._viewer_status.setStyleSheet("color: #333;")
            btn_row.addWidget(self._viewer_status, 1)
            self._btn_export_table = QPushButton("Export to Table")
            self._btn_export_table.setToolTip(
                "Add the current conformer (or all when superposed) to the main table. "
                "Writes 2D Structure, optional E / ΔE / RMSD, and packed 3D into confs when present."
            )
            self._btn_export_table.clicked.connect(self._on_export_to_table)
            if web is None:
                self._btn_export_table.setEnabled(False)
            btn_row.addWidget(self._btn_export_table)
            root.addLayout(btn_row)

        make_window_minimizable(self)

    def _set_viewer_status(self, message: str) -> None:
        msg = (message or "").strip()
        status = getattr(self, "_viewer_status", None)
        if status is not None:
            status.setText(msg)
        parent = self.parent()
        plabel = getattr(parent, "status_label", None) if parent is not None else None
        if plabel is not None and msg:
            try:
                plabel.setText(msg)
            except Exception:
                pass

    def _on_export_to_table(self) -> None:
        if not self._multi_conf_blocks_b64:
            return
        web = getattr(self, "_standalone_web", None)
        if web is None:
            self._set_viewer_status("Export failed: 3D viewer is not available.")
            return
        self._btn_export_table.setEnabled(False)
        self._set_viewer_status("Exporting to table…")

        def _finish(state) -> None:
            try:
                self._export_conformers_to_table(state)
            finally:
                try:
                    self._btn_export_table.setEnabled(True)
                except Exception:
                    pass

        try:
            web.page().runJavaScript(
                "window.molmanagerConfState ? molmanagerConfState() : null",
                _finish,
            )
        except Exception:
            self._btn_export_table.setEnabled(True)
            self._set_viewer_status("Export failed: could not read the current conformer index.")

    def _export_conformers_to_table(self, state) -> None:
        parent = self.parent()
        export_fn = getattr(parent, "export_conformer_viewer_to_table", None)
        if not callable(export_fn):
            self._set_viewer_status("Export failed: open the viewer from the main MolManager window.")
            return
        idx = 0
        superposed = False
        n = 0
        if isinstance(state, dict):
            try:
                idx = int(state.get("idx", 0))
            except Exception:
                idx = 0
            superposed = bool(state.get("superposed"))
            try:
                n = int(state.get("n", 0))
            except Exception:
                n = 0
        if superposed or n <= 1:
            indices = None  # all
        else:
            indices = [max(0, idx)]
        try:
            n_added = int(
                export_fn(
                    blocks_json_b64=self._multi_conf_blocks_b64,
                    conf_indices=indices,
                    strain_overlay=self._strain_overlay,
                    parent_oid=self._export_parent_oid,
                    confs_column=self._export_confs_column,
                )
            )
        except Exception as exc:
            logger.exception("Export to table failed")
            self._set_viewer_status(f"Export failed: {exc}")
            return
        if n_added <= 0:
            self._set_viewer_status("No conformers were exported.")
            return
        self._set_viewer_status(f"Added {n_added} row(s) to the table.")

    def _refit_standalone_viewer(self, web) -> None:
        """After load/resize, zoom so the full structure sits inside the frame."""
        if web is None:
            return
        js = (
            "try {"
            "  var el = document.getElementById('v');"
            "  if (el && el.viewer) { var v = el.viewer; v.resize(); v.zoomTo(); try { v.zoom(0.88); } catch(e){} v.render(); }"
            "  else if (window.$3Dmol && window.$3Dmol.viewers) {"
            "    var vs = window.$3Dmol.viewers; var keys = Object.keys(vs);"
            "    if (keys.length) { var v = vs[keys[0]]; v.resize(); v.zoomTo(); try { v.zoom(0.88); } catch(e){} v.render(); }"
            "  }"
            "} catch (e) {}"
        )
        try:
            web.page().runJavaScript(js)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        web = getattr(self, "_standalone_web", None)
        if web is not None:
            QTimer.singleShot(50, lambda w=web: self._refit_standalone_viewer(w))


def open_molecule_3d_viewer(mol: Chem.Mol, parent: QWidget | None = None, *, title: str = "View in 3D") -> None:
    """Show *mol* in 3Dmol: multiple RDKit conformers use a conformer slider; otherwise embed once (ETKDG)."""
    if mol is None or not isinstance(mol, Chem.Mol):
        return
    try:
        nconf = int(mol.GetNumConformers())
    except Exception:
        nconf = 0
    if nconf > 1:
        try:
            m = Chem.Mol(mol)
        except Exception:
            m = mol
        payload = conformer_mol_blocks_b64_json(m)
        try:
            inner = json.loads(base64.b64decode(payload.encode("ascii")))
        except Exception:
            inner = []
        if not inner:
            QMessageBox.warning(
                parent,
                title,
                "This molecule reports multiple conformers but none could be serialized for the 3D viewer.",
            )
            return
        win_title = title if title else "View in 3D"
        if win_title == "View in 3D":
            win_title = "View Conformers"
        win_title = f"{win_title} ({len(inner)} conformers)"
        dlg = Molecule3DViewerDialog(
            m,
            parent,
            window_title=win_title,
            flat=False,
            multi_conf_blocks_json_b64=payload,
            multi_conf_initial_superpose=False,
        )
        dlg.show()
        return

    m3d = prepare_mol_3d(mol)
    if m3d is None:
        QMessageBox.warning(
            parent,
            title,
            "Could not build a 3D conformation for this structure.\n"
            "Try editing the structure or simplifying the molecule.",
        )
        return
    dlg = Molecule3DViewerDialog(m3d, parent, window_title=title, flat=False)
    dlg.show()


def open_conformation_viewer_from_blocks_payload(
    parent: QWidget | None,
    blocks_json_b64: str,
    *,
    title: str = "View Conformers",
    initial_superpose: bool = False,
    strain_overlay: dict | None = None,
    initial_conf_index: int = 0,
    export_parent_oid: int | None = None,
    export_confs_column: str = "confs",
) -> None:
    """Open the multi-conformer 3Dmol viewer (one-at-a-time and/or superpose) from packed mol blocks."""
    b = (blocks_json_b64 or "").strip()
    if not b:
        return
    n = 0
    try:
        inner = json.loads(base64.b64decode(b.encode("ascii")))
        if isinstance(inner, list):
            n = len(inner)
    except Exception:
        pass
    if n < 1:
        QMessageBox.warning(
            parent,
            title,
            "No conformers could be read from this cell for the 3D viewer.",
        )
        return
    strain_b64 = ""
    if strain_overlay:
        try:
            strain_b64 = base64.b64encode(
                json.dumps(strain_overlay, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
        except Exception:
            strain_b64 = ""
    dummy = Chem.MolFromSmiles("C")
    win_title = title if title else "View Conformers"
    if n > 1:
        win_title = f"{win_title} ({n} conformers)"
    dlg = Molecule3DViewerDialog(
        dummy,
        parent,
        window_title=win_title,
        flat=False,
        multi_conf_blocks_json_b64=b,
        multi_conf_initial_superpose=initial_superpose,
        multi_conf_strain_overlay_json_b64=strain_b64,
        multi_conf_initial_index=int(initial_conf_index),
        export_parent_oid=export_parent_oid,
        export_confs_column=export_confs_column,
        strain_overlay=strain_overlay,
    )
    dlg.show()


def open_molecule_2d_viewer(mol: Chem.Mol, parent: QWidget | None = None, *, title: str = "View in 2D") -> None:
    """Lay out *mol* in 2D and show it in 3Dmol with an orthographic (flat) projection."""
    if mol is None or not isinstance(mol, Chem.Mol):
        return
    m2d = prepare_mol_2d(mol)
    if m2d is None:
        QMessageBox.warning(
            parent,
            title,
            "Could not compute a 2D layout for this structure.",
        )
        return
    dlg = Molecule3DViewerDialog(m2d, parent, window_title=title, flat=True)
    dlg.show()
