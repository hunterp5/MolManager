"""Write self-contained Plotly HTML for Qt WebEngine (no CDN)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from plotly import graph_objects as go
from plotly.io import to_json as plotly_to_json
from plotly.offline import get_plotlyjs

_DEFAULT_WEB_CONFIG = {"displaylogo": False, "responsive": True}

_UTILITY_LEGEND_NAMES = frozenset(
    {
        "fit",
        "points",
        "selected",
        "compounds",
        "compound",
        "data",
        "values",
    }
)
_TRACE_LEGEND_RE = re.compile(r"^trace\s*\d+$", re.I)
_FIT_LEGEND_RE = re.compile(r"^fit\b", re.I)


def legend_name_is_utility(name: str | None) -> bool:
    """True when a trace name should not appear in the Plotly legend."""
    text = ("" if name is None else str(name)).strip()
    if not text:
        return True
    low = text.lower()
    if low in _UTILITY_LEGEND_NAMES:
        return True
    if _TRACE_LEGEND_RE.match(text):
        return True
    if _FIT_LEGEND_RE.match(text):
        return True
    return False


def suppress_utility_legend_entries(fig: go.Figure) -> None:
    """Hide generic / internal trace names from the Plotly legend (Fit, Trace 0, Compounds, …)."""
    any_visible = False
    for tr in fig.data:
        if legend_name_is_utility(getattr(tr, "name", None)):
            tr.showlegend = False
        elif getattr(tr, "showlegend", True) is not False:
            any_visible = True
    if not any_visible:
        fig.update_layout(showlegend=False)


def finalize_plot_legend(fig: go.Figure) -> go.Figure:
    """Apply legend cleanup (call from every figure builder before display)."""
    suppress_utility_legend_entries(fig)
    return fig


def figure_payload_json(fig: go.Figure, *, config: dict | None = None) -> str:
    """
    Serialize a figure for ``JSON.parse`` in Qt WebEngine.

    Standard ``json.dumps(fig.to_plotly_json())`` emits bare ``NaN`` tokens (invalid JSON)
    when marker colors include missing numeric values.
    """
    suppress_utility_legend_entries(fig)
    payload = json.loads(plotly_to_json(fig, validate=False))
    merged = dict(_DEFAULT_WEB_CONFIG)
    if config:
        merged.update(config)
    payload["config"] = merged
    return json.dumps(payload, separators=(",", ":"))


def write_self_contained_plotly_html(fig: go.Figure, path: Path) -> None:
    """
    Embed Plotly.js inline so QWebEngine does not depend on a CDN.

    Escapes ``:focus-visible`` CSS (Qt/Chromium can reject it) and ``</script>`` in JS.
    """
    plotly_js = get_plotlyjs().replace(":focus-visible", ":focus").replace("</script>", "<\\/script>")
    payload = figure_payload_json(fig)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>html, body, #plot {{ width: 100%; height: 100%; margin: 0; }}</style>
</head>
<body>
  <div id="plot"></div>
  <script>{plotly_js}</script>
  <script>
    (function() {{
      try {{
        var payload = {payload};
        var gd = document.getElementById('plot');
        Plotly.newPlot(gd, payload.data || [], payload.layout || {{}}, {{
          displaylogo: false,
          responsive: true
        }});
      }} catch (e) {{
        console.error('Plotly render failed:', e);
        document.body.innerHTML = '<p style="font-family:sans-serif;padding:1em">'
          + 'Plot failed to render: ' + e + '</p>';
      }}
    }})();
  </script>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")
