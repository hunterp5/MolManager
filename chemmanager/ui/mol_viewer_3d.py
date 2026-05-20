"""Interactive molecular viewers using RDKit + bundled 3Dmol.js (3D conformers or 2D coordinates, offline)."""

from __future__ import annotations

import base64
import json
import logging
import shutil
from pathlib import Path

from PyQt5.QtCore import QTemporaryDir, QUrl, Qt
from PyQt5.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from rdkit import Chem
from rdkit.Chem import AllChem

from ..confs_codec import conformer_mol_blocks_b64_json
from .qt_widget_utils import make_window_minimizable

logger = logging.getLogger(__name__)

# Vendored build (https://3dmol.org — BSD). See chemmanager/ui/static/3Dmol-min.js
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
    """JavaScript to create a 3Dmol viewer with atom click / hover (native 3Dmol atom specs)."""
    flat_js = "true" if flat else "false"
    # Placeholders avoid f-string brace conflicts with large JS object literals.
    tmpl = r"""  <script>
    function chemmanagerInitView() {
      try {
        const data = atob("__MOLB64__");
        const flat = __FLAT__;
        const opts = flat ? { backgroundColor: "white", orthographic: true } : { backgroundColor: "white" };
        const viewer = $3Dmol.createViewer("v", opts);
        viewer.addModel(data, "mol");

        function atomLines(atom) {
          if (!atom) return "";
          var L = [];
          if (atom.elem !== undefined) L.push("Element: " + atom.elem);
          if (atom.atom !== undefined) L.push("Atom name: " + atom.atom);
          if (atom.serial !== undefined) L.push("Serial: " + atom.serial);
          if (atom.index !== undefined) L.push("Index: " + atom.index);
          if (atom.chain !== undefined) L.push("Chain: " + atom.chain);
          if (atom.resn !== undefined) {
            L.push("Residue: " + atom.resn + (atom.resi !== undefined ? " " + atom.resi : ""));
          }
          if (atom.x !== undefined && atom.y !== undefined && atom.z !== undefined) {
            L.push("x: " + Number(atom.x).toFixed(4) + "  y: " + Number(atom.y).toFixed(4) + "  z: " + Number(atom.z).toFixed(4));
          }
          if (atom.formalCharge !== undefined && atom.formalCharge !== 0) {
            L.push("Formal charge: " + atom.formalCharge);
          }
          return L.join("\n");
        }

        function selSpec(atom) {
          if (!atom) return {};
          if (atom.serial !== undefined && atom.serial !== null) return { serial: atom.serial };
          if (atom.index !== undefined) return { index: atom.index };
          return {};
        }

        function baseRadii() {
          return { stickR: flat ? 0.1 : 0.12, sph: flat ? 0.18 : 0.22 };
        }

        function applyInteractiveBase() {
          var br = baseRadii();
          /* Picking uses atom.clickable (setClickable), not setStyle — top-level clickable in setStyle does not enable hits. */
          viewer.setStyle({}, {
            stick: { radius: br.stickR },
            sphere: { scale: br.sph }
          });
        }

        function applyAtomInteractivity() {
          viewer.setClickable({}, true, onAtomPick);
          viewer.setHoverable({}, true, onAtomHover, onAtomUnhover);
        }

        function onAtomUnhover() {
          var h = document.getElementById("chem-atom-hover");
          if (h) h.textContent = "";
        }

        function onAtomHover(atom) {
          var h = document.getElementById("chem-atom-hover");
          if (!h) return;
          if (!atom) { h.textContent = ""; return; }
          var t = atom.elem || "?";
          if (atom.serial !== undefined && atom.serial !== null) t += " serial " + atom.serial;
          else if (atom.index !== undefined) t += " index " + atom.index;
          h.textContent = "Hover: " + t;
        }

        function onAtomPick(atom) {
          var d = document.getElementById("chem-atom-detail");
          if (d) d.textContent = atomLines(atom) || "(no atom data)";
          applyInteractiveBase();
          applyAtomInteractivity();
          var sel = selSpec(atom);
          var br = baseRadii();
          viewer.setStyle(sel, {
            stick: { radius: br.stickR * 1.45, color: "#c0392b" },
            sphere: { scale: br.sph * 1.35, color: "#c0392b" }
          });
          viewer.render();
        }

        function clearAtomSelection() {
          var d = document.getElementById("chem-atom-detail");
          if (d) d.textContent = "";
          applyInteractiveBase();
          applyAtomInteractivity();
          viewer.render();
        }

        function maybeDeselectBackgroundClick(ev) {
          if (!viewer.scene) return;
          if (ev.button !== undefined && ev.button !== 0) return;
          var x = viewer.getX(ev);
          var y = viewer.getY(ev);
          if (x === undefined || y === undefined) return;
          if (!viewer.isInViewer(x, y)) return;
          if (!viewer.closeEnoughForClick(ev)) return;
          var mouse = viewer.mouseXY(x, y);
          var pool = viewer.selectedAtoms({ clickable: true });
          var hits = viewer.targetedObjects(mouse.x, mouse.y, pool);
          if (hits.length === 0) clearAtomSelection();
        }

        applyInteractiveBase();
        applyAtomInteractivity();
        viewer.zoomTo();
        viewer.render();

        var pickEl = viewer.getCanvas && viewer.getCanvas();
        if (pickEl && pickEl.addEventListener) {
          pickEl.addEventListener("mouseup", function (ev) {
            window.setTimeout(function () { maybeDeselectBackgroundClick(ev); }, 0);
          });
        }
      } catch (e) {
        document.body.innerHTML = "<pre style='padding:12px;font-family:monospace'>3Dmol error: " + e + "</pre>";
      }
    }
  </script>"""
    return tmpl.replace("__MOLB64__", mol_b64).replace("__FLAT__", flat_js)


