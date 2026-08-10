"""ACS Document 1996–style 2D rendering for :class:`~molmanager.ui.sketcher.widget.SketchWidget`."""

from __future__ import annotations

import math

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF

from .acs_style import acs_sketch_style
from .chem import sketch_lone_pair_count
from .sketch_rdkit_paint import render_sketch_mol_to_pixmap, sketch_rdkit_paint_cache_key
from .constants import SKETCH_MEDIAN_BOND_PX, WILDCARD_ELEMENT
from .bonds import BOND_STEREO_DATIVE, BOND_STEREO_WAVY, BOND_STEREO_WEDGE, BOND_STEREO_HASH, _bond_unpack
from .element_colors import rdkit_default_element_rgb
from .wildcards import _is_wildcard_node, _normalize_wildcard_elements


class SketchWidgetPaintMixin:
    def _acs_style(self):
        med = getattr(self, "_median_bond_length_px", None)
        if med is None or med <= 0:
            med = float(SKETCH_MEDIAN_BOND_PX)
        return acs_sketch_style(med)

    def _acs_ink_pen(self, style, width: float | None = None) -> QPen:
        pen = QPen(QColor(*style.ink))
        pen.setWidthF(width if width is not None else style.bond_width_px)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _bond_parallel_offset(self, x1: float, y1: float, x2: float, y2: float, dist: float) -> tuple[float, float]:
        dx, dy = x2 - x1, y2 - y1
        length = max(math.hypot(dx, dy), 1.0)
        return (-dy / length * dist, dx / length * dist)

    def _wedge_triangle_points(
        self, x1: float, y1: float, x2: float, y2: float, half_width: float
    ) -> tuple[QPointF, QPointF, QPointF]:
        dx, dy = x2 - x1, y2 - y1
        length = max(math.hypot(dx, dy), 1.0)
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        return (
            QPointF(x1, y1),
            QPointF(x2 - px * half_width, y2 - py * half_width),
            QPointF(x2 + px * half_width, y2 + py * half_width),
        )

    def _draw_bond(self, p: QPainter, ni: dict, nj: dict, order: int, stereo: int, pen: QPen) -> None:
        if order != 1:
            stereo = 0
        x1, y1 = float(ni["pos"].x()), float(ni["pos"].y())
        x2, y2 = float(nj["pos"].x()), float(nj["pos"].y())
        style = self._acs_style()
        p.setPen(pen)
        if order == 1 and stereo == BOND_STEREO_WEDGE:
            apex, left, right = self._wedge_triangle_points(x1, y1, x2, y2, style.wedge_half_width_px)
            p.setBrush(QBrush(QColor(*style.ink)))
            p.drawPolygon(QPolygonF([apex, left, right]))
            p.setBrush(Qt.NoBrush)
            return
        if order == 1 and stereo == BOND_STEREO_HASH:
            apex, left, right = self._wedge_triangle_points(x1, y1, x2, y2, style.wedge_half_width_px)
            path = QPainterPath()
            path.moveTo(apex)
            path.lineTo(left)
            path.lineTo(right)
            path.closeSubpath()
            dash = QPen(QColor(*style.ink), max(1.0, pen.widthF() * 0.85))
            dash.setStyle(Qt.DashLine)
            p.setPen(dash)
            p.drawPath(path)
            return
        if order == 1 and stereo == BOND_STEREO_WAVY:
            self._draw_wavy_bond(p, x1, y1, x2, y2, pen)
            return
        if order == 1 and stereo == BOND_STEREO_DATIVE:
            self._draw_dative_bond(p, x1, y1, x2, y2, pen, style)
            return
        if order == 1:
            p.drawLine(ni["pos"], nj["pos"])
            return
        dist = style.double_bond_offset_px if order == 2 else style.triple_bond_offset_px
        ox, oy = self._bond_parallel_offset(x1, y1, x2, y2, dist / 2 if order == 2 else dist)
        if order == 2:
            offsets = [(-ox, -oy), (ox, oy)]
        else:
            offsets = [(-ox, -oy), (0.0, 0.0), (ox, oy)]
        for ox2, oy2 in offsets:
            p.drawLine(int(x1 + ox2), int(y1 + oy2), int(x2 + ox2), int(y2 + oy2))

    def _draw_wavy_bond(self, p: QPainter, x1: float, y1: float, x2: float, y2: float, pen: QPen) -> None:
        dx, dy = x2 - x1, y2 - y1
        length = max(math.hypot(dx, dy), 1.0)
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        amp = max(2.5, min(6.0, length * 0.08))
        waves = max(2, min(5, int(round(length / 14.0))))
        n = max(12, waves * 8)
        path = QPainterPath()
        for i in range(n + 1):
            t = i / n
            wave = math.sin(t * math.pi * waves) * amp
            x = x1 + dx * t + px * wave
            y = y1 + dy * t + py * wave
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _draw_dative_bond(
        self,
        p: QPainter,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        pen: QPen,
        style,
    ) -> None:
        """Arrow from atom1 → atom2 (donor → acceptor), matching RDKit DATIVE begin→end."""
        dx, dy = x2 - x1, y2 - y1
        length = max(math.hypot(dx, dy), 1.0)
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        head = max(6.0, min(11.0, length * 0.22))
        half = max(3.0, style.wedge_half_width_px * 0.55)
        sx2 = x2 - ux * head
        sy2 = y2 - uy * head
        p.setPen(pen)
        p.drawLine(QPointF(x1, y1), QPointF(sx2, sy2))
        p.setBrush(QBrush(pen.color()))
        p.setPen(Qt.NoPen)
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(x2, y2),
                    QPointF(sx2 + px * half, sy2 + py * half),
                    QPointF(sx2 - px * half, sy2 - py * half),
                ]
            )
        )
        p.setBrush(Qt.NoBrush)

    def _draw_element_label(
        self,
        p: QPainter,
        pos: QPoint,
        text: str,
        *,
        font_pt: int,
        fill: QColor,
    ) -> None:
        font = QFont("Helvetica", font_pt)
        font.setStyleHint(QFont.SansSerif)
        font.setWeight(QFont.Black)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text)
        x = float(pos.x() - tw / 2)
        y = float(pos.y() + fm.ascent() * 0.35)
        path = QPainterPath()
        path.addText(x, y, font, text)
        halo = QPen(QColor(255, 255, 255))
        halo.setWidthF(max(3.0, font_pt * 0.32))
        halo.setJoinStyle(Qt.RoundJoin)
        halo.setCapStyle(Qt.RoundCap)
        p.setPen(halo)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.fillPath(path, QBrush(fill))

    def _draw_formal_charge(
        self, p: QPainter, pos: QPoint, ch: int, *, symbol: str | None, font_pt: int
    ) -> None:
        if not ch:
            return
        if ch == 1:
            label = "+"
        elif ch == -1:
            label = "−"
        else:
            label = f"{ch:+d}".replace("-", "−")
        ch_pt = max(7, int(round(font_pt * 0.58)))
        font = QFont("Helvetica", ch_pt, QFont.Bold)
        fm = QFontMetrics(font)
        if symbol:
            sym_font = QFont("Helvetica", font_pt, QFont.Bold)
            sym_fm = QFontMetrics(sym_font)
            tw = (
                sym_fm.horizontalAdvance(symbol)
                if hasattr(sym_fm, "horizontalAdvance")
                else sym_fm.width(symbol)
            )
            bx = float(pos.x()) - tw / 2 + tw + 1.0
            by = float(pos.y()) - sym_fm.ascent() * 0.45 + fm.ascent() * 0.5
        else:
            bx = float(pos.x() + 7)
            by = float(pos.y() - 5 + fm.ascent())
        path = QPainterPath()
        path.addText(bx, by, font, label)
        p.fillPath(path, QBrush(QColor(180, 0, 0)))

    def _annotation_offset(self, nid: int, pos: QPoint, slot: int) -> QPoint:
        ux, uy, vx, vy = self._bond_avoidance_axes(nid, pos)
        d = self.radius + 9
        dirs = ((ux, uy), (vx, vy), (-ux, -uy), (-vx, -vy))
        dx, dy = dirs[slot % 4]
        return QPoint(int(round(dx * d)), int(round(dy * d)))

    def _bond_avoidance_axes(self, nid: int, pos: QPoint) -> tuple[float, float, float, float]:
        pts: list[QPoint] = []
        for b in self.bonds:
            a, b0, _, __ = _bond_unpack(b)
            other = b0 if a == nid else a if b0 == nid else None
            if other is None:
                continue
            on = next((x for x in self.nodes if x["id"] == other), None)
            if on is not None:
                pts.append(on["pos"])
        if not pts:
            ux, uy = -0.82, -0.58
        else:
            sx = sy = 0.0
            for pt in pts:
                dx = float(pt.x() - pos.x())
                dy = float(pt.y() - pos.y())
                length = math.hypot(dx, dy)
                if length > 1e-6:
                    sx += dx / length
                    sy += dy / length
            length = math.hypot(sx, sy)
            ux, uy = (-sx / length, -sy / length) if length > 1e-6 else (0.0, -1.0)
        return ux, uy, -uy, ux

    def _draw_cip_label(self, p: QPainter, pos: QPoint, nid: int, code: str) -> None:
        off = self._annotation_offset(nid, pos, 0)
        font = QFont("Helvetica", 9, QFont.Bold)
        fm = QFontMetrics(font)
        tw = fm.width(code)
        bx = pos.x() + off.x() - tw // 2
        by = pos.y() + off.y() + fm.ascent() // 3
        path = QPainterPath()
        path.addText(float(bx), float(by), font, code)
        p.fillPath(path, QBrush(QColor(0, 70, 140)))

    def _draw_alkene_ez_label(self, p: QPainter, ni: dict, nj: dict, code: str, *, font_pt: int) -> None:
        x1, y1 = float(ni["pos"].x()), float(ni["pos"].y())
        x2, y2 = float(nj["pos"].x()), float(nj["pos"].y())
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        ox, oy = self._bond_parallel_offset(x1, y1, x2, y2, 11.0)
        pt = max(7, font_pt - 2)
        font = QFont("Helvetica", pt)
        font.setItalic(True)
        fm = QFontMetrics(font)
        tw = fm.width(f"({code})")
        tx = mx + ox - tw / 2
        ty = my + oy + fm.ascent() / 3
        path = QPainterPath()
        path.addText(float(tx), float(ty), font, f"({code})")
        p.fillPath(path, QBrush(QColor(110, 110, 110)))

    def _draw_atom_selection_ring(self, p: QPainter, pos: QPoint, *, selected: bool, hover: bool) -> None:
        style = self._acs_style()
        r = float(self.radius) + style.atom_selection_radius_extra
        if selected:
            pen = QPen(QColor(0, 80, 200))
            pen.setWidthF(style.selection_pen_width)
        elif hover:
            pen = QPen(QColor(100, 140, 220))
            pen.setWidthF(style.hover_pen_width)
        else:
            return
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pos, int(r), int(r))

    def _draw_atom_issue_ring(self, p: QPainter, pos: QPoint, *, valence: bool, stereo: bool) -> None:
        if not valence and not stereo:
            return
        color = QColor(200, 50, 40) if valence else QColor(140, 80, 180)
        pen = QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pos, self.radius + 2, self.radius + 2)

    def _set_stereo_label_visible(self, node_id: int, visible: bool) -> None:
        if visible:
            self._stereo_label_node_ids.add(node_id)
        else:
            self._stereo_label_node_ids.discard(node_id)
        self._after_sketch_edit()

    def _should_paint_sketch_with_rdkit(self) -> bool:
        if not self.nodes or self.sketch_has_wildcards():
            return False
        # Wavy / dative use custom ACS glyphs; keep those sketches on the local painter.
        for b in self.bonds:
            if _bond_unpack(b)[3] in (BOND_STEREO_WAVY, BOND_STEREO_DATIVE):
                return False
        # Explicit C labels need ACS drawing so bonds stop at the atom label.
        if any(n.get("element") == "C" and n.get("explicit_carbon") for n in self.nodes):
            return False
        return True

    def _set_explicit_carbon_visible(self, node_id: int, visible: bool) -> None:
        n = next((x for x in self.nodes if x["id"] == node_id), None)
        if n is None or n.get("element") != "C":
            return
        old = bool(n.get("explicit_carbon"))
        on = bool(visible)
        if old == on:
            return
        if on:
            n["explicit_carbon"] = True
        else:
            n.pop("explicit_carbon", None)
        self._push_undo("chg_explicit_carbon", (node_id, old, on))
        self._invalidate_rdkit_sketch_paint_cache()
        self._after_sketch_edit()


    def _invalidate_rdkit_sketch_paint_cache(self) -> None:
        self._rdkit_sketch_paint_cache_key = None
        self._rdkit_sketch_paint_cache = None

    def _try_paint_rdkit_sketch_structure(self, p: QPainter) -> bool:
        if not self._should_paint_sketch_with_rdkit():
            return False
        cache_key = sketch_rdkit_paint_cache_key(self.nodes, self.bonds)
        view_bucket = max(1, int(round(max(1.0, float(getattr(self, "_view_scale", 1.0))) * 2)))
        cache_key = cache_key + (view_bucket, int(round(max(1.0, float(self.devicePixelRatioF())))))
        if (
            getattr(self, "_rdkit_sketch_paint_cache", None) is not None
            and getattr(self, "_rdkit_sketch_paint_cache_key", None) == cache_key
        ):
            pm, transform = self._rdkit_sketch_paint_cache
        else:
            ids = {n["id"] for n in self.nodes}
            out = self._mol_from_node_ids(ids, return_idmap=True)
            if out is None:
                return False
            mol, idmap = out
            if mol is None or mol.GetNumAtoms() == 0:
                return False
            med = float(getattr(self, "_median_bond_length_px", None) or SKETCH_MEDIAN_BOND_PX)
            view_scale = max(1.0, float(getattr(self, "_view_scale", 1.0)))
            dpr = max(1.0, float(self.devicePixelRatioF()))
            render_scale = max(2, min(4, int(round(max(view_scale, dpr) * 2))))
            rendered = render_sketch_mol_to_pixmap(
                mol,
                idmap,
                self.nodes,
                pad_px=max(32.0, med * 0.65),
                bond_scale_px=med,
                render_scale=render_scale,
            )
            if rendered is None:
                return False
            self._rdkit_sketch_paint_cache = rendered
            self._rdkit_sketch_paint_cache_key = cache_key
            pm, transform = rendered
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform, float(getattr(self, "_view_scale", 1.0)) != 1.0)
        p.setTransform(transform, combine=True)
        p.drawPixmap(0, 0, pm)
        p.restore()
        return True

    def _bond_highlight_stroke_width(self, style, order: int, *, selected: bool) -> float:
        """Width of a single centerline underlay that covers single/double/triple bond ink."""
        ink_w = max(1.0, float(style.bond_width_px))
        pad = float(style.bond_selection_extra_width if selected else 0.8)
        if order >= 3:
            span = 2.0 * float(style.triple_bond_offset_px)
        elif order == 2:
            span = float(style.double_bond_offset_px)
        else:
            span = 0.0
        return max(ink_w + pad * 2.0, span + ink_w * 2.0 + pad)

    def _draw_bond_highlight_underlay(
        self,
        p: QPainter,
        ni: dict,
        nj: dict,
        order: int,
        *,
        color: QColor,
        selected: bool,
        style,
    ) -> None:
        """Draw one rounded stroke along the bond axis (avoids thick multi-line redraws)."""
        pen = QPen(color)
        pen.setWidthF(self._bond_highlight_stroke_width(style, order, selected=selected))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(ni["pos"], nj["pos"])

    def _paint_sketch_bond_highlights(self, p: QPainter, style) -> None:
        selected_bonds = set(self.selected_bond_indices)
        hover_bond: int | None = None
        if isinstance(self.hover, tuple) and self.hover[0] == "bond":
            try:
                hover_bond = int(self.hover[1])
            except (TypeError, ValueError):
                hover_bond = None

        for bi in sorted(selected_bonds):
            if bi < 0 or bi >= len(self.bonds):
                continue
            a, b, order, _stereo = _bond_unpack(self.bonds[bi])
            ni = next((n for n in self.nodes if n["id"] == a), None)
            nj = next((n for n in self.nodes if n["id"] == b), None)
            if ni and nj:
                self._draw_bond_highlight_underlay(
                    p,
                    ni,
                    nj,
                    order,
                    color=QColor(0, 100, 220, 110),
                    selected=True,
                    style=style,
                )

        if hover_bond is not None and 0 <= hover_bond < len(self.bonds) and hover_bond not in selected_bonds:
            a, b, order, _stereo = _bond_unpack(self.bonds[hover_bond])
            ni = next((n for n in self.nodes if n["id"] == a), None)
            nj = next((n for n in self.nodes if n["id"] == b), None)
            if ni and nj:
                self._draw_bond_highlight_underlay(
                    p,
                    ni,
                    nj,
                    order,
                    color=QColor(80, 140, 230, 90),
                    selected=False,
                    style=style,
                )

    def _paint_sketch_atom_overlays(self, p: QPainter, style) -> None:
        stereo_issue = getattr(self, "_chiral_stereo_issue_ids", set())
        for n in self.nodes:
            pos = n["pos"]
            nid = n["id"]
            self._draw_atom_issue_ring(
                p,
                pos,
                valence=nid in self._valence_violations or nid in self._charge_violations,
                stereo=nid in stereo_issue,
            )
            self._draw_atom_selection_ring(
                p,
                pos,
                selected=nid in self.selected_nodes,
                hover=self.hover == nid,
            )

        for bi, bond in enumerate(self.bonds):
            i, j, order, stereo = _bond_unpack(bond)
            if order != 2:
                continue
            ez = (getattr(self, "_alkene_ez_by_bond_index", None) or {}).get(bi)
            if ez not in ("E", "Z"):
                continue
            ni = next((n for n in self.nodes if n["id"] == i), None)
            nj = next((n for n in self.nodes if n["id"] == j), None)
            if ni and nj:
                self._draw_alkene_ez_label(p, ni, nj, str(ez), font_pt=style.label_font_pt)

        for n in self.nodes:
            code = self._stereo_cip_by_node_id.get(n["id"])
            if n["id"] in self._stereo_label_node_ids and code in ("R", "S"):
                self._draw_cip_label(p, pos=n["pos"], nid=n["id"], code=code)

        if getattr(self, "show_lone_pairs", False):
            self._paint_sketch_lone_pairs(p, style)

    def _node_lone_pair_count(self, n: dict) -> int:
        if _is_wildcard_node(n):
            return 0
        nid = int(n["id"])
        fc = int(n.get("charge", 0) or 0)
        bond_sum = int(self._current_valence(nid))
        max_allowed = int(self._max_bond_order_sum_for_node(n, fc))
        implicit_h = max(0, max_allowed - bond_sum)
        return sketch_lone_pair_count(
            str(n.get("element", "")),
            formal_charge=fc,
            bond_order_sum=bond_sum,
            implicit_h=implicit_h,
        )

    def _lone_pair_directions(self, n: dict, count: int) -> list[float]:
        """Return ``count`` angles (radians) for lone-pair placement around node ``n``."""
        if count <= 0:
            return []
        nid = int(n["id"])
        cx, cy = float(n["pos"].x()), float(n["pos"].y())
        angles: list[float] = []
        for bond in self.bonds:
            a, b, _o, _s = _bond_unpack(bond)
            if a != nid and b != nid:
                continue
            other_id = b if a == nid else a
            other = next((x for x in self.nodes if x["id"] == other_id), None)
            if other is None:
                continue
            dx = float(other["pos"].x()) - cx
            dy = float(other["pos"].y()) - cy
            if math.hypot(dx, dy) < 1e-6:
                continue
            angles.append(math.atan2(dy, dx))
        if not angles:
            return [2.0 * math.pi * i / count - math.pi / 2.0 for i in range(count)]

        angles.sort()
        # Gaps between consecutive bond directions (including wrap-around).
        gaps: list[tuple[float, float]] = []
        for i, ang in enumerate(angles):
            nxt = angles[(i + 1) % len(angles)]
            span = (nxt - ang) % (2.0 * math.pi)
            if span < 1e-6:
                span = 2.0 * math.pi
            mid = (ang + span / 2.0) % (2.0 * math.pi)
            if mid > math.pi:
                mid -= 2.0 * math.pi
            gaps.append((span, mid))
        gaps.sort(key=lambda g: g[0], reverse=True)
        dirs = [g[1] for g in gaps[:count]]
        while len(dirs) < count:
            # Extra pairs: evenly fill remaining slots from the largest gap midpoints.
            dirs.append(2.0 * math.pi * len(dirs) / count)
        return dirs[:count]

    def _draw_lone_pair_at(self, p: QPainter, cx: float, cy: float, angle: float, style) -> None:
        """Draw one Lewis lone pair (two dots) along ``angle`` from the atom center."""
        r = max(10.0, float(getattr(self, "radius", 14)) * 0.95)
        pair_sep = max(3.0, float(style.bond_width_px) * 2.2)
        dot_r = max(1.35, float(style.bond_width_px) * 0.85)
        ux, uy = math.cos(angle), math.sin(angle)
        px, py = -uy, ux
        mx, my = cx + ux * r, cy + uy * r
        ink = QColor(*style.ink)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(ink))
        for sign in (-1.0, 1.0):
            p.drawEllipse(
                QPointF(mx + px * pair_sep * 0.5 * sign, my + py * pair_sep * 0.5 * sign),
                dot_r,
                dot_r,
            )
        p.setBrush(Qt.NoBrush)

    def _paint_sketch_lone_pairs(self, p: QPainter, style) -> None:
        for n in self.nodes:
            n_lp = self._node_lone_pair_count(n)
            if n_lp <= 0:
                continue
            cx, cy = float(n["pos"].x()), float(n["pos"].y())
            for ang in self._lone_pair_directions(n, n_lp):
                self._draw_lone_pair_at(p, cx, cy, ang, style)

    def _paint_sketch_structure_acs(self, p: QPainter, style) -> None:
        ink = self._acs_ink_pen(style)
        p.setPen(ink)
        p.setBrush(Qt.NoBrush)

        # Soft selection/hover underlays first so multi-bond ink stays crisp on top.
        self._paint_sketch_bond_highlights(p, style)

        for bi, bond in enumerate(self.bonds):
            i, j, order, stereo = _bond_unpack(bond)
            ni = next((n for n in self.nodes if n["id"] == i), None)
            nj = next((n for n in self.nodes if n["id"] == j), None)
            if not ni or not nj:
                continue
            self._draw_bond(p, ni, nj, order, stereo, ink)

        self._paint_sketch_atom_overlays(p, style)

        for n in self.nodes:
            pos = n["pos"]
            el = n["element"]
            if el == WILDCARD_ELEMENT:
                self._draw_element_label(p, pos, "*", font_pt=style.label_font_pt + 1, fill=QColor(80, 40, 120))
                sub = ",".join(_normalize_wildcard_elements(n))
                if len(sub) > 10:
                    sub = sub[:9] + "..."
                self._draw_element_label(
                    p, QPoint(pos.x(), pos.y() + 10), sub, font_pt=style.charge_font_pt, fill=QColor(60, 60, 60)
                )
                ch = n.get("charge", 0)
                if ch:
                    self._draw_formal_charge(p, pos, ch, symbol="*", font_pt=style.label_font_pt)
                continue

            if el == "C":
                has_conn = any((_bond_unpack(b)[0] == n["id"] or _bond_unpack(b)[1] == n["id"]) for b in self.bonds)
                show_c = bool(n.get("explicit_carbon")) or not has_conn
                if show_c:
                    c = rdkit_default_element_rgb(el)
                    self._draw_element_label(p, pos, el, font_pt=style.label_font_pt, fill=QColor(*c))
                ch = n.get("charge", 0)
                if ch:
                    self._draw_formal_charge(
                        p, pos, ch, symbol="C" if show_c else None, font_pt=style.label_font_pt
                    )
            else:
                c = rdkit_default_element_rgb(el)
                self._draw_element_label(p, pos, el, font_pt=style.label_font_pt, fill=QColor(*c))
                ch = n.get("charge", 0)
                if ch:
                    self._draw_formal_charge(p, pos, ch, symbol=el, font_pt=style.label_font_pt)

    def _paint_sketch_structure(self, p: QPainter, style) -> None:
        if self._try_paint_rdkit_sketch_structure(p):
            self._paint_sketch_bond_highlights(p, style)
            self._paint_sketch_atom_overlays(p, style)
            return
        self._paint_sketch_structure_acs(p, style)

    def paintEvent(self, ev) -> None:
        self._ensure_bonds_sanitized()
        style = self._acs_style()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        p.setRenderHint(QPainter.Antialiasing, True)

        p.save()
        self._apply_view_transform(p)
        self._paint_sketch_structure(p, style)
        if self._is_dragging and self._drag_start is not None and self._drag_pos is not None:
            start = next((n for n in self.nodes if n["id"] == self._drag_start), None)
            if start:
                dpen = QPen(QColor(60, 120, 200))
                dpen.setStyle(Qt.DashLine)
                dpen.setWidthF(max(1.0, style.bond_width_px * 0.75))
                p.setPen(dpen)
                p.drawLine(start["pos"], self._drag_pos)
        p.restore()

        if self._selection_rect is not None:
            r = self._selection_rect
            sel_pen = QPen(QColor(80, 120, 200))
            sel_pen.setStyle(Qt.DashLine)
            sel_pen.setWidth(1)
            p.setPen(sel_pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.left(), r.top(), r.width(), r.height())
