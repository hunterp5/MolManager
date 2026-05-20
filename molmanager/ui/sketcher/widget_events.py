"""Mouse, keyboard, and shortcut input for :class:`~molmanager.ui.sketcher.widget.SketchWidget`."""

from __future__ import annotations

import math
from typing import Any

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtWidgets import QAction, QMenu, QMessageBox, QWidget

from .bonds import _bond_make, _bond_unpack
from .constants import DEFAULT_WILDCARD_ELEMENTS, WILDCARD_ELEMENT
from .wildcards import _is_wildcard_node


class SketchWidgetEventsMixin:
    """Qt event handlers: pointer, keys, undo/redo shortcuts (mixed into ``SketchWidget``)."""

    def _release_marquee_mouse_grab_if_any(self) -> None:
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()

    def mousePressEvent(self, ev):
        wpt = ev.pos()
        pt = self._widget_point_to_model(wpt)
        if ev.button() == Qt.LeftButton:
            hit = self._hit_node(pt)
            self._mouse_down_pos = QPoint(wpt)

            if self.select_mode:
                if hit is not None:
                    if hit["id"] not in self.selected_nodes:
                        self.selected_nodes = [hit["id"]]
                    self._sync_selected_bonds_from_nodes()
                    self._maybe_move = True
                    self._move_start_pos = QPoint(pt)
                else:
                    bi_m, _ = self._hit_bond(pt)
                    if bi_m is not None and bi_m in self.selected_bond_indices:
                        self._maybe_move = True
                        self._move_start_pos = QPoint(pt)
                        self._selecting = False
                        self._select_start = None
                        self.update()
                        return
                    if bi_m is not None:
                        a0, b0, _, __ = _bond_unpack(self.bonds[bi_m])
                        s0 = self._selected_node_set()
                        if a0 in s0 and b0 in s0:
                            self._maybe_move = True
                            self._move_start_pos = QPoint(pt)
                            self._selecting = False
                            self._select_start = None
                            self.update()
                            return
                    self.selected_nodes = []
                    self.selected_bond_indices = set()
                    self._maybe_move = False
                    self._move_start_pos = None
                    self._moving = False
                    self._move_orig = {}
                    self._selecting = True
                    self._select_start = QPoint(wpt)
                    self._selection_rect = None
                    self.grabMouse()
                self.update()
                return

            if hit is not None and not self.erase_mode:
                self._drag_candidate = hit["id"]
            else:
                self._drag_candidate = None

            # click on bond cycles order: single → double → triple → single
            if isinstance(self.hover, tuple) and self.hover[0] == "bond" and not self.erase_mode:
                bi = self.hover[1]
                if 0 <= bi < len(self.bonds):
                    a, b, order, st = _bond_unpack(self.bonds[bi])
                    new_order = 1 if order >= 3 else order + 1
                    new_st = st if new_order == 1 else 0
                    self.bonds[bi] = _bond_make(a, b, new_order, new_st)
                    self._push_undo("chg_bond", (a, b, (order, st), (new_order, new_st)))
                    self._mouse_down_pos = None
                    self._drag_candidate = None
                    self._suppress_click = True
                    try:
                        self._refresh_hover_from_cursor()
                    except Exception:
                        pass
                    self._after_sketch_edit()
                    return

            # erase mode
            if self.erase_mode:
                if hit is not None:
                    node = next((n for n in self.nodes if n["id"] == hit["id"]), None)
                    conn = [b for b in self.bonds if _bond_unpack(b)[0] == hit["id"] or _bond_unpack(b)[1] == hit["id"]]
                    self._push_undo("del_node", (node, conn))
                    self._delete_node(hit["id"])
                    self._suppress_click = True
                    return
                bi, _ = self._hit_bond(pt)
                if bi is not None:
                    b = self.bonds.pop(bi)
                    self._push_undo("del_bond", b)
                self._after_sketch_edit()
                return

        elif ev.button() == Qt.RightButton:
            hit = self._hit_node(pt)
            if hit:
                menu = QMenu(self)
                act_edit = QAction("Edit Atom...", self)
                act_edit.triggered.connect(lambda ch, h=hit: self._open_edit_atom_dialog(h))
                menu.addAction(act_edit)
                act_fc = QAction("Edit Formal Charge…", self)
                act_fc.setToolTip("Set the atom’s integer formal charge (e.g. +2 on sulfur, −1 on oxygen).")
                act_fc.triggered.connect(lambda ch, h=hit: self._open_edit_formal_charge_dialog(h))
                menu.addAction(act_fc)
                act_h_atom = QAction("Show implicit hydrogens on this atom", self)
                act_h_atom.setEnabled(not _is_wildcard_node(hit))
                act_h_atom.setToolTip(
                    "Add explicit H atoms and bonds for this atom only (RDKit), using the same geometry as a full AddHs expansion."
                )
                hid = hit["id"]

                def _do_h_atom(_=False, atom_id=hid):
                    ok, msg = self.add_explicit_hydrogens_on_atom(atom_id)
                    dlg = self._sketcher_dialog_if()
                    parent_w = dlg if dlg is not None else self
                    if not ok:
                        QMessageBox.information(parent_w, "Hydrogens", msg)
                    elif dlg is not None:
                        dlg._update_sketch_status()

                act_h_atom.triggered.connect(_do_h_atom)
                menu.addAction(act_h_atom)
                if _is_wildcard_node(hit):
                    act_ed = QAction("Edit wildcard elements...", self)
                    act_ed.triggered.connect(lambda ch, h=hit: self._edit_wildcard_dialog(h))
                    menu.addAction(act_ed)
                menu.addSeparator()
                act_st = QAction("Show Stereochemistry", self)
                act_st.setCheckable(True)
                _hid = hit["id"]
                act_st.setChecked(_hid in self._stereo_label_node_ids)
                act_st.toggled.connect(lambda on, h=_hid: self._set_stereo_label_visible(h, on))
                menu.addAction(act_st)
                self._add_group_action_if_applicable(menu)
                menu.exec_(self.mapToGlobal(pt))
            else:
                bi, _ = self._hit_bond(pt)
                if bi is not None:
                    a_idx, b_idx, order, st = _bond_unpack(self.bonds[bi])
                    menu = QMenu(self)

                    set_menu = menu.addMenu("Set order")
                    for o in [1, 2, 3]:
                        act_o = QAction(str(o), self)

                        def _seto(ch, val=o, bi_m=bi, ao=a_idx, bo=b_idx):
                            _, _, cur_o, cur_s = _bond_unpack(self.bonds[bi_m])
                            if val == cur_o:
                                return
                            new_st = cur_s if val == 1 else 0
                            self.bonds[bi_m] = _bond_make(ao, bo, val, new_st)
                            self._push_undo("chg_bond", (ao, bo, (cur_o, cur_s), (val, new_st)))
                            try:
                                self._refresh_hover_from_cursor()
                            except Exception:
                                pass
                            self._after_sketch_edit()

                        act_o.triggered.connect(_seto)
                        set_menu.addAction(act_o)

                    stereo_menu = menu.addMenu("Bond stereo (single bonds)")
                    for label, sval in [("Plain", 0), ("Wedge (narrow at first atom)", 1), ("Hash / dashed wedge", 2)]:
                        sa = QAction(label, self)

                        def _set_st(ch, sv=sval, bi_m=bi, ao=a_idx, bo=b_idx):
                            _, _, o0, s0 = _bond_unpack(self.bonds[bi_m])
                            if o0 != 1:
                                return
                            if sv == s0:
                                return
                            self.bonds[bi_m] = _bond_make(ao, bo, o0, sv)
                            self._push_undo("chg_bond", (ao, bo, (o0, s0), (o0, sv)))
                            self._after_sketch_edit()

                        sa.triggered.connect(_set_st)
                        stereo_menu.addAction(sa)

                    self._add_group_action_if_applicable(menu)
                    menu.exec_(self.mapToGlobal(pt))
                else:
                    dlg = self._sketcher_dialog_if()
                    if dlg is not None:
                        dlg.show_sketch_canvas_menu(self.mapToGlobal(pt))
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(ev)
        pt = self._widget_point_to_model(ev.pos())
        hit = self._hit_node(pt)
        bi, _bd = self._hit_bond(pt)
        if hit is None and bi is None:
            return super().mouseDoubleClickEvent(ev)
        if not self.nodes:
            return super().mouseDoubleClickEvent(ev)
        seed: int | None = None
        if hit is not None:
            seed = hit["id"]
        elif bi is not None and 0 <= bi < len(self.bonds):
            a, b, _, __ = _bond_unpack(self.bonds[bi])
            seed = a
        if seed is None:
            return super().mouseDoubleClickEvent(ev)
        comp: set[int] | None = None
        for c in self.connected_components():
            if seed in c:
                comp = c
                break
        if not comp:
            return super().mouseDoubleClickEvent(ev)
        self._activate_select_mode_from_parent()
        self.selected_nodes = sorted(comp)
        self._sync_selected_bonds_from_nodes()
        self._selection_rect = None
        self._selecting = False
        self._maybe_move = False
        self._moving = False
        self._move_orig = {}
        self._move_start_pos = None
        self._release_marquee_mouse_grab_if_any()
        ev.accept()
        self._after_sketch_edit()

    def mouseMoveEvent(self, ev):
        wpt = ev.pos()
        pt = self._widget_point_to_model(wpt)

        if self.select_mode:
            if self._moving and self._move_start_pos is not None:
                dx = pt.x() - self._move_start_pos.x()
                dy = pt.y() - self._move_start_pos.y()
                dx, dy = self._clamp_selection_delta(dx, dy)
                for nid, orig in list(self._move_orig.items()):
                    n = next((x for x in self.nodes if x["id"] == nid), None)
                    if n:
                        n["pos"] = QPoint(int(orig.x() + dx), int(orig.y() + dy))
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return
            if self._selecting and self._select_start is not None:
                sx, sy = self._select_start.x(), self._select_start.y()
                minx, maxx = min(sx, wpt.x()), max(sx, wpt.x())
                miny, maxy = min(sy, wpt.y()), max(sy, wpt.y())
                self._selection_rect = QRect(minx, miny, maxx - minx, maxy - miny)
                model_rect = self._widget_rect_to_model(self._selection_rect)
                self.selected_nodes = [
                    n["id"]
                    for n in self.nodes
                    if (
                        model_rect.left() <= n["pos"].x() <= model_rect.right()
                        and model_rect.top() <= n["pos"].y() <= model_rect.bottom()
                    )
                ]
                self._sync_selected_bonds_from_marquee_rect(model_rect)
                self.update()
                return
            if self._maybe_move and self._move_start_pos is not None:
                dx = pt.x() - self._move_start_pos.x()
                dy = pt.y() - self._move_start_pos.y()
                if dx * dx + dy * dy >= (6**2):
                    self._moving = True
                    self._maybe_move = False
                    move_ids = self._atoms_for_selection_move()
                    self._move_orig = {n["id"]: QPoint(n["pos"].x(), n["pos"].y()) for n in self.nodes if n["id"] in move_ids}
                    self.setCursor(Qt.ClosedHandCursor)
                    self.update()
                    return

        if self._drag_candidate is not None and not self._is_dragging and self._mouse_down_pos is not None:
            dx = wpt.x() - self._mouse_down_pos.x()
            dy = wpt.y() - self._mouse_down_pos.y()
            if dx * dx + dy * dy >= (6**2):
                self._is_dragging = True
                self._drag_start = self._drag_candidate
                self._drag_pos = QPoint(pt)
                self.setCursor(Qt.ClosedHandCursor)

        if self._is_dragging:
            self._drag_pos = QPoint(pt)
            hit = self._hit_node(pt)
            self.hover = hit["id"] if hit else None
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return

        hit = self._hit_node(pt)
        bi, _ = self._hit_bond(pt)
        sel = self._selected_node_set()
        if self.select_mode:
            if hit and hit["id"] in sel:
                self.hover = hit["id"]
                self.setCursor(Qt.OpenHandCursor)
            elif bi is not None and bi in self.selected_bond_indices:
                self.hover = ("bond", bi)
                self.setCursor(Qt.OpenHandCursor)
            elif hit:
                self.hover = hit["id"]
                self.setCursor(Qt.PointingHandCursor)
            elif bi is not None:
                self.hover = ("bond", bi)
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.hover = None
                self.setCursor(Qt.ArrowCursor)
        elif hit:
            self.hover = hit["id"]
            self.setCursor(Qt.PointingHandCursor)
        else:
            if bi is not None:
                self.hover = ("bond", bi)
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.hover = None
                if self.erase_mode or self._carbon_chain_cursor_active():
                    self.setCursor(Qt.CrossCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return

        if self.select_mode:
            if self._moving:
                moves = []
                for nid, old_pos in self._move_orig.items():
                    n = next((x for x in self.nodes if x["id"] == nid), None)
                    if n:
                        new_pos = QPoint(n["pos"].x(), n["pos"].y())
                        if old_pos.x() != new_pos.x() or old_pos.y() != new_pos.y():
                            moves.append((nid, old_pos, new_pos))
                if moves:
                    self._push_undo("move_nodes", moves)
                self._moving = False
                self._move_start_pos = None
                self._move_orig = {}
                try:
                    self._refresh_hover_from_cursor()
                except Exception:
                    self.setCursor(Qt.ArrowCursor)
                self.update()
                return
            if self._selecting:
                self._selecting = False
                self._select_start = None
                self._selection_rect = None
                self._release_marquee_mouse_grab_if_any()
                try:
                    self._refresh_hover_from_cursor()
                except Exception:
                    self.setCursor(Qt.ArrowCursor)
                self.update()
                return
            if self._maybe_move:
                self._maybe_move = False
                self._move_start_pos = None
                try:
                    self._refresh_hover_from_cursor()
                except Exception:
                    self.setCursor(Qt.ArrowCursor)
                self.update()
                return

        if self._suppress_click and not self._is_dragging:
            self._suppress_click = False
            self._mouse_down_pos = None
            self._drag_candidate = None
            return

        if self._is_dragging:
            self._is_dragging = False
            self.setCursor(Qt.ArrowCursor)
            end_pt = self._widget_point_to_model(ev.pos())
            start_id = self._drag_start
            self._drag_start = None
            self._drag_pos = None
            self._drag_candidate = None
            self._mouse_down_pos = None

            hit = self._hit_node(end_pt)
            if hit and start_id is not None and hit["id"] != start_id:
                a, b = start_id, hit["id"]
                found = None
                for bi, bo in enumerate(self.bonds):
                    ba, bb, _, __ = _bond_unpack(bo)
                    if (ba == a and bb == b) or (ba == b and bb == a):
                        found = bi
                        break
                if found is None:
                    st = self.active_bond_stereo if self.active_bond_stereo in (1, 2) else 0
                    bond = _bond_make(a, b, 1, st)
                    self.bonds.append(bond)
                    self._push_undo("add_bond", bond)
                    self._after_sketch_edit()
                else:
                    i0, j0, order, st = _bond_unpack(self.bonds[found])
                    new_order = 1 if order >= 3 else order + 1
                    new_st = st if new_order == 1 else 0
                    self.bonds[found] = _bond_make(i0, j0, new_order, new_st)
                    self._push_undo("chg_bond", (i0, j0, (order, st), (new_order, new_st)))
                    try:
                        self._refresh_hover_from_cursor()
                    except Exception:
                        pass
                    self._after_sketch_edit()
            else:
                if start_id is not None:
                    base = next((n for n in self.nodes if n["id"] == start_id), None)
                    if base is None:
                        return
                    bx, by = base["pos"].x(), base["pos"].y()
                    ex0, ey0 = float(end_pt.x()), float(end_pt.y())
                    dx = ex0 - bx
                    dy = ey0 - by
                    dist = math.hypot(dx, dy)
                    # Place the new atom at the drop point so bond length and angle match the drag.
                    if dist < 1e-6:
                        MIN_BOND = 30
                        ix, iy = self._compute_extension_vector(start_id)
                        ex = int(round(bx + ix * MIN_BOND))
                        ey = int(round(by + iy * MIN_BOND))
                    else:
                        ex = int(round(ex0))
                        ey = int(round(ey0))
                    pel = self.place_element if self.place_element is not None else "C"
                    nid = self.next_id
                    self.next_id += 1
                    node: dict[str, Any] = {"id": nid, "pos": QPoint(ex, ey), "element": pel}
                    if pel == WILDCARD_ELEMENT:
                        node["wildcard_els"] = list(DEFAULT_WILDCARD_ELEMENTS)
                    self.nodes.append(node)
                    pst = self.active_bond_stereo if self.active_bond_stereo in (1, 2) else 0
                    bond = _bond_make(start_id, nid, 1, pst)
                    self.bonds.append(bond)
                    self._push_undo("add_node", node)
                    self._push_undo("add_bond", bond)
                    self._after_sketch_edit()
            return

        end_pt = self._widget_point_to_model(ev.pos())

        # charge placement
        if self.active_charge and self._drag_candidate is not None:
            nid = self._drag_candidate
            n = next((x for x in self.nodes if x["id"] == nid), None)
            if n is not None:
                old = int(n.get("charge", 0) or 0)
                n["charge"] = int(self.active_charge)
                self._push_undo("chg_charge", (nid, old, int(n["charge"])))
                self._after_sketch_edit()
            try:
                p = self.parent()
                if p and hasattr(p, "charge_plus"):
                    p.charge_plus.setChecked(False)
                if p and hasattr(p, "charge_minus"):
                    p.charge_minus.setChecked(False)
            except Exception:
                pass
            self.active_charge = None
            self._drag_candidate = None
            self._mouse_down_pos = None
            return

        # template placement (active_template stays set until another tool/mode is chosen)
        if self.active_template:
            tpl = self.active_template
            if self._drag_candidate is not None:
                self.place_template(tpl, attach_to=self._drag_candidate)
            else:
                self.place_template(tpl, center=end_pt)
            self._drag_candidate = None
            self._mouse_down_pos = None
            return

        if self._drag_candidate is not None:
            base_id = self._drag_candidate
            tgt = next((n for n in self.nodes if n["id"] == base_id), None)
            if tgt is not None and self.place_element == "C":
                is_plain_carbon = tgt.get("element") == "C" and not _is_wildcard_node(tgt)
                if is_plain_carbon:
                    self.add_carbon_to(base_id)
                    node = self.nodes[-1]
                    bond = next(
                        (
                            b
                            for b in self.bonds
                            if (b[0] == base_id and b[1] == node["id"]) or (b[1] == base_id and b[0] == node["id"])
                        ),
                        None,
                    )
                    self._push_undo("add_node", node)
                    if bond:
                        self._push_undo("add_bond", bond)
                    self._after_sketch_edit()
                else:
                    self._mutate_atom_element(tgt, "C", None)
            elif tgt is not None and self.place_element is not None:
                wels = list(DEFAULT_WILDCARD_ELEMENTS) if self.place_element == WILDCARD_ELEMENT else None
                self._mutate_atom_element(tgt, self.place_element, wels)
        else:
            ex, ey = end_pt.x(), end_pt.y()
            if self.place_element is None:
                self._drag_candidate = None
                self._mouse_down_pos = None
                return
            nid = self.next_id
            self.next_id += 1
            node: dict[str, Any] = {"id": nid, "pos": QPoint(ex, ey), "element": self.place_element}
            if self.place_element == WILDCARD_ELEMENT:
                node["wildcard_els"] = list(DEFAULT_WILDCARD_ELEMENTS)
            self.nodes.append(node)
            self._push_undo("add_node", node)
            self._after_sketch_edit()

        self._drag_candidate = None
        self._mouse_down_pos = None

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.select_mode and (self.selected_nodes or self.selected_bond_indices):
                self._delete_selected_atoms_and_bonds()
                return
            if isinstance(self.hover, int):
                nid = self.hover
                node = next((n for n in self.nodes if n["id"] == nid), None)
                if node is not None:
                    conn = [b for b in self.bonds if b[0] == nid or b[1] == nid]
                    self._push_undo("del_node", (node, conn))
                self._delete_node(nid)
                self.hover = None
                return
            if isinstance(self.hover, tuple) and self.hover[0] == "bond":
                bi = self.hover[1]
                if 0 <= bi < len(self.bonds):
                    b = self.bonds.pop(bi)
                    self._push_undo("del_bond", b)
                    self.hover = None
                    self._after_sketch_edit(notify=True, notify_if_valence_failed=True)
                    return
            if self.sel is not None:
                node = next((n for n in self.nodes if n["id"] == self.sel), None)
                if node is not None:
                    conn = [b for b in self.bonds if b[0] == self.sel or b[1] == self.sel]
                    self._push_undo("del_node", (node, conn))
                self._delete_node(self.sel)
                return

        try:
            if isinstance(self.hover, int):
                k = ev.key()
                mods = ev.modifiers()
                if mods & Qt.ShiftModifier:
                    if k == Qt.Key_C:
                        self._set_atom("Cl", next((n for n in self.nodes if n["id"] == self.hover), None))
                        return
                    if k == Qt.Key_B:
                        self._set_atom("Br", next((n for n in self.nodes if n["id"] == self.hover), None))
                        return
                key_char = None
                if Qt.Key_A <= k <= Qt.Key_Z:
                    key_char = chr(k)
                if key_char:
                    el = key_char.upper()
                    if el in ["C", "N", "O", "S", "P", "F", "I", "H"]:
                        self._set_atom(el, next((n for n in self.nodes if n["id"] == self.hover), None))
                        return
        except Exception:
            pass

        super().keyPressEvent(ev)

    def event(self, ev):
        try:
            if ev.type() == ev.KeyPress:
                if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Z:
                    self.undo()
                    return True
                if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Y:
                    self.redo()
                    return True
        except Exception:
            pass
        return super().event(ev)