def _assemble_viewer_page(mol_b64: str, *, flat: bool, script_src: str) -> str:
    init = _viewer_init_script_fragment(mol_b64, flat=flat)
    help_html = _viewer_help_overlay_html()
    atom_panel = _atom_info_panel_html()
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
  <script src="{script_src}" onload="chemmanagerInitView()"></script>
""" + help_html + """
</body>
</html>"""


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


def _offline_index_html(mol_b64: str, *, flat: bool = False) -> str:
    """Minimal page next to ``3Dmol-min.js`` (same directory)."""
    return _assemble_viewer_page(mol_b64, flat=flat, script_src="3Dmol-min.js")


def _offline_index_html_multiconf(blocks_json_b64: str, *, initial_superpose: bool = False) -> str:
    return _assemble_viewer_page_multiconf(
        blocks_json_b64, script_src="3Dmol-min.js", initial_superpose=initial_superpose
    )


def _cdn_fallback_html(mol_b64: str, *, flat: bool = False) -> str:
    """Same as offline page but loads 3Dmol from the network (only if the bundle is missing)."""
    return _assemble_viewer_page(mol_b64, flat=flat, script_src="https://3dmol.org/build/3Dmol-min.js")


def _cdn_fallback_html_multiconf(blocks_json_b64: str, *, initial_superpose: bool = False) -> str:
    return _assemble_viewer_page_multiconf(
        blocks_json_b64,
        script_src="https://3dmol.org/build/3Dmol-min.js",
        initial_superpose=initial_superpose,
    )


def build_3dmol_html(mol_b64: str) -> str:
    """Return a self-contained HTML document (offline bundle when available, else CDN)."""
    return _offline_index_html(mol_b64, flat=False) if bundled_3dmol_available() else _cdn_fallback_html(mol_b64, flat=False)


def prepare_mol_3d(mol: Chem.Mol) -> Chem.Mol | None:
    """Return a copy of *mol* with 3D coordinates (ETKDG embed + MMFF/UFF), or ``None`` on failure."""
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    try:
        m = Chem.Mol(mol)
    except Exception:
        return None
    try:
        m = Chem.AddHs(m)
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
<div id="chem-conf-bar" style="position:fixed;left:50%;bottom:52px;transform:translateX(-50%);width:min(480px,calc(100vw - 20px));z-index:22;pointer-events:auto;box-sizing:border-box;display:flex;flex-direction:column;gap:8px;padding:10px 12px;font:12px/1.35 system-ui,Segoe UI,sans-serif;background:rgba(255,255,255,0.97);border:1px solid #c0c0c0;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.14);">
  <div style="display:flex;flex-wrap:wrap;align-items:center;column-gap:12px;row-gap:6px;width:100%;">
    <span style="font-weight:600;color:#222;">Conformers</span>
    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;"><input type="radio" name="chem-conf-view" id="chem-view-one" checked="checked"/> One at a time</label>
    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;"><input type="radio" name="chem-conf-view" id="chem-view-super"/> Superpose all</label>
    <span id="chem-conf-label" style="margin-left:auto;font-weight:600;color:#333;white-space:nowrap;padding-left:8px;"></span>
  </div>
  <div id="chem-conf-nav" style="display:flex;align-items:center;gap:8px;width:100%;box-sizing:border-box;">
    <button type="button" id="chem-conf-prev" style="flex:0 0 auto;padding:5px 12px;cursor:pointer;font:inherit;">Prev</button>
    <input type="range" id="chem-conf-slider" min="0" max="0" value="0" step="1" style="flex:1 1 auto;min-width:0;width:0;height:22px;cursor:pointer;"/>
    <button type="button" id="chem-conf-next" style="flex:0 0 auto;padding:5px 12px;cursor:pointer;font:inherit;">Next</button>
  </div>
</div>
"""


