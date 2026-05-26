"""Neutralize dialog configuration."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from molmanager.ui.dialogs.mol_tools import NeutralizeDialog


def test_neutralize_dialog_defaults(qapp):  # noqa: ARG001
    dlg = NeutralizeDialog(["Structure", "SMILES"], 0)
    src, only_sel, no_render = dlg.config()
    assert src == "Structure"
    assert only_sel is False
    assert no_render is False


def test_neutralize_dialog_no_render_2d(qapp):  # noqa: ARG001
    dlg = NeutralizeDialog(["Structure"], 0)
    dlg.no_render_2d_cb.setChecked(True)
    _, _, no_render = dlg.config()
    assert no_render is True
