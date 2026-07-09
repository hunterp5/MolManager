"""Fast Prepare dialog configuration."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from molmanager.ui.dialogs.mol_tools import FastPrepareDialog


def test_fast_prepare_dialog_config(qapp):  # noqa: ARG001
    dlg = FastPrepareDialog(["Structure", "SMILES"], ["Structure", "SMILES"], 2)
    src, update_target, largest, fragments, only_sel = dlg.config()
    assert src == "Structure"
    assert update_target is True
    assert largest is None
    assert fragments == "Fragments"
    assert only_sel is False


def test_fast_prepare_dialog_new_column_mode(qapp):  # noqa: ARG001
    dlg = FastPrepareDialog(["Structure", "SMILES"], ["Structure", "SMILES"], 0)
    dlg.radio_new_columns.setChecked(True)
    dlg.largest_edit.setText("Largest")
    dlg.fragments_edit.setText("Rest")
    src, update_target, largest, fragments, only_sel = dlg.config()
    assert src == "Structure"
    assert update_target is False
    assert largest == "Largest"
    assert fragments == "Rest"
    assert only_sel is False