def _viewer_init_script_multiconf(blocks_json_b64: str, *, initial_superpose: bool = False) -> str:
    """Multi-conformer 3Dmol page: one-at-a-time vs superpose-all (no atom pick panel; mouse = rotate/zoom)."""
    init_sp = "true" if initial_superpose else "false"
    tmpl = r"""  <script>
    function chemmanagerInitView() {
      try {
        var initialSuperpose = __INIT_SP__;
        const blocks = JSON.parse(atob("__BLOCKSJSONB64__"));
        if (!blocks || blocks.length === 0) {
          document.body.innerHTML = "<pre style='padding:12px'>No conformers to display.</pre>";
          return;
        }
        if (blocks.length < 2) initialSuperpose = false;
        const viewer = $3Dmol.createViewer("v", { backgroundColor: "white" });
        var curIdx = 0;
        var superposed = false;
        var palette = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#f39c12", "#16a085", "#d35400", "#34495e"];

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
          loadConf(0);
        }
      } catch (e) {
        document.body.innerHTML = "<pre style='padding:12px;font-family:monospace'>3Dmol error: " + e + "</pre>";
      }
    }
  </script>"""
    return tmpl.replace("__BLOCKSJSONB64__", blocks_json_b64).replace("__INIT_SP__", init_sp)


def _assemble_viewer_page_multiconf(
    blocks_json_b64: str, *, script_src: str, initial_superpose: bool = False
) -> str:
    init = _viewer_init_script_multiconf(blocks_json_b64, initial_superpose=initial_superpose)
    help_html = _viewer_help_overlay_html()
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
  <script src="{script_src}" onload="chemmanagerInitView()"></script>
""" + help_html + """
</body>
</html>"""


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
    ):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle(window_title)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(920, 720)

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
                        ),
                        QUrl("https://3dmol.org/"),
                    )
                else:
                    web.setHtml(_cdn_fallback_html(mol_b64, flat=flat), QUrl("https://3dmol.org/"))
            root.addWidget(web, 1)
        except Exception as e:
            web = None
            err = f"{type(e).__name__}: {e}"
            logger.warning("Embedded 3Dmol viewer unavailable: %s", err, exc_info=True)
            msg = (
                "The embedded viewer could not start.\n\n"
                f"{err}\n\n"
                "Typical fixes:\n"
                "• Install PyQtWebEngine with the same major.minor version as PyQt5 (e.g. both 5.15.x).\n"
                "• Start ChemManager with `python -m chemmanager` so QtWebEngine loads before the GUI initializes.\n\n"
                "Close this window when you are done."
            )
            root.addWidget(QLabel(msg), 1)

        make_window_minimizable(self)


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
