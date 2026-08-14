# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager. If not, see <https://www.gnu.org/licenses/>.

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
  <style>
    html, body, #plot {{ width: 100%; height: 100%; margin: 0; }}
    #mol-hover-layer {{
      position: absolute;
      left: 0; top: 0;
      width: 0;
      height: 0;
      overflow: visible;
      pointer-events: none;
      z-index: 40;
    }}
    #mol-hover-connectors {{
      position: absolute;
      left: 0; top: 0;
      width: 100vw;
      height: 100vh;
      overflow: visible;
      pointer-events: none;
    }}
    #mol-hover-connectors line {{
      stroke: #5a5a5a;
      stroke-width: 1.25;
      stroke-opacity: 0.85;
    }}
    .mol-hover-card {{
      position: absolute;
      max-width: 240px;
      max-height: min(42vh, 320px);
      overflow: auto;
      pointer-events: none;
      background: rgba(255,255,255,0.97);
      border: 1px solid #c8c8c8;
      border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.18);
      padding: 8px 10px;
      font: 12px/1.35 system-ui, Segoe UI, sans-serif;
      color: #222;
    }}
    .mol-hover-card img {{
      display: block;
      max-width: 140px;
      max-height: 116px;
      margin: 0 auto 6px auto;
      background: #fff;
      border: 1px solid #ddd;
      border-radius: 4px;
    }}
    .mol-hover-card .lines {{ white-space: pre-wrap; word-break: break-word; }}
    .mol-hover-overflow {{
      position: absolute;
      background: rgba(255,255,255,0.95);
      border: 1px solid #c8c8c8;
      border-radius: 6px;
      padding: 4px 8px;
      font: 11px/1.3 system-ui, Segoe UI, sans-serif;
      color: #444;
      box-shadow: 0 1px 4px rgba(0,0,0,0.12);
    }}
  </style>
