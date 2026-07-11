"""Sketcher delete-key behavior."""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QAction, QWidget

from molmanager.ui.sketcher.bonds import _bond_make
from molmanager.ui.sketcher.dialog import SketcherDialog
from molmanager.ui.sketcher.widget import SketchWidget


class _FakeParent(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._hotkey_actions = {
            "edit.delete_selection": QAction("Delete Selection"),
        }


def _add_two_atom_sketch(w: SketchWidget) -> tuple[int, int]:
    w.nodes = [
        {"id": 1, "pos": QPoint(100, 100), "element": "C"},
        {"id": 2, "pos": QPoint(160, 100), "element": "C"},
    ]
    w.bonds = [_bond_make(1, 2, 1, 0)]
    w.next_id = 3
    w.hover = 1
    return 1, 2


def test_delete_key_removes_hovered_atom_in_draw_mode(qapp) -> None:  # noqa: ARG001
    w = SketchWidget()
    w.select_mode = False
    w.erase_mode = False
    _add_two_atom_sketch(w)
    w.hover = 1
    assert w._try_delete_hover_target(refresh_hover=False)
    assert len(w.nodes) == 1
    assert w.nodes[0]["id"] == 2


def test_handle_delete_key_prefers_hover_over_selection(qapp) -> None:  # noqa: ARG001
    w = SketchWidget()
    w.select_mode = True
    _add_two_atom_sketch(w)
    w.selected_nodes = [2]
    w.hover = 1
    assert w._try_delete_hover_target(refresh_hover=False)
    assert len(w.nodes) == 1
    assert w.nodes[0]["id"] == 2


def test_handle_delete_key_deletes_selection_without_hover(qapp) -> None:  # noqa: ARG001
    w = SketchWidget()
    w.select_mode = True
    _add_two_atom_sketch(w)
    w.selected_nodes = [2]
    w.hover = None
    assert w._handle_delete_key()
    assert len(w.nodes) == 1
    assert w.nodes[0]["id"] == 1


def test_delete_key_removes_hovered_bond(qapp) -> None:  # noqa: ARG001
    w = SketchWidget()
    w.nodes = [
        {"id": 1, "pos": QPoint(100, 100), "element": "C"},
        {"id": 2, "pos": QPoint(160, 100), "element": "C"},
        {"id": 3, "pos": QPoint(220, 100), "element": "C"},
    ]
    w.bonds = [_bond_make(1, 2, 1, 0), _bond_make(2, 3, 1, 0)]
    w.next_id = 4
    w.hover = ("bond", 0)
    assert w._try_delete_hover_target(refresh_hover=False)
    assert len(w.bonds) == 1
    assert w.bonds[0][:2] == (2, 3)


def test_sketcher_dialog_blocks_parent_delete_action(qapp) -> None:  # noqa: ARG001
    parent = _FakeParent()
    act = parent._hotkey_actions["edit.delete_selection"]
    act.setEnabled(True)
    dlg = SketcherDialog(parent)
    dlg.show()
    qapp.processEvents()
    assert act.isEnabled() is False
    dlg.hide()
    qapp.processEvents()
    assert act.isEnabled() is True


def test_sketcher_dialog_event_filter_deletes_hovered_atom(qapp) -> None:  # noqa: ARG001
    parent = _FakeParent()
    dlg = SketcherDialog(parent)
    dlg.show()
    qapp.processEvents()
    w = dlg.canvas
    _add_two_atom_sketch(w)
    w.hover = 1
    ev = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    assert dlg.eventFilter(w, ev) is True
    assert len(w.nodes) == 1
    assert w.nodes[0]["id"] == 2
    dlg.close()
    qapp.processEvents()
