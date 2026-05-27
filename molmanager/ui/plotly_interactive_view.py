"""Embedded Plotly view with table-linked point selection (shared by Plotter and PCA/t-SNE)."""

from __future__ import annotations

import json
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from plotly import graph_objects as go
from plotly.offline import get_plotlyjs

if TYPE_CHECKING:
    from .main_window import ChemicalTableApp


class _PlotBridge(QObject):
    def __init__(self, view: "PlotlyInteractiveView") -> None:
        super().__init__(view)
        self._view = view

    @pyqtSlot(int)
    def pointClicked(self, point_index: int) -> None:  # noqa: N802
        self._view._on_plot_point_clicked(int(point_index))

    @pyqtSlot(str)
    def pointsSelected(self, points_json: str) -> None:  # noqa: N802
        self._view._on_plot_points_selected(points_json)

    @pyqtSlot(int)
    def radarTraceClicked(self, trace_index: int) -> None:  # noqa: N802
        handler = getattr(self._view, "_on_radar_trace_clicked", None)
        if callable(handler):
            handler(int(trace_index))


class PlotlyInteractiveView(QWidget):
    """Plotly scatter with lasso/click selection synced to the compound table."""

    def __init__(self, parent_app: ChemicalTableApp | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parent_app = parent_app
        self._plot_shell_path = Path(tempfile.gettempdir()) / f"MOLMANAGER_plot_shell_{id(self)}.html"
        self._last_browser_opened_path: str | None = None
        self.plotted_oids: list[int] = []
        self._selected_point_indices: set[int] = set()
        self._web_ready = False
        self._pending_payload_json: str | None = None
        self._ignore_plot_clear_until: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        from PyQt5.QtWebEngineWidgets import QWebEngineView

        self.web = QWebEngineView(self)
        self.web.setMinimumHeight(220)
        self.web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.web, 1)

        self._bridge = _PlotBridge(self)
        self._web_channel = QWebChannel(self.web.page())
        self._web_channel.registerObject("chemBridge", self._bridge)
        self.web.page().setWebChannel(self._web_channel)
        self.web.loadFinished.connect(self._on_web_load_finished)
        self._load_plot_shell()

    def push_figure(self, fig: go.Figure, oids: list[int]) -> None:
        """Display a figure and map point indices to table OIDs."""
        self.plotted_oids = list(oids)
        self._selected_point_indices = {
            i for i in self._selected_point_indices if 0 <= i < len(self.plotted_oids)
        }
        from .plot_table_sync import (
    apply_table_selection_for_source_rows,
    clear_table_selection_from_plot,
    point_indices_for_oids as _point_indices_for_oids,
    source_rows_for_point_indices,
)
from .plotly_html import figure_payload_json

        self._pending_payload_json = figure_payload_json(fig)
        self._last_browser_opened_path = None
        if self._web_ready:
            self._apply_pending_payload()
            QTimer.singleShot(0, self.sync_from_table_selection)

    def point_indices_for_oids(self, oids: set[int] | frozenset[int]) -> set[int]:
        """Map table OIDs to scatter point indices in the current figure."""
        return _point_indices_for_oids(self.plotted_oids, oids)

    def select_oids(self, oids: set[int] | frozenset[int] | list[int]) -> int:
        """Select table rows (and plot points) for the given OIDs. Returns rows selected."""
        oid_set = {int(o) for o in oids}
        indices = sorted(self.point_indices_for_oids(oid_set))
        if not indices or self.parent_app is None:
            return 0
        self._selected_point_indices = set(indices)
        self._arm_ignore_plot_clear()
        self._select_rows_for_point_indices(indices)
        self.sync_selection_visual()
        return len(indices)

    def sync_from_table_selection(self) -> None:
        """Highlight plot points for the current table row selection."""
        if not self.plotted_oids or self.parent_app is None:
            return
        selected = self.parent_app._selected_oids_set()
        self._selected_point_indices = _point_indices_for_oids(self.plotted_oids, selected)
        self._arm_ignore_plot_clear()
        self.sync_selection_visual()

    def sync_selection_visual(self) -> None:
        if not self._web_ready:
            return
        idxs = sorted(self._selected_point_indices)
        js_arg = json.dumps(idxs)
        self.web.page().runJavaScript(f"window.molmanagerSetSelection({js_arg});")

    def clear_table_selection(self, *, update_plot: bool = True) -> None:
        self._selected_point_indices = set()
        self._ignore_plot_clear_until = 0.0
        if self.parent_app is not None:
            clear_table_selection_from_plot(self.parent_app)
        if update_plot:
            self.sync_selection_visual()

    def _load_plot_shell(self) -> None:
        plotly_js = get_plotlyjs().replace(":focus-visible", ":focus").replace("</script>", "<\\/script>")
        shell_html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>html, body, #plot {{ width: 100%; height: 100%; margin: 0; }}</style>