</head>
<body>
  <div id="plot"></div>
  <div id="mol-hover-layer">
    <svg id="mol-hover-connectors" xmlns="http://www.w3.org/2000/svg"></svg>
  </div>
  <script>{plotly_js}</script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <script>
    (function() {{
      var gd = document.getElementById('plot');
      var hoverLayer = document.getElementById('mol-hover-layer');
      var hoverSvg = document.getElementById('mol-hover-connectors');
      var bridge = null;
      try {{
        new QWebChannel(qt.webChannelTransport, function(channel) {{
          bridge = channel.objects.chemBridge || null;
        }});
      }} catch (_e) {{}}
      var suppressPlotBridge = false;
      var suppressPlotBridgeGen = 0;
      var lastNonemptyPlotSelection = 0;
      var applyInFlight = false;
      var pendingSelectionJson = null;
      var hoverPersist = false;
      var hoverPinned = false;
      var hoverReqGen = 0;
      var shiftHeld = false;
      var lastHoverAnchor = {{x: 16, y: 16}};
      var SVG_NS = "http://www.w3.org/2000/svg";
      function refreshShiftHeld(ev) {{
        try {{
          if (ev && typeof ev.shiftKey === "boolean") shiftHeld = !!ev.shiftKey;
        }} catch (_sh) {{}}
      }}
      function isAdditiveEvent(ev) {{
        try {{
          if (ev && ev.shiftKey) return true;
        }} catch (_a0) {{}}
        return !!shiftHeld;
      }}
      window.addEventListener('keydown', function(e) {{
        if (e.key === 'Shift') shiftHeld = true;
      }});
      window.addEventListener('keyup', function(e) {{
        if (e.key === 'Shift') shiftHeld = false;
      }});
      window.addEventListener('mousedown', function(e) {{ refreshShiftHeld(e); }}, true);
      window.addEventListener('mousemove', function(e) {{
        if (e && e.buttons) refreshShiftHeld(e);
      }}, true);
      window.addEventListener('mouseup', function(e) {{ refreshShiftHeld(e); }}, true);
      // Do not clear shiftHeld on blur — Qt focus steals otherwise break Shift+lasso.
      function clearSelectionShapes() {{
        try {{
          if (!gd || !gd.layout) return;
          Plotly.relayout(gd, {{selections: []}});
        }} catch (_clrSel) {{}}
      }}
      function beginSuppressPlotBridge(ms) {{
        // Generation token so an earlier timeout cannot clear a later suppress window.
        suppressPlotBridge = true;
        var hold = (typeof ms === "number" && ms >= 0) ? ms : 250;
        var gen = ++suppressPlotBridgeGen;
        setTimeout(function() {{
          if (gen === suppressPlotBridgeGen) suppressPlotBridge = false;
        }}, hold);
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
      function axisForPoint(pt, which) {{
        try {{
          if (pt && pt[which]) return pt[which];
        }} catch (_a0) {{}}
        try {{
          var full = gd && gd._fullLayout;
          if (!full) return null;
          return which === "xaxis" ? full.xaxis : full.yaxis;
        }} catch (_a1) {{
          return null;
        }}
      }}
      function dataPointToPageXY(pt) {{
        try {{
          if (pt && pt.bbox && typeof pt.bbox.x0 === "number" && typeof pt.bbox.y0 === "number") {{
            return {{
              x: (pt.bbox.x0 + pt.bbox.x1) / 2,
              y: (pt.bbox.y0 + pt.bbox.y1) / 2
            }};
          }}
        }} catch (_bb) {{}}
        try {{
          var main = gd && gd.data && gd.data[0];
          if (main && main.type === "scatter3d") return null;
          var xa = axisForPoint(pt, "xaxis");
          var ya = axisForPoint(pt, "yaxis");
          if (xa && ya && typeof xa.l2p === "function" && typeof ya.l2p === "function"
              && pt && pt.x != null && pt.y != null) {{
            var xp = (xa._offset || 0) + xa.l2p(pt.x);
            var yp = (ya._offset || 0) + ya.l2p(pt.y);
            var rect = gd.getBoundingClientRect();
            return {{ x: rect.left + xp, y: rect.top + yp }};
          }}
        }} catch (_l2p) {{}}
        return null;
      }}
      function indexToPageXY(idx) {{
        try {{
          if (!gd || !gd.data || !gd.data.length) return null;
          var main = gd.data[0];
          if (!main || main.type === "scatter3d") return null;
          var xa = gd._fullLayout && gd._fullLayout.xaxis;
          var ya = gd._fullLayout && gd._fullLayout.yaxis;
          if (!xa || !ya || typeof xa.l2p !== "function" || typeof ya.l2p !== "function") return null;
          var ii = Number(idx);
          if (!Number.isFinite(ii) || ii < 0 || ii >= main.x.length) return null;
          var xv = main.x[ii], yv = main.y[ii];
          if (xv == null || yv == null) return null;
          var rect = gd.getBoundingClientRect();
          return {{
            x: rect.left + (xa._offset || 0) + xa.l2p(xv),
            y: rect.top + (ya._offset || 0) + ya.l2p(yv)
          }};
        }} catch (_ix) {{
          return null;
        }}
      }}
      function clearHoverCards(force) {{
        if (hoverPinned && !force) return;
        if (!hoverLayer || !hoverSvg) return;
        var cards = hoverLayer.querySelectorAll('.mol-hover-card, .mol-hover-overflow');
        for (var i = 0; i < cards.length; i++) cards[i].remove();
        while (hoverSvg.firstChild) hoverSvg.removeChild(hoverSvg.firstChild);
      }}
      function hideHoverCard(force) {{
        clearHoverCards(force);
      }}
      function escapeHtml(s) {{
        return String(s == null ? "" : s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      }}
      var POINT_CLEAR_PX = 16;
      var CARD_GAP_PX = 8;
      function clampCardPosition(x, y, w, h) {{
        var maxX = Math.max(8, window.innerWidth - w - 8);
        var maxY = Math.max(8, window.innerHeight - h - 8);
        return {{
          x: Math.min(Math.max(8, x), maxX),
          y: Math.min(Math.max(8, y), maxY)
        }};
      }}
      function rectCoversPoint(x, y, w, h, px, py, pad) {{
        var p = (typeof pad === "number") ? pad : POINT_CLEAR_PX;
        return px >= (x - p) && px <= (x + w + p) && py >= (y - p) && py <= (y + h + p);
      }}
      function rectHitsAnyPoint(x, y, w, h, points, pad) {{
        if (!points || !points.length) return false;
        for (var i = 0; i < points.length; i++) {{
          var pt = points[i];
          if (!pt) continue;
          if (rectCoversPoint(x, y, w, h, pt.x, pt.y, pad)) return true;
        }}
        return false;
      }}
      function rectsOverlap(a, b, gap) {{
        var g = (typeof gap === "number") ? gap : CARD_GAP_PX;
        return !(a.x + a.w + g <= b.x || b.x + b.w + g <= a.x
          || a.y + a.h + g <= b.y || b.y + b.h + g <= a.y);
      }}
      function cardCandidateOrigins(px, py, w, h, index) {{
        // Prefer placing the card fully beside/above/below the point with clearance.
        var dirs = [
          [1, 0], [-1, 0], [0, -1], [0, 1],
          [1, -1], [1, 1], [-1, -1], [-1, 1],
          [0.6, -1], [-0.6, -1], [0.6, 1], [-0.6, 1],
          [1, -0.6], [1, 0.6], [-1, -0.6], [-1, 0.6]
        ];
        var distances = [18, 32, 48, 68, 92, 120, 156, 200, 250];
        var start = (index || 0) % dirs.length;
        var out = [];
        for (var di = 0; di < distances.length; di++) {{
          var dist = distances[di];
          for (var k = 0; k < dirs.length; k++) {{
            var d = dirs[(start + k) % dirs.length];
            var x, y;
            if (d[0] > 0.15) x = px + dist;
            else if (d[0] < -0.15) x = px - dist - w;
            else x = px - w / 2;
            if (d[1] > 0.15) y = py + dist;
            else if (d[1] < -0.15) y = py - dist - h;
            else y = py - h / 2;
            out.push({{x: x, y: y}});
          }}
        }}
        return out;
      }}
      function cornerAwayFromPoints(w, h, points) {{
        var corners = [
          {{x: 8, y: 8}},
          {{x: Math.max(8, window.innerWidth - w - 8), y: 8}},
          {{x: 8, y: Math.max(8, window.innerHeight - h - 8)}},
          {{x: Math.max(8, window.innerWidth - w - 8), y: Math.max(8, window.innerHeight - h - 8)}}
        ];
        var best = corners[0];
        var bestScore = -1;
        for (var i = 0; i < corners.length; i++) {{
          var c = corners[i];
          if (rectHitsAnyPoint(c.x, c.y, w, h, points, POINT_CLEAR_PX)) continue;
          var minDist = Infinity;
          for (var j = 0; j < (points || []).length; j++) {{
            var pt = points[j];
            if (!pt) continue;
            var dx = (c.x + w / 2) - pt.x;
            var dy = (c.y + h / 2) - pt.y;
            minDist = Math.min(minDist, Math.sqrt(dx * dx + dy * dy));
          }}
          if (!isFinite(minDist)) minDist = 0;
          if (minDist > bestScore) {{
            bestScore = minDist;
            best = c;
          }}
        }}
        return best;
      }}
      function findCardPosition(px, py, w, h, index, allPoints, placedRects) {{
        var cands = cardCandidateOrigins(px, py, w, h, index);
        function scoreAt(c, allowCardOverlap) {{
          var clamped = clampCardPosition(c.x, c.y, w, h);
          if (rectHitsAnyPoint(clamped.x, clamped.y, w, h, allPoints, POINT_CLEAR_PX)) {{
            return null;
          }}
          if (!allowCardOverlap) {{
            var self = {{x: clamped.x, y: clamped.y, w: w, h: h}};
            for (var j = 0; j < (placedRects || []).length; j++) {{
              if (rectsOverlap(self, placedRects[j], CARD_GAP_PX)) return null;
            }}
          }}
          var cx = clamped.x + w / 2;
          var cy = clamped.y + h / 2;
          var dist = Math.sqrt((cx - px) * (cx - px) + (cy - py) * (cy - py));
          return {{pos: clamped, dist: dist}};
        }}
        var best = null;
        for (var pass = 0; pass < 2; pass++) {{
          var allowOverlap = pass === 1;
          best = null;
          for (var i = 0; i < cands.length; i++) {{
            var scored = scoreAt(cands[i], allowOverlap);
            if (!scored) continue;
            if (!best || scored.dist < best.dist) best = scored;
            // First clear candidate in the nearest rings is good enough.
            if (best && i < 32 && pass === 0) break;
          }}
          if (best) return best.pos;
        }}
        return cornerAwayFromPoints(w, h, allPoints);
      }}
      function connectorAttachPoint(cardRect, px, py) {{
        var cx = cardRect.left + cardRect.width / 2;
        var cy = cardRect.top + cardRect.height / 2;
        var dx = px - cx;
        var dy = py - cy;
        if (Math.abs(dx) > Math.abs(dy)) {{
          return {{
            x: dx > 0 ? cardRect.right : cardRect.left,
            y: Math.min(cardRect.bottom - 4, Math.max(cardRect.top + 4, py))
          }};
        }}
        return {{
          x: Math.min(cardRect.right - 4, Math.max(cardRect.left + 4, px)),
          y: dy > 0 ? cardRect.bottom : cardRect.top
        }};
      }}
      function drawConnector(px, py, cardEl) {{
        if (!hoverSvg || !cardEl) return;
        var rect = cardEl.getBoundingClientRect();
        var attach = connectorAttachPoint(rect, px, py);
        var line = document.createElementNS(SVG_NS, "line");
        line.setAttribute("x1", String(attach.x));
        line.setAttribute("y1", String(attach.y));
        line.setAttribute("x2", String(px));
        line.setAttribute("y2", String(py));
        hoverSvg.appendChild(line);
      }}
      function buildCardElement(item) {{
        var card = document.createElement("div");
        card.className = "mol-hover-card";
        var lines = item.html_lines || (item.lines || []).map(escapeHtml);
        var html = "";
        if (item.img) {{
          html += '<img alt="" src="' + String(item.img).replace(/"/g, "&quot;") + '">';
        }}
        html += '<div class="lines">' + lines.join("<br>") + '</div>';
        card.innerHTML = html;
        return card;
      }}
      function placeCardAtPoint(card, px, py, index, allPoints, placedRects) {{
        hoverLayer.appendChild(card);
        card.style.left = "0px";
        card.style.top = "0px";
        card.style.visibility = "hidden";
        var w = Math.max(120, card.offsetWidth || 180);
        var h = Math.max(40, card.offsetHeight || 80);
        var pos = findCardPosition(px, py, w, h, index, allPoints || [], placedRects || []);
        card.style.left = pos.x + "px";
        card.style.top = pos.y + "px";
        card.style.visibility = "visible";
        // Re-measure after layout; if still covering a point (font/img load), nudge again.
        w = Math.max(w, card.offsetWidth || w);
        h = Math.max(h, card.offsetHeight || h);
        if (rectHitsAnyPoint(pos.x, pos.y, w, h, allPoints, POINT_CLEAR_PX)) {{
          pos = findCardPosition(px, py, w, h, index, allPoints || [], placedRects || []);
          card.style.left = pos.x + "px";
          card.style.top = pos.y + "px";
        }}
        drawConnector(px, py, card);
        lastHoverAnchor = {{x: px, y: py}};
        return {{x: pos.x, y: pos.y, w: w, h: h}};
      }}
      function renderHoverItems(items, anchors, overflow) {{
        clearHoverCards(true);
        if (!hoverLayer || !items || !items.length) return;
        var allPoints = (anchors || []).filter(function(a) {{
          return a && typeof a.x === "number" && typeof a.y === "number";
        }});
        var placed = [];
        for (var i = 0; i < items.length; i++) {{
          var item = items[i];
          var anchor = (anchors && anchors[i]) || lastHoverAnchor || {{x: 24, y: 24}};
          var card = buildCardElement(item);
          var rect = placeCardAtPoint(card, anchor.x, anchor.y, i, allPoints, placed);
          if (rect) placed.push(rect);
        }}
        var over = Number(overflow) || 0;
        if (over > 0) {{
          var badge = document.createElement("div");
          badge.className = "mol-hover-overflow";
          badge.textContent = "… and " + over + " more selected";
          hoverLayer.appendChild(badge);
          var bw = Math.max(120, badge.offsetWidth || 140);
          var bh = Math.max(20, badge.offsetHeight || 24);
          var bpos = cornerAwayFromPoints(bw, bh, allPoints.concat(placed.map(function(r) {{
            return {{x: r.x + r.w / 2, y: r.y + r.h / 2}};
          }})));
          badge.style.left = bpos.x + "px";
          badge.style.top = bpos.y + "px";
          badge.style.bottom = "auto";
        }}
      }}
      function normalizePayloadItems(payload) {{
        if (!payload) return [];
        if (Array.isArray(payload.items) && payload.items.length) return payload.items;
        if (payload.lines || payload.html_lines || payload.img) return [payload];
        return [];
      }}
      function requestHoverCard(pointIndex, clientX, clientY, pin) {{
        requestHoverCards([pointIndex], [{{x: clientX, y: clientY}}], pin);
      }}
      function requestHoverCards(pointIndices, anchors, pin) {{
        var gen = ++hoverReqGen;
        var idxs = (pointIndices || []).map(Number).filter(function(n) {{ return Number.isFinite(n); }});
        if (!idxs.length) {{
          if (!pin) clearHoverCards(false);
          return;
        }}
        function apply(raw) {{
          if (gen !== hoverReqGen) return;
          var payload = null;
          try {{
            payload = (typeof raw === "string") ? JSON.parse(raw || "null") : raw;
          }} catch (_j) {{
            payload = null;
          }}
          var items = normalizePayloadItems(payload);
          if (!items.length) {{
            if (!pin) clearHoverCards(false);
            return;
          }}
          if (pin) hoverPinned = true;
          var pts = idxs.slice(0, items.length);
          var anch = [];
          for (var i = 0; i < items.length; i++) {{
            var fromIdx = indexToPageXY(pts[i]);
            if (fromIdx) anch.push(fromIdx);
            else if (anchors && anchors[i]) anch.push(anchors[i]);
            else anch.push(lastHoverAnchor || {{x: 24, y: 24}});
          }}
          renderHoverItems(items, anch, payload && payload.overflow);
          requestAnimationFrame(function() {{
            if (gen !== hoverReqGen) return;
            renderHoverItems(items, anch, payload && payload.overflow);
          }});
        }}
        if (bridge && bridge.hoverCardsJson) {{
          try {{
            var maybeMulti = bridge.hoverCardsJson(JSON.stringify(idxs), apply);
            if (typeof maybeMulti === "string" && maybeMulti) apply(maybeMulti);
            return;
          }} catch (_hm) {{}}
        }}
        if (bridge && bridge.hoverCardJson) {{
          try {{
            var maybe = bridge.hoverCardJson(idxs[0], apply);
            if (typeof maybe === "string" && maybe) apply(maybe);
          }} catch (_h) {{
            try {{
              bridge.hoverCardJson(idxs[0], apply);
            }} catch (_h2) {{}}
          }}
        }}
      }}
      window.molmanagerClearHoverPin = function() {{
        hoverPinned = false;
        clearHoverCards(true);
      }};
      window.molmanagerPinHoverPoint = function(pointIndex) {{
        window.molmanagerPinHoverPoints(JSON.stringify([pointIndex]));
      }};
      window.molmanagerPinHoverPoints = function(indicesJson) {{
        var idxs = parseSelectionIndices(indicesJson).map(Number)
          .filter(function(n) {{ return Number.isFinite(n); }});
        if (!idxs.length) {{
          window.molmanagerClearHoverPin();
          return;
        }}
        hoverPinned = true;
        var anchors = idxs.map(function(i) {{
          return indexToPageXY(i) || lastHoverAnchor || {{x: 16, y: 16}};
        }});
        requestHoverCards(idxs, anchors, true);
      }};
      window.molmanagerSetHoverPersist = function(on) {{
        hoverPersist = !!on;
        if (!hoverPersist) {{
          hoverPinned = false;
        }}
      }};
      function applySelectionIndices(indicesJson) {{
        try {{
          // Clearing Plotly lasso shapes can re-fire plotly_selected; ignore bridge while we paint.
          beginSuppressPlotBridge(500);
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
                name: "Selected", showlegend: false, hoverinfo: "skip"
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
          beginSuppressPlotBridge(800);
          applyInFlight = true;
          pendingSelectionJson = null;
          hoverPinned = false;
          hideHoverCard(true);
          try {{
            hoverPersist = !!(layout.meta && layout.meta.molmanager_hover_persist);
          }} catch (_hp) {{
            hoverPersist = false;
          }}
          Plotly.react(gd, data, layout, config).then(function() {{
            try {{
              gd.removeAllListeners('plotly_click');
              gd.removeAllListeners('plotly_selected');
              gd.removeAllListeners('plotly_deselect');
              gd.removeAllListeners('plotly_hover');
              gd.removeAllListeners('plotly_unhover');
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
                      bridge.histogramPointsSelected(JSON.stringify(nums), isAdditiveEvent(ev.event || {{}}));
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
                if (!Number.isFinite(pn)) return;
                var clickEv = ev.event || {{}};
                refreshShiftHeld(clickEv);
                bridge.pointClicked(pn, isAdditiveEvent(clickEv));
              }} catch (_clickErr) {{}}
            }});
            gd.on('plotly_selected', function(ev) {{
              try {{
                scheduleClearSelectionShapes();
                if (suppressPlotBridge) return;
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
                var selEv = (ev && ev.event) ? ev.event : null;
                if (selEv) refreshShiftHeld(selEv);
                bridge.pointsSelected(JSON.stringify(idxs), isAdditiveEvent(selEv));
              }} catch (_selErr) {{}}
            }});
            gd.on('plotly_deselect', function() {{
              try {{
                scheduleClearSelectionShapes();
                if (suppressPlotBridge) return;
                if (Date.now() - lastNonemptyPlotSelection < 450) return;
                if (shiftHeld) return;
                if (bridge && bridge.pointsSelected) bridge.pointsSelected("[]", false);
              }} catch (_deselErr) {{}}
            }});
            gd.on('plotly_hover', function(ev) {{
              try {{
                if (!ev || !ev.points || !ev.points.length) return;
                var pt = ev.points[0];
                if (!traceSelectable(pt.curveNumber)) return;
                var pn = Number(pt.pointNumber);
                if (!Number.isFinite(pn)) return;
                if (hoverPinned && hoverPersist) return;
                var evt = ev.event || {{}};
                var fromPt = dataPointToPageXY(pt);
                var cx = fromPt ? fromPt.x
                  : ((typeof evt.clientX === "number") ? evt.clientX : 24);
                var cy = fromPt ? fromPt.y
                  : ((typeof evt.clientY === "number") ? evt.clientY : 24);
                requestHoverCard(pn, cx, cy, false);
              }} catch (_hovErr) {{}}
            }});
            gd.on('plotly_unhover', function() {{
              try {{
                if (hoverPinned && hoverPersist) return;
                hideHoverCard(false);
              }} catch (_unhovErr) {{}}
            }});
            applyInFlight = false;
            flushPendingSelection();
            try {{ Plotly.Plots.resize(gd); }} catch (_rz) {{}}
          }}).finally(function() {{
            applyInFlight = false;
            // Hold a bit after react so restyle/relayout echo events stay suppressed.
            beginSuppressPlotBridge(400);
          }});
        }} catch (e) {{
          console.error('molmanager Plotly embed failed:', e);
        }}
      }};
      function resizePlot() {{
        try {{
          if (gd && gd.data) Plotly.Plots.resize(gd);
        }} catch (_r) {{}}
      }}
      var resizeTimer = null;
      window.addEventListener('resize', function() {{
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(resizePlot, 50);
      }});
    }})();
  </script>
</body>
</html>"""


def write_interactive_plot_shell(path: Path) -> None:
    path.write_text(interactive_plot_shell_html(), encoding="utf-8")
