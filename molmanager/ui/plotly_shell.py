"""Shared Qt WebEngine shell for interactive Plotly (Plotter + PlotlyInteractiveView)."""

from __future__ import annotations

from pathlib import Path

from plotly.offline import get_plotlyjs


def sanitized_plotly_js() -> str:
    """Plotly.js safe for embedding in HTML (Qt/Chromium quirks)."""
    return get_plotlyjs().replace(":focus-visible", ":focus").replace("</script>", "<\\/script>")


def interactive_plot_shell_html() -> str:
    """HTML document with Plotly, QWebChannel bridge, selection, and Plotter-specific click handlers."""
    plotly_js = sanitized_plotly_js()
    return f"""<!doctype html>
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
      var applyInFlight = false;
      var pendingSelectionJson = null;
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
      function selectionTracesFromLayout() {{
        var selTraces = [0];
        try {{
          var metaTr = gd.layout && gd.layout.meta && gd.layout.meta.molmanager_selection_traces;
          if (Array.isArray(metaTr) && metaTr.length) selTraces = metaTr;
        }} catch (_meta) {{}}
        return selTraces;
      }}
      function parseSelectionIndices(raw) {{
        if (Array.isArray(raw)) return raw;
        if (typeof raw === "string") {{
          try {{
            var parsed = JSON.parse(raw || "[]");
            return Array.isArray(parsed) ? parsed : [];
          }} catch (_parse) {{
            return [];
          }}
        }}
        return [];
      }}
      function findSelectedOverlayTraceIndex() {{
        for (var ti = 1; ti < gd.data.length; ti++) {{
          if (gd.data[ti] && gd.data[ti].name === "Selected") return ti;
        }}
        return -1;
      }}
      function applySelectionIndices(indicesJson) {{
        try {{
          var idxs = parseSelectionIndices(indicesJson);
          if (!gd || !gd.data || !gd.data.length) return;
          var selTraces = selectionTracesFromLayout();
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
          if (main.type === "scatter" || main.type === "scattergl") {{
            var sx = [], sy = [];
            var x0 = main.x || [], y0 = main.y || [];
            for (var j = 0; j < idxs.length; j++) {{
              var ii = idxs[j];
              if (ii >= 0 && ii < x0.length) {{
                sx.push(x0[ii]); sy.push(y0[ii]);
              }}
            }}
            var overlayIdx = findSelectedOverlayTraceIndex();
            if (sx.length) {{
              var overlay = {{
                type: main.type,
                x: sx,
                y: sy,
                mode: "markers",
                marker: {{size: 10, color: "#d62828", opacity: 1.0, line: {{width: 1, color: "#8b0000"}}}},
                name: "Selected",
                showlegend: false,
                hoverinfo: "skip",
              }};
              if (overlayIdx >= 0) {{
                Plotly.restyle(gd, {{x: [sx], y: [sy]}}, [overlayIdx]);
              }} else {{
                Plotly.addTraces(gd, overlay);
              }}
              var dimPatch = {{"unselected.marker.opacity": [], selectedpoints: []}};
              for (var di = 0; di < selTraces.length; di++) {{
                dimPatch["unselected.marker.opacity"].push(0.35);
                dimPatch.selectedpoints.push([]);
              }}
              Plotly.restyle(gd, dimPatch, selTraces);
            }} else {{
              if (overlayIdx >= 0) Plotly.deleteTraces(gd, [overlayIdx]);
              var clearPatch = {{selectedpoints: [], "unselected.marker.opacity": []}};
              for (var ci = 0; ci < selTraces.length; ci++) {{
                clearPatch.selectedpoints.push(null);
                clearPatch["unselected.marker.opacity"].push(0.85);
              }}
              Plotly.restyle(gd, clearPatch, selTraces);
            }}
            clearSelectionShapes();
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
      }}
      function flushPendingSelection() {{
        if (pendingSelectionJson === null) return;
        var ps = pendingSelectionJson;
        pendingSelectionJson = null;
        applySelectionIndices(ps);
      }}
      window.molmanagerSetSelection = function(indicesJson) {{
        if (applyInFlight) {{
          pendingSelectionJson = indicesJson;
          return;
        }}
        applySelectionIndices(indicesJson);
      }};
      window.molmanagerApply = function(payloadJson) {{
        try {{
          var payload = JSON.parse(payloadJson);
          var data = payload.data || [];
          var layout = payload.layout || {{}};
          var config = payload.config || {{}};
          suppressPlotDeselect = true;
          applyInFlight = true;
          pendingSelectionJson = null;
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
                if (trace && trace.type === "heatmap") {{
                  if (bridge && bridge.heatmapCellClicked) {{
                    var xv = Number(pt.x);
                    var yv = Number(pt.y);
                    if (Number.isFinite(xv) && Number.isFinite(yv)) bridge.heatmapCellClicked(xv, yv);
                  }}
                  return;
                }}
                if (trace && trace.type === "histogram") {{
                  if (bridge && bridge.histogramPointsSelected) {{
                    var nums = pt.pointNumbers;
                    if (!Array.isArray(nums) || !nums.length) {{
                      if (pt.pointNumber != null && pt.pointNumber !== undefined) {{
                        nums = [pt.pointNumber];
                      }}
                    }}
                    if (Array.isArray(nums) && nums.length) {{
                      bridge.histogramPointsSelected(JSON.stringify(nums));
                      return;
                    }}
                  }}
                  if (bridge && bridge.histogramBinClicked) {{
                    var bn = Number(pt.pointNumber);
                    if (Number.isFinite(bn)) bridge.histogramBinClicked(bn);
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
            applyInFlight = false;
            flushPendingSelection();
          }}).finally(function() {{
            applyInFlight = false;
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


def write_interactive_plot_shell(path: Path) -> None:
    path.write_text(interactive_plot_shell_html(), encoding="utf-8")