</head>
<body>
  <div id="plot"></div>
  <script>{plotly_js}</script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <script>
    (function() {{
      var gd = document.getElementById('plot');
      var bridge = null;
      try {{
        new QWebChannel(qt.webChannelTransport, function(channel) {{
          bridge = channel.objects.chemBridge || null;
        }});
      }} catch (_e) {{}}
      var suppressPlotDeselect = false;
      var lastNonemptyPlotSelection = 0;
      function clearSelectionShapes() {{
        try {{
          if (!gd || !gd.layout) return;
          Plotly.relayout(gd, {{selections: []}});
        }} catch (_clrSel) {{}}
      }}
      function scheduleClearSelectionShapes() {{
        setTimeout(clearSelectionShapes, 0);
        try {{
          requestAnimationFrame(function() {{
            requestAnimationFrame(clearSelectionShapes);
          }});
        }} catch (_raf) {{
          setTimeout(clearSelectionShapes, 16);
        }}
      }}
      window.molmanagerSetSelection = function(indicesJson) {{
        try {{
          var idxs = JSON.parse(indicesJson || "[]");
          if (!Array.isArray(idxs)) idxs = [];
          if (!gd || !gd.data || !gd.data.length) return;
          var selTraces = [0];
          try {{
            var metaTr = gd.layout && gd.layout.meta && gd.layout.meta.molmanager_selection_traces;
            if (Array.isArray(metaTr) && metaTr.length) selTraces = metaTr;
          }} catch (_meta) {{}}
          var main = gd.data[0];
          if (main.type === "scatter3d") {{
            var sx = [], sy = [], sz = [];
            var x0 = main.x, y0 = main.y, z0 = main.z;
            for (var j = 0; j < idxs.length; j++) {{
              var ii = idxs[j];
              if (ii >= 0 && ii < x0.length) {{
                sx.push(x0[ii]); sy.push(y0[ii]); sz.push(z0[ii]);
              }}
            }}
            if (gd.data.length > 1) {{
              if (sx.length) {{
                Plotly.restyle(gd, {{x: [sx], y: [sy], z: [sz]}}, [1]);
              }} else {{
                Plotly.deleteTraces(gd, [1]);
              }}
            }} else if (sx.length) {{
              Plotly.addTraces(gd, {{
                type: "scatter3d", x: sx, y: sy, z: sz, mode: "markers",
                marker: {{size: 7, opacity: 1.0, color: "#d62828"}},
                name: "Selected", showlegend: false
              }});
            }}
            return;
          }}
          if (!idxs.length) {{
            var clearPatch = {{selectedpoints: [], "unselected.marker.opacity": []}};
            for (var ci = 0; ci < selTraces.length; ci++) {{
              clearPatch.selectedpoints.push(null);
              clearPatch["unselected.marker.opacity"].push(0.85);
            }}
            Plotly.restyle(gd, clearPatch, selTraces);
            clearSelectionShapes();
            return;
          }}
          var selPatch = {{selectedpoints: [], "unselected.marker.opacity": []}};
          for (var si = 0; si < selTraces.length; si++) {{
            selPatch.selectedpoints.push(idxs);
            selPatch["unselected.marker.opacity"].push(0.35);
          }}
          Plotly.restyle(gd, selPatch, selTraces);
          clearSelectionShapes();
        }} catch (_selVis) {{}}
      }};
      window.molmanagerApply = function(payloadJson) {{
        try {{
          var payload = JSON.parse(payloadJson);
          var data = payload.data || [];
          var layout = payload.layout || {{}};
          var config = payload.config || {{}};
          suppressPlotDeselect = true;
          Plotly.react(gd, data, layout, config).then(function() {{
            try {{
              gd.removeAllListeners('plotly_click');
              gd.removeAllListeners('plotly_selected');
              gd.removeAllListeners('plotly_deselect');
            }} catch (_l) {{}}
            var selTracesClick = [0];
            try {{
              var metaClick = layout.meta && layout.meta.molmanager_selection_traces;
              if (Array.isArray(metaClick) && metaClick.length) selTracesClick = metaClick;
            }} catch (_metaClick) {{}}
            function traceSelectable(cn) {{
              for (var k = 0; k < selTracesClick.length; k++) {{
                if (selTracesClick[k] === cn) return true;
              }}
              return false;
            }}
            gd.on('plotly_click', function(ev) {{
              try {{
                if (!ev || !ev.points || !ev.points.length || !gd.data || !gd.data.length) return;
                var pt = ev.points[0];
                var trace = gd.data[pt.curveNumber];
                if (trace && trace.type === "scatterpolar") {{
                  if (bridge && bridge.radarTraceClicked) {{
                    var cn = Number(pt.curveNumber);
                    if (Number.isFinite(cn)) bridge.radarTraceClicked(cn);
                  }}
                  return;
                }}
                if (!bridge || !bridge.pointClicked) return;
                if (!traceSelectable(pt.curveNumber)) return;
                var pn = Number(pt.pointNumber);
                if (Number.isFinite(pn)) bridge.pointClicked(pn);
              }} catch (_clickErr) {{}}
            }});
            gd.on('plotly_selected', function(ev) {{
              try {{
                scheduleClearSelectionShapes();
                if (!bridge || !bridge.pointsSelected) return;
                var idxs = [];
                if (ev && ev.points && ev.points.length) {{
                  for (var i = 0; i < ev.points.length; i++) {{
                    var pt = ev.points[i];
                    if (!traceSelectable(pt.curveNumber)) continue;
                    var pn = Number(pt.pointNumber);
                    if (Number.isFinite(pn)) idxs.push(pn);
                  }}
                }}
                if (!idxs.length) return;
                lastNonemptyPlotSelection = Date.now();
                bridge.pointsSelected(JSON.stringify(idxs));
              }} catch (_selErr) {{}}
            }});
            gd.on('plotly_deselect', function() {{
              try {{
                scheduleClearSelectionShapes();
                if (suppressPlotDeselect) return;
                if (Date.now() - lastNonemptyPlotSelection < 450) return;
                if (bridge && bridge.pointsSelected) bridge.pointsSelected("[]");
              }} catch (_deselErr) {{}}
            }});
          }}).finally(function() {{
            setTimeout(function() {{ suppressPlotDeselect = false; }}, 200);
          }});
        }} catch (e) {{
          console.error('molmanager Plotly embed failed:', e);
        }}
      }};
    }})();
  </script>
