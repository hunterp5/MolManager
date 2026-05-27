"""Fast Prepare dialog configuration."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from molmanager.ui.dialogs.mol_tools import FastPrepareDialog


def test_fast_prepare_dialog_config(qapp):  # noqa: ARG001
    dlg = FastPrepareDialog(["Structure", "SMILES"], 2)
    src, fragments, only_sel = dlg.config()
    assert src == "Structure"
    assert fragments == "Fragments"
    assert only_sel is False
