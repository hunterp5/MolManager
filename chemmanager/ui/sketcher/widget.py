from __future__ import annotations

import json
import math
import re
from typing import Any

from PyQt5.QtCore import QLineF, QPoint, QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QShortcut,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rdkit import Chem

from .alkene_stereo import infer_alkene_ez_for_sketch_mol
from .bonds import (
    _bond_make,
    _bond_record_ok,
    _bond_same_undirected,
    _bond_unpack,
    reorient_wedged_bonds_tip_away_from_multiples,
)
from .chem import _parse_atom_symbol_input
from .sketch_graph import connected_components_from_graph, topology_fingerprint
from .sketch_rdkit import SketchWidgetRdkitMixin
from .constants import (
    ACS_PUBLICATION_MEDIAN_BOND_PX,
    CLIPBOARD_PREFIX,
    DEFAULT_WILDCARD_ELEMENTS,
    SKETCH_RING_TEMPLATES,
    WILDCARD_ELEMENT,
    WILDCARD_ELEMENT_CHOICES,
)
from .wildcards import (
    WildcardElementsDialog,
    _is_wildcard_node,
    _normalize_wildcard_elements,
)
from .widget_painting import SketchWidgetPaintMixin
from .widget_events import SketchWidgetEventsMixin


class SketchWidget(SketchWidgetEventsMixin, SketchWidgetPaintMixin, SketchWidgetRdkitMixin, QWidget):
    """
    Chemical sketch widget: place atoms, draw bonds by dragging, adjust bond order,
    erase, select/move, templates, undo/redo, and export SMILES via RDKit.

    Stereo model: wedge/hash on **single** bonds only (narrow tip = stereocenter atom in the bond tuple);
    alkene **E/Z** from 2D layout via ``alkene_stereo``. Not a full tautomer/atropisomer engine—see
    ``docs/STEREO_AND_ISOMERISM.md``.

    Bonds: stored **order** 1/2/3 only; aromatic mol loads may appear as order 1 lines. Valence UI sums
    bond orders vs RDKit-based caps—see ``docs/VALENCE_BONDS_AND_AROMATICITY.md``.
    """

    sketchChanged = pyqtSignal()

    def _sketcher_dialog_if(self):
        from .dialog import SketcherDialog

        p = self.parent()
        return p if isinstance(p, SketcherDialog) else None

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(500, 400)

        self.nodes: list[dict[str, Any]] = []  # {'id': int, 'pos': QPoint, 'element': str, 'charge'?: int}
        self.bonds: list[tuple[int, int, int, int]] = []  # (a_id, b_id, order, stereo) stereo: 0 plain, 1 wedge(a→b), 2 hash(a→b)
        self.next_id = 0
        self.sel: int | None = None

        # placement / modes
        self.place_element: str | None = None
        self.erase_mode = False
        self.select_mode = False
        self.active_template: str | None = None
        self.active_charge: int | None = None  # +1, -1, or None
        self.active_bond_stereo: int = 0  # 0 plain, 1 wedge, 2 hash (single bonds only)

        # hover & interaction state
        self.hover: int | tuple[str, int] | None = None  # node id, or ('bond', bond_index)
        self.selected_nodes: list[int] = []
        self.selected_bond_indices: set[int] = set()
        self._selecting = False
        self._select_start: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._moving = False
        self._move_start_pos: QPoint | None = None
        self._move_orig: dict[int, QPoint] = {}

        self._is_dragging = False
        self._drag_start: int | None = None
        self._drag_pos: QPoint | None = None
        self._drag_candidate: int | None = None
        self._mouse_down_pos: QPoint | None = None
        self._maybe_move = False
        self._suppress_click = False

        self._angle_signs: dict[int, int] = {}
        self._undo: list[tuple[str, Any]] = []
        self._redo: list[tuple[str, Any]] = []

        self.radius = 14
        self._valence_violations: set[int] = set()
        self._charge_violations: set[int] = set()

        # User "Group": fixed union of fragments → one SMILES entry (dot-separated) until sketch changes or Ungroup.
        # True salt (cation + anion fragments) uses ion ordering; otherwise fragments are only co-grouped, not as a salt.
        self._salt_bundle_smiles: str | None = None
        self._salt_bundle_nodes: frozenset[int] | None = None  # node ids in the grouped fragments only
        self._salt_bundle_fragment_count: int | None = None
        self._group_bundle_is_salt: bool = False

        self._chiral_center_ids: set[int] = set()
        self._chiral_stereo_issue_ids: set[int] = set()
        self._stereo_cip_by_node_id: dict[int, str] = {}
        self._alkene_ez_by_bond_index: dict[int, str] = {}
        self._stereo_label_node_ids: set[int] = set()

        self._ccache_fp: tuple[tuple[int, ...], tuple[tuple[int, int], ...]] | None = None
        self._ccache_comps: tuple[frozenset[int], ...] = ()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def _carbon_chain_cursor_active(self) -> bool:
        """Carbon tool uses crosshair over empty canvas (chain / replace-on-click)."""
        return (
            self.place_element == "C"
            and not self.select_mode
            and not self.erase_mode
            and self.active_template is None
        )

    def _notify_sketch_changed(self) -> None:
        self.sketchChanged.emit()

    def _clear_salt_bundle(self) -> None:
        self._salt_bundle_smiles = None
        self._salt_bundle_nodes = None
        self._salt_bundle_fragment_count = None
        self._group_bundle_is_salt = False

    def _salt_invalidate_if_stale(self) -> None:
        if self._salt_bundle_nodes is None:
            return
        U = self._salt_bundle_nodes
        cur = frozenset(n["id"] for n in self.nodes)
        if not U <= cur:
            self._clear_salt_bundle()
            return
        comps_u = [c for c in self.connected_components() if c & U]
        if self._salt_bundle_fragment_count is not None and len(comps_u) != self._salt_bundle_fragment_count:
            self._clear_salt_bundle()

    def _selected_node_set(self) -> set[int]:
        return set(self.selected_nodes)

    def _sync_selected_bonds_from_nodes(self) -> None:
        """Bonds whose both endpoints are in the current node selection (click / replace selection)."""
        s = self._selected_node_set()
        self.selected_bond_indices = {bi for bi, bond in enumerate(self.bonds) if _bond_unpack(bond)[0] in s and _bond_unpack(bond)[1] in s}

    @staticmethod
    def _segment_intersects_rect(x1: float, y1: float, x2: float, y2: float, rect: QRect) -> bool:
        """True if the segment crosses or lies inside the axis-aligned rectangle."""
        r = rect.normalized()
        if r.isEmpty():
            return False
        p1 = QPointF(x1, y1)
        p2 = QPointF(x2, y2)
        if r.contains(p1.toPoint()) or r.contains(p2.toPoint()):
            return True
        seg = QLineF(p1, p2)
        left, top, right, bottom = float(r.left()), float(r.top()), float(r.right()), float(r.bottom())
        edges = (
            QLineF(left, top, right, top),
            QLineF(right, top, right, bottom),
            QLineF(right, bottom, left, bottom),
            QLineF(left, bottom, left, top),
        )
        for edge in edges:
            itype, _ = seg.intersects(edge)
            if itype == QLineF.BoundedIntersection:
                return True
        return False

    def _sync_selected_bonds_from_marquee_rect(self, rect: QRect | None) -> None:
        """Marquee: any bond whose segment intersects the selection rectangle."""
        if rect is None:
            self.selected_bond_indices = set()
            return
        r = rect.normalized()
        if r.width() <= 0 and r.height() <= 0:
            r = QRect(r.left(), r.top(), 1, 1)
        elif r.width() <= 0:
            r = QRect(r.left(), r.top(), 1, r.height())
        elif r.height() <= 0:
            r = QRect(r.left(), r.top(), r.width(), 1)
        buds: set[int] = set()
        for bi, bond in enumerate(self.bonds):
            a, b, _, __ = _bond_unpack(bond)
            na = next((n for n in self.nodes if n["id"] == a), None)
            nb = next((n for n in self.nodes if n["id"] == b), None)
            if not na or not nb:
                continue
            x1, y1 = float(na["pos"].x()), float(na["pos"].y())
            x2, y2 = float(nb["pos"].x()), float(nb["pos"].y())
            if self._segment_intersects_rect(x1, y1, x2, y2, r):
                buds.add(bi)
        self.selected_bond_indices = buds

    def _atoms_for_selection_move(self) -> set[int]:
        """Atoms to translate: explicitly selected plus endpoints of selected bonds."""
        s = self._selected_node_set()
        for bi in self.selected_bond_indices:
            if 0 <= bi < len(self.bonds):
                a, b, _, __ = _bond_unpack(self.bonds[bi])
                s.add(a)
                s.add(b)
        return s

    def _clamp_selection_delta(self, dx: int, dy: int) -> tuple[int, int]:
        """Keep all atoms in _move_orig inside the widget when moving as a group."""
        if not self._move_orig:
            return dx, dy
        m = int(self.radius) + 6
        w, h = max(self.width(), 1), max(self.height(), 1)
        lo_x = max(m - int(o.x()) for o in self._move_orig.values())
        hi_x = min((w - m) - int(o.x()) for o in self._move_orig.values())
        lo_y = max(m - int(o.y()) for o in self._move_orig.values())
        hi_y = min((h - m) - int(o.y()) for o in self._move_orig.values())
        if lo_x > hi_x:
            dx = int((lo_x + hi_x) / 2)
        else:
            dx = min(max(dx, lo_x), hi_x)
        if lo_y > hi_y:
            dy = int((lo_y + hi_y) / 2)
        else:
            dy = min(max(dy, lo_y), hi_y)
        return dx, dy

    def _delete_selected_atoms_and_bonds(self) -> None:
        """Remove selected bonds (only those still present), then selected atoms; clears selection."""
        if not self.select_mode or (not self.selected_nodes and not self.selected_bond_indices):
            return
        for bi in sorted(self.selected_bond_indices, reverse=True):
            if 0 <= bi < len(self.bonds):
                b = self.bonds.pop(bi)
                self._push_undo("del_bond", b)
        for nid in list(self.selected_nodes):
            node = next((n for n in self.nodes if n["id"] == nid), None)
            if node is None:
                continue
            conn = [b for b in self.bonds if b[0] == nid or b[1] == nid]
            self._push_undo("del_node", (node, conn))
            self._delete_node(nid)
        self.selected_nodes = []
        self.selected_bond_indices = set()
        self.hover = None
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

    @staticmethod
    def _formal_charge(node: dict[str, Any]) -> int:
        ch = node.get("charge", 0)
        if ch is None:
            return 0
        try:
            return int(ch)
        except (TypeError, ValueError):
            return 0

    def connected_components(self) -> list[set[int]]:
        """Heavy-atom connectivity via bonds. Each isolated atom is its own component."""
        fp = topology_fingerprint(self.nodes, self.bonds)
        if fp != self._ccache_fp:
            self._ccache_fp = fp
            raw = connected_components_from_graph(self.nodes, self.bonds)
            self._ccache_comps = tuple(frozenset(c) for c in raw)
        return [set(c) for c in self._ccache_comps]

    def fragment_count(self) -> int:
        return len(self.connected_components())

    def _max_bond_order_sum(self, element: str, fc: int) -> int:
        """
        Allowed sum of bond orders to neighboring heavy atoms before implicit H.
        Charge-aware so e.g. N+, O-, quaternary ammonium do not spuriously trip validation.
        """
        if fc == 1:
            if element == "N":
                return 4
            if element == "O":
                return 3
            if element in ("S", "P"):
                return 5
        if fc == -1:
            if element == "O":
                return 1
            if element == "N":
                return 2
        base = self._max_valence(element)
        if fc > 0:
            return max(1, base - fc)
        if fc < 0:
            return max(1, base + fc)
        return base

    def _max_bond_order_sum_for_node(self, n: dict[str, Any], fc: int) -> int:
        if _is_wildcard_node(n):
            els = _normalize_wildcard_elements(n)
            return max(self._max_bond_order_sum(el, fc) for el in els)
        return self._max_bond_order_sum(n["element"], fc)

    def sketch_has_wildcards(self) -> bool:
        return any(_is_wildcard_node(n) for n in self.nodes)

    # ---------- Geometry / hits ----------
    def _hit_node(self, pt: QPoint):
        for n in self.nodes:
            d2 = (n["pos"].x() - pt.x()) ** 2 + (n["pos"].y() - pt.y()) ** 2
            if d2 <= (self.radius * 1.5) ** 2:
                return n
        return None

    def _point_to_segment_distance_sq(self, px, py, x1, y1, x2, y2):
        vx, vy = x2 - x1, y2 - y1
        wx, wy = px - x1, py - y1
        c1 = vx * wx + vy * wy
        if c1 <= 0:
            dx, dy = px - x1, py - y1
            return dx * dx + dy * dy
        c2 = vx * vx + vy * vy
        if c2 <= c1:
            dx, dy = px - x2, py - y2
            return dx * dx + dy * dy
        b = c1 / c2
        bx, by = x1 + b * vx, y1 + b * vy
        dx, dy = px - bx, py - by
        return dx * dx + dy * dy

    def _hit_bond(self, pt: QPoint):
        best_i, best_d = None, None
        px, py = pt.x(), pt.y()
        for bi, bond in enumerate(self.bonds):
            a, b, order, _ = _bond_unpack(bond)
            ni = next((n for n in self.nodes if n["id"] == a), None)
            nj = next((n for n in self.nodes if n["id"] == b), None)
            if not ni or not nj:
                continue
            d2 = self._point_to_segment_distance_sq(px, py, ni["pos"].x(), ni["pos"].y(), nj["pos"].x(), nj["pos"].y())
            if best_d is None or d2 < best_d:
                best_d, best_i = d2, bi
        if best_d is not None and best_d <= (12**2):
            return best_i, best_d
        return None, None

    def _refresh_hover_from_cursor(self):
        try:
            gpos = QCursor.pos()
            lpos = self.mapFromGlobal(gpos)
            hit = self._hit_node(lpos)
            bi, _ = self._hit_bond(lpos)
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
        except Exception:
            pass

    # ---------- Valence checks ----------
    def _max_valence(self, element: str) -> int:
        if element in ("H", "D", "T"):
            return 1
        try:
            pt = Chem.GetPeriodicTable()
            an = pt.GetAtomicNumber(element)
            if an <= 0:
                return 8
            dv = pt.GetDefaultValence(an)
            if dv > 0:
                return dv
        except Exception:
            pass
        if element in ("Na", "K", "Rb", "Cs", "Li"):
            return 1
        if element in ("Mg", "Ca", "Sr", "Ba"):
            return 2
        if element in ("Zn", "Cd", "Hg", "Cu", "Ag", "Au", "Ni", "Pd", "Pt", "Co"):
            return 4
        return 8

    def _current_valence(self, node_id: int) -> int:
        s = 0
        for bond in self.bonds:
            a, b, order, _ = _bond_unpack(bond)
            if a == node_id or b == node_id:
                s += order
        return s

    def _recompute_chiral_highlights(self) -> None:
        """Tetrahedral R/S (wedge/hash) and alkene E/Z from sketch geometry + RDKit ranking."""
        self._chiral_center_ids = set()
        self._chiral_stereo_issue_ids = set()
        self._stereo_cip_by_node_id = {}
        self._alkene_ez_by_bond_index = {}
        if not self.nodes:
            return
        try:
            ids = {n["id"] for n in self.nodes}
            out = self._mol_from_node_ids(ids, return_idmap=True)
            if out is None:
                return
            mol, sk2rd = out
            if mol is None or mol.GetNumAtoms() == 0:
                return
            mol.UpdatePropertyCache(strict=False)
            inv = {v: k for k, v in sk2rd.items()}
            for cen in Chem.FindMolChiralCenters(
                mol,
                includeUnassigned=True,
                includeCIP=True,
                useLegacyImplementation=False,
            ):
                idx = cen[0]
                if idx not in inv:
                    continue
                nid = inv[idx]
                self._chiral_center_ids.add(nid)
                tag = cen[1] if len(cen) >= 2 else ""
                if tag in ("R", "S"):
                    self._stereo_cip_by_node_id[nid] = str(tag)
                else:
                    self._chiral_stereo_issue_ids.add(nid)
            try:
                ez_rd = infer_alkene_ez_for_sketch_mol(mol)
            except Exception:
                ez_rd = {}
            for bi, bond in enumerate(self.bonds):
                a, b, o, _s = _bond_unpack(bond)
                if o != 2:
                    continue
                ai, bj = sk2rd.get(a), sk2rd.get(b)
                if ai is None or bj is None:
                    continue
                key = (min(ai, bj), max(ai, bj))
                lab = ez_rd.get(key)
                if lab in ("E", "Z"):
                    self._alkene_ez_by_bond_index[bi] = lab
        except Exception:
            self._chiral_center_ids = set()
            self._chiral_stereo_issue_ids = set()
            self._stereo_cip_by_node_id = {}
            self._alkene_ez_by_bond_index = {}

    def _recompute_valence_violations(self, *, notify: bool = True) -> None:
        live_ids = {n["id"] for n in self.nodes}
        self._stereo_label_node_ids &= live_ids
        bad: set[int] = set()
        charge_bad: set[int] = set()
        for n in self.nodes:
            vid = n["id"]
            val = self._current_valence(vid)
            fc = self._formal_charge(n)
            cap = self._max_bond_order_sum_for_node(n, fc)
            if val > cap:
                bad.add(vid)
                if fc != 0:
                    charge_bad.add(vid)
        self._valence_violations = bad
        self._charge_violations = charge_bad
        self._recompute_chiral_highlights()
        self.update()
        if notify:
            self._notify_sketch_changed()

    def _after_sketch_edit(
        self,
        *,
        valence: bool = True,
        notify: bool = True,
        notify_if_valence_failed: bool = False,
    ) -> None:
        """Recompute valence/stereo highlights, repaint, and optionally notify listeners.

        Wraps valence recomputation in ``try/except`` so a bad intermediate graph cannot leave the
        widget without a repaint. On success, ``_recompute_valence_violations`` handles
        ``update`` and (when ``notify``) ``_notify_sketch_changed``. On failure, repaints; callers
        that must still emit ``sketch_changed`` (for example bulk delete) set
        ``notify_if_valence_failed=True``.
        """
        if valence:
            try:
                self._recompute_valence_violations(notify=notify)
                return
            except Exception:
                try:
                    self.update()
                except Exception:
                    pass
                if notify and notify_if_valence_failed:
                    try:
                        self._notify_sketch_changed()
                    except Exception:
                        pass
                return
        try:
            self.update()
        except Exception:
            pass
        if notify:
            try:
                self._notify_sketch_changed()
            except Exception:
                pass

    # ---------- Undo/redo ----------
    def _push_undo(self, op: str, data: Any):
        self._undo.append((op, data))
        self._redo.clear()

    def _ensure_bonds_sanitized(self) -> None:
        """Drop malformed bond tuples and normalize: wedge/hash stereo only applies to single bonds."""
        out: list[tuple[int, int, int, int]] = []
        for b in self.bonds:
            if not _bond_record_ok(b):
                continue
            a, bo, o, s = _bond_unpack(b)
            if o != 1:
                s = 0
            out.append(_bond_make(a, bo, o, s))
        out = reorient_wedged_bonds_tip_away_from_multiples(out)
        if len(out) != len(self.bonds):
            self.selected_bond_indices = set()
        if out != self.bonds:
            self.bonds = out

    def undo(self):
        if not self._undo:
            return
        self._ensure_bonds_sanitized()
        op, data = self._undo.pop()
        if op == "add_node":
            node = data
            nid = node["id"]
            conn = [b for b in self.bonds if b[0] == nid or b[1] == nid]
            self.bonds = [b for b in self.bonds if b[0] != nid and b[1] != nid]
            self.nodes = [n for n in self.nodes if n["id"] != nid]
            self._redo.append(("del_node", (node, conn)))
        elif op == "add_bond":
            if not _bond_record_ok(data):
                pass
            else:
                bond = _bond_make(*_bond_unpack(data))
                for i, b in enumerate(self.bonds):
                    if b == bond:
                        self.bonds.pop(i)
                        self._redo.append(("del_bond", bond))
                        break
        elif op == "move_nodes":
            moves = data
            for nid, old_pos, new_pos in moves:
                n = next((n for n in self.nodes if n["id"] == nid), None)
                if n:
                    n["pos"] = QPoint(old_pos.x(), old_pos.y())
            rev = [(nid, new_pos, old_pos) for nid, old_pos, new_pos in moves]
            self._redo.append(("move_nodes", rev))
        elif op == "del_node":
            node, conn = data
            self.nodes.append(node)
            for b in conn:
                self.bonds.append(_bond_make(*_bond_unpack(b)))
            self._redo.append(("add_node", node))
        elif op == "del_bond":
            if not _bond_record_ok(data):
                pass
            else:
                bond = _bond_make(*_bond_unpack(data))
                self.bonds.append(bond)
                self._redo.append(("add_bond", bond))
        elif op == "chg_atom":
            if len(data) >= 5:
                nid, old_el, new_el, old_w, new_w = data[:5]
            else:
                nid, old_el, new_el = data[:3]
                old_w, new_w = None, None
            n = next((n for n in self.nodes if n["id"] == nid), None)
            if n:
                n["element"] = old_el
                if old_el == WILDCARD_ELEMENT:
                    n["wildcard_els"] = list(old_w) if old_w else list(DEFAULT_WILDCARD_ELEMENTS)
                else:
                    n.pop("wildcard_els", None)
                self._redo.append(("chg_atom", (nid, new_el, old_el, new_w, old_w)))
        elif op == "chg_charge":
            nid, old, new = data
            n = next((n for n in self.nodes if n["id"] == nid), None)
            if n:
                if int(old or 0) == 0:
                    n.pop("charge", None)
                else:
                    n["charge"] = int(old)
                self._redo.append(("chg_charge", (nid, new, old)))
        elif op == "chg_bond":
            if len(data) == 4 and isinstance(data[2], int):
                a, b, old_o, new_o = data
                old_os, new_os = (old_o, 0), (new_o, 0)
            else:
                a, b, old_os, new_os = data
            for i, bb in enumerate(self.bonds):
                x, y, o, s = _bond_unpack(bb)
                if {x, y} != {a, b}:
                    continue
                if x == a and y == b:
                    self.bonds[i] = _bond_make(x, y, old_os[0], old_os[1])
                else:
                    sr = old_os[1]
                    if sr == 1:
                        sr = 2
                    elif sr == 2:
                        sr = 1
                    self.bonds[i] = _bond_make(x, y, old_os[0], sr)
                self._redo.append(("chg_bond", (a, b, new_os, old_os)))
                break
        elif op == "add_hs_local":
            payload = data
            nids = {n["id"] for n in payload["nodes"]}
            for bb in payload["bonds"]:
                bt = _bond_make(*_bond_unpack(bb))
                self.bonds = [b for b in self.bonds if b != bt]
            self.nodes = [n for n in self.nodes if n["id"] not in nids]
            self._redo.append(("add_hs_redo", payload))
            self._after_sketch_edit()
            return
        elif op == "paste_group":
            payload = data
            for nid in payload["new_ids"]:
                self.nodes = [n for n in self.nodes if n["id"] != nid]
                self.bonds = [b for b in self.bonds if _bond_unpack(b)[0] != nid and _bond_unpack(b)[1] != nid]
            if self.sel in payload["new_ids"]:
                self.sel = None
            self.selected_nodes = [x for x in self.selected_nodes if x not in payload["new_ids"]]
            self.selected_bond_indices = set()
            self._redo.append(("paste_redo", payload))
        self._after_sketch_edit()

    def redo(self):
        if not self._redo:
            return
        self._ensure_bonds_sanitized()
        op, data = self._redo.pop()
        if op == "del_node":
            node, conn = data
            self.nodes.append(node)
            for b in conn:
                self.bonds.append(_bond_make(*_bond_unpack(b)))
            self._undo.append(("add_node", node))
            self._after_sketch_edit()
            return
        if op == "del_bond":
            if not _bond_record_ok(data):
                self._after_sketch_edit()
                return
            bond = _bond_make(*_bond_unpack(data))
            self.bonds.append(bond)
            self._undo.append(("add_bond", bond))
            self._after_sketch_edit()
            return
        if op == "add_bond":
            if not _bond_record_ok(data):
                self._after_sketch_edit()
                return
            bond = _bond_make(*_bond_unpack(data))
            for i, b in enumerate(self.bonds):
                if b == bond:
                    self.bonds.pop(i)
                    self._undo.append(("del_bond", bond))
                    break
            self._after_sketch_edit()
            return
        if op == "add_hs_redo":
            payload = data
            for n in payload["nodes"]:
                self.nodes.append(n)
            mx_id = max((n["id"] for n in self.nodes), default=0)
            self.next_id = max(self.next_id, mx_id + 1)
            for bb in payload["bonds"]:
                self.bonds.append(_bond_make(*_bond_unpack(bb)))
            self._undo.append(("add_hs_local", payload))
            self._after_sketch_edit()
            return
        if op == "paste_redo":
            self._paste_fragment_payload(data["fragment"], QPoint(int(data["anchor"][0]), int(data["anchor"][1])))
            return
        if op == "chg_bond":
            if len(data) == 4 and isinstance(data[2], int):
                a, b, old_o, new_o = data
                old_os, new_os = (old_o, 0), (new_o, 0)
            else:
                a, b, old_os, new_os = data
            for i, bb in enumerate(self.bonds):
                x, y, o, s = _bond_unpack(bb)
                if {x, y} != {a, b}:
                    continue
                if x == a and y == b:
                    self.bonds[i] = _bond_make(x, y, new_os[0], new_os[1])
                else:
                    sr = new_os[1]
                    if sr == 1:
                        sr = 2
                    elif sr == 2:
                        sr = 1
                    self.bonds[i] = _bond_make(x, y, new_os[0], sr)
                self._undo.append(("chg_bond", (a, b, old_os, new_os)))
                break
            self._after_sketch_edit()
            return
        if op == "chg_atom":
            if len(data) >= 5:
                nid, new_el, old_el, new_w, old_w = data[:5]
            else:
                nid, new_el, old_el = data[:3]
                new_w, old_w = None, None
            n = next((n for n in self.nodes if n["id"] == nid), None)
            if n is not None:
                n["element"] = new_el
                if new_el == WILDCARD_ELEMENT:
                    n["wildcard_els"] = list(new_w) if new_w else list(DEFAULT_WILDCARD_ELEMENTS)
                else:
                    n.pop("wildcard_els", None)
                self._undo.append(("chg_atom", (nid, old_el, new_el, old_w, new_w)))
            self._after_sketch_edit()
            return
        if op == "chg_charge":
            try:
                nid, new, old = data
            except Exception:
                nid, new = data[0], data[1]
                old = None
            n = next((n for n in self.nodes if n["id"] == nid), None)
            if n is not None:
                if int(new or 0) == 0:
                    n.pop("charge", None)
                else:
                    n["charge"] = int(new)
                if old is not None:
                    self._undo.append(("chg_charge", (nid, old, new)))
            self._after_sketch_edit()
            return
        if op == "move_nodes":
            moves = data
            for nid, old_pos, new_pos in moves:
                n = next((n for n in self.nodes if n["id"] == nid), None)
                if n:
                    n["pos"] = QPoint(new_pos.x(), new_pos.y())
            rev = [(nid, old_pos, new_pos) for nid, old_pos, new_pos in moves]
            self._undo.append(("move_nodes", rev))
            self._after_sketch_edit()
            return
        self._after_sketch_edit()

    # ---------- Editing helpers ----------
    def _delete_node(self, nid: int):
        self.nodes = [n for n in self.nodes if n["id"] != nid]
        self.bonds = [b for b in self.bonds if b[0] != nid and b[1] != nid]
        if self.sel == nid:
            self.sel = None
        self._after_sketch_edit()

    def _set_atom(self, element: str, hit: dict[str, Any] | None):
        if hit:
            self._mutate_atom_element(hit, element, None)
        else:
            self.place_element = element
            self.update()

    def _mutate_atom_element(self, hit: dict[str, Any], new_el: str, wildcard_els: list[str] | None) -> None:
        """Change an existing atom (with undo). For wildcards, pass ``wildcard_els`` or None for defaults."""
        nid = hit["id"]
        n = next((x for x in self.nodes if x["id"] == nid), None)
        if n is None:
            return
        old_el = n["element"]
        old_w = tuple(_normalize_wildcard_elements(n)) if _is_wildcard_node(n) else None
        if new_el == WILDCARD_ELEMENT:
            raw = list(wildcard_els) if wildcard_els else list(DEFAULT_WILDCARD_ELEMENTS)
            clean = [x for x in raw if x in WILDCARD_ELEMENT_CHOICES]
            if not clean:
                clean = list(DEFAULT_WILDCARD_ELEMENTS)
            new_w_store = tuple(sorted(set(clean)))
            if old_el == WILDCARD_ELEMENT and old_w == new_w_store:
                return
            n["element"] = WILDCARD_ELEMENT
            n["wildcard_els"] = list(new_w_store)
        else:
            if old_el == new_el:
                return
            n["element"] = new_el
            n.pop("wildcard_els", None)
        new_w_store = tuple(_normalize_wildcard_elements(n)) if _is_wildcard_node(n) else None
        self._push_undo("chg_atom", (nid, old_el, new_el, old_w, new_w_store))
        self._after_sketch_edit()

    def _edit_wildcard_dialog(self, hit: dict[str, Any]) -> None:
        d = WildcardElementsDialog(_normalize_wildcard_elements(hit), self)
        if d.exec_() != QDialog.Accepted:
            return
        sel = d.selected_elements()
        if not sel:
            QMessageBox.warning(self, "Wildcard", "Select at least one element.")
            return
        self._mutate_atom_element(hit, WILDCARD_ELEMENT, sel)

    def _open_edit_atom_dialog(self, hit: dict[str, Any]) -> None:
        hint = "*" if _is_wildcard_node(hit) else str(hit.get("element", "C"))
        txt, ok = QInputDialog.getText(
            self,
            "Edit Atom",
            "Element symbol or * for wildcard (e.g. C, N, Cl, Br, *):",
            text=hint,
        )
        if not ok:
            return
        parsed = _parse_atom_symbol_input(txt)
        if parsed is None:
            QMessageBox.warning(
                self,
                "Edit Atom",
                "Unknown symbol. Use a standard element (see the Elements toolbar), or * for wildcard.",
            )
            return
        new_el, wels = parsed
        self._mutate_atom_element(hit, new_el, wels)

    @staticmethod
    def _parse_formal_charge_text(raw: str) -> int | None:
        """Return integer formal charge, or 0 for neutral, or None if invalid."""
        s = (raw or "").strip().replace("−", "-")
        if s in ("", "none", "None", "neutral", "0"):
            return 0
        if s.startswith("+"):
            s = s[1:]
        try:
            v = int(s, 10)
        except ValueError:
            return None
        if v < -12 or v > 12:
            return None
        return v

    def _open_edit_formal_charge_dialog(self, hit: dict[str, Any]) -> None:
        nid = hit["id"]
        n = next((x for x in self.nodes if x["id"] == nid), None)
        if n is None:
            return
        cur = int(self._formal_charge(n))
        hint = str(cur) if cur != 0 else "0"
        txt, ok = QInputDialog.getText(
            self,
            "Edit Formal Charge",
            "Formal charge (integer). Examples: 0, 1, -1, +2, -3 (neutral uses 0):",
            text=hint,
        )
        if not ok:
            return
        new_q = self._parse_formal_charge_text(txt)
        if new_q is None:
            QMessageBox.warning(
                self,
                "Formal charge",
                "Enter an integer between -12 and +12 (e.g. 0, -2, +3).",
            )
            return
        if new_q == cur:
            return
        old = int(self._formal_charge(n))
        if new_q == 0:
            n.pop("charge", None)
        else:
            n["charge"] = new_q
        self._push_undo("chg_charge", (nid, old, new_q))
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

    def _selection_fragment_ids(self) -> set[int]:
        return self._atoms_for_selection_move()

    def _serialize_selection_fragment(self) -> dict[str, Any] | None:
        ids = self._selection_fragment_ids()
        if not ids:
            return None
        cx = cy = 0.0
        for i in ids:
            n = next((x for x in self.nodes if x["id"] == i), None)
            if n:
                cx += n["pos"].x()
                cy += n["pos"].y()
        nlen = max(len(ids), 1)
        cx /= nlen
        cy /= nlen
        nodes_j: list[dict[str, Any]] = []
        for old_id in sorted(ids):
            n = next((x for x in self.nodes if x["id"] == old_id), None)
            if not n:
                continue
            ent: dict[str, Any] = {
                "old_id": old_id,
                "element": n["element"],
                "rx": n["pos"].x() - cx,
                "ry": n["pos"].y() - cy,
                "charge": self._formal_charge(n),
            }
            if _is_wildcard_node(n):
                ent["wildcard_els"] = list(_normalize_wildcard_elements(n))
            nodes_j.append(ent)
        bonds_j: list[list[int]] = []
        for b in self.bonds:
            a, b0, o, s = _bond_unpack(b)
            if a in ids and b0 in ids:
                bonds_j.append([int(a), int(b0), int(o), int(s)])
        stereo_labels = sorted(int(x) for x in (ids & self._stereo_label_node_ids))
        return {"nodes": nodes_j, "bonds": bonds_j, "stereo_labels": stereo_labels}

    def copy_selection_to_clipboard(self) -> bool:
        if not self.select_mode:
            return False
        frag = self._serialize_selection_fragment()
        if not frag or not frag.get("nodes"):
            return False
        blob = CLIPBOARD_PREFIX + json.dumps(frag, separators=(",", ":"))
        QApplication.clipboard().setText(blob)
        return True

    def _paste_fragment_payload(self, frag: dict[str, Any], anchor: QPoint) -> None:
        old_to_new: dict[int, int] = {}
        new_ids: list[int] = []
        ax, ay = float(anchor.x()), float(anchor.y())
        for ent in frag["nodes"]:
            oid = int(ent["old_id"])
            nid = self.next_id
            self.next_id += 1
            old_to_new[oid] = nid
            ch = int(ent.get("charge", 0) or 0)
            el = str(ent["element"])
            node: dict[str, Any] = {
                "id": nid,
                "pos": QPoint(int(ax + float(ent["rx"])), int(ay + float(ent["ry"]))),
                "element": el,
            }
            if el == WILDCARD_ELEMENT:
                node["element"] = WILDCARD_ELEMENT
                raw = ent.get("wildcard_els") or DEFAULT_WILDCARD_ELEMENTS
                clean = [str(x).strip() for x in raw if str(x).strip() in WILDCARD_ELEMENT_CHOICES]
                node["wildcard_els"] = clean or list(DEFAULT_WILDCARD_ELEMENTS)
            if ch:
                node["charge"] = ch
            self.nodes.append(node)
            new_ids.append(nid)
        bi0 = len(self.bonds)
        for brec in frag.get("bonds", []):
            if not isinstance(brec, (list, tuple)) or len(brec) < 2:
                continue
            oa, ob = int(brec[0]), int(brec[1])
            o = int(brec[2])
            s = int(brec[3]) if len(brec) > 3 else 0
            na, nb = old_to_new.get(oa), old_to_new.get(ob)
            if na is None or nb is None:
                continue
            self.bonds.append(_bond_make(na, nb, o, s))
        for oid in frag.get("stereo_labels", []):
            try:
                oi = int(oid)
            except (TypeError, ValueError):
                continue
            nid = old_to_new.get(oi)
            if nid is not None:
                self._stereo_label_node_ids.add(nid)
        self._push_undo(
            "paste_group",
            {"new_ids": new_ids, "fragment": frag, "anchor": [anchor.x(), anchor.y()]},
        )
        self.selected_nodes = list(new_ids)
        self.selected_bond_indices = set(range(bi0, len(self.bonds)))
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

    def paste_from_clipboard(self, anchor: QPoint | None = None) -> bool:
        text = QApplication.clipboard().text()
        if not text.startswith(CLIPBOARD_PREFIX):
            return False
        try:
            frag = json.loads(text[len(CLIPBOARD_PREFIX) :])
        except Exception:
            return False
        if not isinstance(frag, dict) or "nodes" not in frag:
            return False
        pt = anchor if anchor is not None else self.rect().center()
        self._paste_fragment_payload(frag, pt)
        return True

    def clear(self):
        self.nodes = []
        self.bonds = []
        self.next_id = 0
        self.sel = None
        self.selected_nodes = []
        self.selected_bond_indices = set()
        self._selection_rect = None
        self._selecting = False
        self._select_start = None
        self._release_marquee_mouse_grab_if_any()
        self._chiral_center_ids = set()
        self._chiral_stereo_issue_ids = set()
        self._stereo_cip_by_node_id = {}
        self._alkene_ez_by_bond_index = {}
        self._stereo_label_node_ids.clear()
        self._clear_salt_bundle()
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

    def _median_bond_length_sketch_px(self) -> float | None:
        """Median Euclidean bond length in current sketch pixel coordinates."""
        if not self.bonds:
            return None
        pos = {n["id"]: n["pos"] for n in self.nodes}
        lens: list[float] = []
        for b in self.bonds:
            a, bj, _, __ = _bond_unpack(b)
            pa, pb = pos.get(a), pos.get(bj)
            if pa is None or pb is None:
                continue
            lens.append(math.hypot(float(pa.x() - pb.x()), float(pa.y() - pb.y())))
        if not lens:
            return None
        lens.sort()
        return float(lens[len(lens) // 2])

    def fit_sketch_to_viewport(
        self,
        margin: int | None = None,
        max_scale: float = 5.0,
        min_scale: float = 0.06,
        *,
        cap_median_bond_length_px: float | None = None,
    ) -> bool:
        """
        Uniformly scale and translate the sketch so all atoms fit inside the widget rect with margin.
        Used after RDKit loads so the structure is fully visible. Does not record undo.

        If ``cap_median_bond_length_px`` is set, the fit scale is reduced when needed so the median
        bond length does not exceed that value (publication / ACS-style sizing on load).

        Returns True if positions were scaled/translated and valence/stereo were refreshed; False
        if the sketch was left unchanged (empty sketch or viewport too small).
        """
        if not self.nodes:
            return False
        r = self.rect()
        rw, rh = r.width(), r.height()
        if rw < 48 or rh < 48:
            return False
        pad = int(self.radius * 1.5)
        xs = [n["pos"].x() for n in self.nodes]
        ys = [n["pos"].y() for n in self.nodes]
        minx = float(min(xs)) - pad
        maxx = float(max(xs)) + pad
        miny = float(min(ys)) - pad
        maxy = float(max(ys)) + pad
        bw = max(maxx - minx, 40.0)
        bh = max(maxy - miny, 40.0)
        if margin is None:
            margin = max(120, min(rw, rh) // 5)
        avail_w = max(float(rw - 2 * margin), 60.0)
        avail_h = max(float(rh - 2 * margin), 60.0)
        scale = min(avail_w / bw, avail_h / bh)
        if cap_median_bond_length_px is not None:
            mb = self._median_bond_length_sketch_px()
            if mb is not None and mb * scale > float(cap_median_bond_length_px):
                scale = float(cap_median_bond_length_px) / max(mb, 1e-6)
        scale = max(min(scale, max_scale), min_scale)
        tcx = float(r.center().x())
        tcy = float(r.center().y())
        mx = 0.5 * (minx + maxx)
        my = 0.5 * (miny + maxy)
        for n in self.nodes:
            x, y = float(n["pos"].x()), float(n["pos"].y())
            nx = int(round((x - mx) * scale + tcx))
            ny = int(round((y - my) * scale + tcy))
            n["pos"] = QPoint(nx, ny)
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)
        return True

    def center_sketch_in_viewport(self, push_undo: bool = True) -> None:
        """Translate all atoms so the sketch bounding box is centered in the widget (undoable)."""
        if not self.nodes:
            return
        r = self.rect()
        if r.width() < 8 or r.height() < 8:
            return
        xs = [n["pos"].x() for n in self.nodes]
        ys = [n["pos"].y() for n in self.nodes]
        mx = 0.5 * (min(xs) + max(xs))
        my = 0.5 * (min(ys) + max(ys))
        tcx = float(r.center().x())
        tcy = float(r.center().y())
        dx = int(round(tcx - mx))
        dy = int(round(tcy - my))
        if dx == 0 and dy == 0:
            return
        moves: list[tuple[int, QPoint, QPoint]] = []
        if push_undo:
            for n in self.nodes:
                oid = n["id"]
                op = QPoint(n["pos"].x(), n["pos"].y())
                np = QPoint(op.x() + dx, op.y() + dy)
                moves.append((oid, op, np))
        for n in self.nodes:
            n["pos"] = QPoint(n["pos"].x() + dx, n["pos"].y() + dy)
        if push_undo and moves:
            self._push_undo("move_nodes", moves)
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

    def zoom_about_viewport_center(self, factor: float, push_undo: bool = True) -> None:
        """Scale all atom positions uniformly about the widget center (undoable when ``push_undo``)."""
        if not self.nodes or abs(factor - 1.0) < 1e-6:
            return
        factor = float(factor)
        if factor <= 0:
            return
        r = self.rect()
        if r.width() < 8 or r.height() < 8:
            return
        cx = float(r.center().x())
        cy = float(r.center().y())
        old_pos: dict[int, QPoint] = {n["id"]: QPoint(n["pos"]) for n in self.nodes}
        for n in self.nodes:
            x, y = float(n["pos"].x()), float(n["pos"].y())
            nx = int(round((x - cx) * factor + cx))
            ny = int(round((y - cy) * factor + cy))
            n["pos"] = QPoint(nx, ny)
        if push_undo:
            moves: list[tuple[int, QPoint, QPoint]] = []
            for n in self.nodes:
                nid = n["id"]
                op = old_pos[nid]
                np = n["pos"]
                if op.x() != np.x() or op.y() != np.y():
                    moves.append((nid, op, np))
            if moves:
                self._push_undo("move_nodes", moves)
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

    def _can_group_selection(self) -> bool:
        sel = self._selected_node_set()
        if len(sel) < 2:
            return False
        n_touch = sum(1 for c in self.connected_components() if c & sel)
        return n_touch >= 2

    def _add_group_action_if_applicable(self, menu: QMenu) -> None:
        if self.select_mode and self._can_group_selection():
            menu.addSeparator()
            act = QAction("Group selection for export", self)
            act.triggered.connect(self._run_group_selection_menu)
            menu.addAction(act)

    # ---------- Placement (chain / templates) ----------
    def add_ring(self, n: int, center: QPoint | None = None, radius: int = 60, elements=None, bond_orders=None):
        c = center if center is not None else self.rect().center()
        elems = elements if elements is not None else ["C"] * n
        orders = bond_orders if bond_orders is not None else [1] * n
        ids: list[int] = []
        for i in range(n):
            theta = 2 * math.pi * i / n
            x = int(c.x() + radius * math.cos(theta))
            y = int(c.y() + radius * math.sin(theta))
            nid = self.next_id
            self.next_id += 1
            self.nodes.append({"id": nid, "pos": QPoint(x, y), "element": elems[i] if i < len(elems) else "C"})
            ids.append(nid)
        for i in range(n):
            a = ids[i]
            b = ids[(i + 1) % n]
            o = orders[i] if i < len(orders) else 1
            self.bonds.append(_bond_make(a, b, o, 0))
        self.update()

    def _compute_extension_vector(self, atom_id: int):
        base = next((n for n in self.nodes if n["id"] == atom_id), None)
        if base is None:
            return (1.0, 0.0)
        bx, by = base["pos"].x(), base["pos"].y()
        neigh = [b for b in self.bonds if _bond_unpack(b)[0] == atom_id or _bond_unpack(b)[1] == atom_id]
        angles = []
        for bond in neigh:
            a, b, _, __ = _bond_unpack(bond)
            nid = b if a == atom_id else a
            nnode = next((n for n in self.nodes if n["id"] == nid), None)
            if nnode:
                ang = math.atan2(nnode["pos"].y() - by, nnode["pos"].x() - bx)
                angles.append(ang)
        if not angles:
            return (1.0, 0.0)
        if len(angles) == 1:
            neigh_ang = angles[0]
            sign = self._angle_signs.get(atom_id, 1)
            has_high_order = any(_bond_unpack(b)[2] >= 2 for b in neigh)
            # sp2-style (~120° internal) vs sp3 tetrahedral (~109.5° internal)
            dev = math.radians(60.0) if has_high_order else math.radians(180.0 - 109.47)
            ang_new = neigh_ang + math.pi - sign * dev
            self._angle_signs[atom_id] = -sign
            return (math.cos(ang_new), math.sin(ang_new))
        angles = sorted(angles)
        max_gap = -1.0
        best_mid = 0.0
        for i in range(len(angles)):
            a1 = angles[i]
            a2 = angles[(i + 1) % len(angles)] if i + 1 < len(angles) else angles[0] + 2 * math.pi
            gap = a2 - a1 if a2 >= a1 else (a2 + 2 * math.pi - a1)
            if gap > max_gap:
                max_gap = gap
                best_mid = a1 + gap / 2.0
        sign = self._angle_signs.get(atom_id, 1)
        # Prefer ~120° spacing at trigonal centers (two neighbors ~60° apart in wide rings)
        if len(angles) == 2:
            a1, a2 = angles[0], angles[1]
            d = (a2 - a1) % (2 * math.pi)
            d = min(d, 2 * math.pi - d)
            if d < math.radians(75) and max_gap > math.radians(100):
                best_mid += sign * math.radians(15)
                self._angle_signs[atom_id] = -sign
        elif max_gap > math.radians(150):
            best_mid += sign * math.radians(25)
            self._angle_signs[atom_id] = -sign
        return (math.cos(best_mid), math.sin(best_mid))

    def _activate_select_mode_from_parent(self) -> None:
        dlg = self.parent()
        if dlg is not None and hasattr(dlg, "select_btn") and hasattr(dlg, "_toggle_select"):
            dlg._toggle_select(True)
        else:
            self.select_mode = True
            self.erase_mode = False

    def place_template(self, name: str, center: QPoint | None = None, attach_to: int | None = None, radius: int = 60, bond_length: int = 60):
        tpl = SKETCH_RING_TEMPLATES.get(name)
        if tpl is None:
            return
        n, elems, orders = tpl

        if attach_to is not None:
            base = next((n for n in self.nodes if n["id"] == attach_to), None)
            if base is None:
                return
            bx, by = base["pos"].x(), base["pos"].y()
            ux, uy = self._compute_extension_vector(attach_to)
            cx = int(bx + ux * (bond_length + radius))
            cy = int(by + uy * (bond_length + radius))
            cpt = QPoint(cx, cy)
            pre_nodes = [nid["id"] for nid in self.nodes]
            self.add_ring(n, center=cpt, radius=radius, elements=elems, bond_orders=orders)
            new_ids = [nid["id"] for nid in self.nodes if nid["id"] not in pre_nodes]
            if not new_ids:
                return
            best = None
            best_d = None
            for nid in new_ids:
                nn = next((no for no in self.nodes if no["id"] == nid), None)
                if nn:
                    d = (nn["pos"].x() - bx) ** 2 + (nn["pos"].y() - by) ** 2
                    if best_d is None or d < best_d:
                        best_d = d
                        best = nn
            if best is None:
                return
            bond = _bond_make(attach_to, best["id"], 1, 0)
            self.bonds.append(bond)
            self._after_sketch_edit()
            for nid in new_ids:
                nobj = next((no for no in self.nodes if no["id"] == nid), None)
                if nobj:
                    self._push_undo("add_node", nobj)
            for b in list(self.bonds):
                ba, bb, _, __ = _bond_unpack(b)
                if ba in new_ids and bb in new_ids:
                    self._push_undo("add_bond", b)
            self._push_undo("add_bond", bond)
            return

        cpt = center if center is not None else self.rect().center()
        pre_nodes = [nid["id"] for nid in self.nodes]
        self.add_ring(n, center=cpt, radius=radius, elements=elems, bond_orders=orders)
        new_ids = [nid["id"] for nid in self.nodes if nid["id"] not in pre_nodes]
        for nid in new_ids:
            nobj = next((no for no in self.nodes if no["id"] == nid), None)
            if nobj:
                self._push_undo("add_node", nobj)
        for b in list(self.bonds):
            ba, bb, _, __ = _bond_unpack(b)
            if ba in new_ids and bb in new_ids:
                self._push_undo("add_bond", b)
        self._after_sketch_edit()

    def add_carbon_to(self, atom_id: int, bond_length: int = 60):
        base = next((n for n in self.nodes if n["id"] == atom_id), None)
        if base is None:
            return
        bx, by = base["pos"].x(), base["pos"].y()
        neigh = [b for b in self.bonds if _bond_unpack(b)[0] == atom_id or _bond_unpack(b)[1] == atom_id]
        angles = []
        for bond in neigh:
            a, b, _, __ = _bond_unpack(bond)
            nid = b if a == atom_id else a
            nnode = next((n for n in self.nodes if n["id"] == nid), None)
            if nnode:
                ang = math.atan2(nnode["pos"].y() - by, nnode["pos"].x() - bx)
                angles.append(ang)
        if not angles:
            ux, uy = 1.0, 0.0
        elif len(angles) == 1:
            neigh_ang = angles[0]
            sign = self._angle_signs.get(atom_id, 1)
            ang_new = neigh_ang + math.pi + sign * math.radians(60)
            self._angle_signs[atom_id] = -sign
            ux, uy = math.cos(ang_new), math.sin(ang_new)
        else:
            angles = sorted(angles)
            max_gap = -1
            best_mid = 0
            for i in range(len(angles)):
                a1 = angles[i]
                a2 = angles[(i + 1) % len(angles)] if i + 1 < len(angles) else angles[0] + 2 * math.pi
                gap = a2 - a1 if a2 >= a1 else (a2 + 2 * math.pi - a1)
                if gap > max_gap:
                    max_gap = gap
                    best_mid = a1 + gap / 2.0
            ang_new = best_mid
            sign = self._angle_signs.get(atom_id, 1)
            if max_gap > math.radians(150):
                ang_new += sign * math.radians(30)
                self._angle_signs[atom_id] = -sign
            ux, uy = math.cos(ang_new), math.sin(ang_new)

        MIN_BOND = 30
        bond_length = max(bond_length, MIN_BOND)
        nx = int(bx + ux * bond_length)
        ny = int(by + uy * bond_length)
        dist = math.hypot(nx - bx, ny - by)
        if dist < MIN_BOND:
            nx = int(bx + ux * MIN_BOND)
            ny = int(by + uy * MIN_BOND)

        nid = self.next_id
        self.next_id += 1
        node = {"id": nid, "pos": QPoint(nx, ny), "element": "C"}
        self.nodes.append(node)
        pst = self.active_bond_stereo if self.active_bond_stereo in (1, 2) else 0
        self.bonds.append(_bond_make(atom_id, nid, 1, pst))
        self.update()