</body>
</html>"""
        self._plot_shell_path.write_text(shell_html, encoding="utf-8")
        self.web.load(QUrl.fromLocalFile(str(self._plot_shell_path)))

    def _apply_pending_payload(self) -> None:
        if not self._web_ready or not self._pending_payload_json:
            return
        js_arg = json.dumps(self._pending_payload_json)
        self.web.page().runJavaScript(f"window.molmanagerApply({js_arg});")
        self._arm_ignore_plot_clear()
        QTimer.singleShot(300, self.sync_from_table_selection)

    def _on_web_load_finished(self, ok: bool) -> None:
        if not ok:
            self._fallback_open_in_browser("Plot view failed to load in embedded renderer.")
            return

        def _after_probe(result) -> None:
            if not bool(result):
                self._fallback_open_in_browser("Embedded Plotly renderer is not supported on this system.")
                return
            self._web_ready = True
            self._apply_pending_payload()
            QTimer.singleShot(0, self.sync_from_table_selection)

        QTimer.singleShot(
            0,
            lambda: self.web.page().runJavaScript(
                "typeof window.Plotly !== 'undefined' && typeof window.molmanagerApply === 'function'",
                _after_probe,
            ),
        )

    def _fallback_open_in_browser(self, reason: str) -> None:
        if self.parent_app is None:
            return
        path = str(self._plot_shell_path)
        if self._last_browser_opened_path == path:
            return
        self._last_browser_opened_path = path
        webbrowser.open(self._plot_shell_path.as_uri())
        self.parent_app.status_label.setText(f"Plot fallback: opened in browser ({reason})")

    def _arm_ignore_plot_clear(self, ms: int = 500) -> None:
        self._ignore_plot_clear_until = time.monotonic() + (ms / 1000.0)

    def _select_rows_for_point_indices(self, point_indices: list[int]) -> None:
        if self.parent_app is None:
            return
        source_rows = source_rows_for_point_indices(self.parent_app, self.plotted_oids, point_indices)
        apply_table_selection_for_source_rows(self.parent_app, source_rows)

    def _on_plot_point_clicked(self, point_index: int) -> None:
        if point_index < 0 or self.parent_app is None:
            return
        self._selected_point_indices = {int(point_index)}
        self._arm_ignore_plot_clear()
        self._select_rows_for_point_indices([int(point_index)])
        self.sync_selection_visual()
        if 0 <= point_index < len(self.plotted_oids):
            oid = int(self.plotted_oids[point_index])
            row = self.parent_app.get_row_by_id(oid)
            if row >= 0:
                self.parent_app.status_label.setText(f"Plot: selected row {row + 1:,} (OID {oid}).")

    def _on_plot_points_selected(self, points_json: str) -> None:
        if self.parent_app is None:
            return
        try:
            raw = json.loads(points_json or "[]")
            idxs = [int(x) for x in raw if isinstance(x, (int, float))]
        except Exception:
            idxs = []
        if not idxs:
            if time.monotonic() < self._ignore_plot_clear_until:
                return
            self.clear_table_selection()
            self.parent_app.status_label.setText("Plot: selection cleared.")
            return
        self._selected_point_indices = {i for i in idxs if 0 <= i < len(self.plotted_oids)}
        if not self._selected_point_indices:
            return
        self._arm_ignore_plot_clear()
        sel_sorted = sorted(self._selected_point_indices)
        self._select_rows_for_point_indices(sel_sorted)
        self.parent_app.status_label.setText(f"Plot: selected {len(sel_sorted):,} point(s).")
        self.sync_selection_visual()
