"""Tests for shared interactive Plotly WebEngine shell."""

from molmanager.ui.plotly_shell import interactive_plot_shell_html, sanitized_plotly_js


def test_sanitized_plotly_js_escapes_script_close():
    js = sanitized_plotly_js()
    assert "</script>" not in js
    assert "<\\/script>" in js or "script" in js


def test_interactive_plot_shell_includes_bridge_handlers():
    html = interactive_plot_shell_html()
    assert "molmanagerApply" in html
    assert "molmanagerSetSelection" in html
    assert "applySelectionIndices" in html
    assert "pendingSelectionJson" in html
    assert "heatmapCellClicked" in html
    assert "radarTraceClicked" in html
    assert "molmanager_selection_traces" in html
