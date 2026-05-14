"""2D canvas rendering for :class:`~chemmanager.ui.sketcher.widget.SketchWidget`."""

from __future__ import annotations

import math
from typing import Any

from PyQt5.QtCore import QPoint, QPointF, QRect, Qt
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from .bonds import _bond_unpack
from .constants import (
    WEDGE_TRI_HALF_WIDTH as _WEDGE_TRI_HALF_WIDTH,
    WILDCARD_ELEMENT,
)
from .element_colors import rdkit_default_element_rgb
from .wildcards import _normalize_wildcard_elements


class SketchWidgetPaintMixin:
    # ---------- Rendering ----------
    def _draw_formal_charge_indicator(
        self,
        p: QPainter,
        _n: dict[str, Any],
        pos: QPoint,
        ch: int,
        *,
        symbol: str | None = None,
        font_pt: int = 12,
        baseline_offset_y: float = 4.0,
    ) -> None:
        """Formal charge as a superscript tight to the element symbol (or top-right if symbol is hidden)."""
        if not ch:
            return
        if ch == 1:
            label = "+"
        elif ch == -1:
            label = "−"
        else:
            label = f"{ch:+d}".replace("-", "−")

        ch_pt = max(7, int(round(font_pt * 0.56)))
        f_ch = QFont("Sans", ch_pt, QFont.Bold)
        f_ch.setStyleHint(QFont.SansSerif)
        fm_ch = QFontMetrics(f_ch)

        if symbol:
            f_sym = QFont("Sans", int(font_pt), QFont.Bold)
            f_sym.setStyleHint(QFont.SansSerif)
            fm_sym = QFontMetrics(f_sym)
            tw_sym = fm_sym.horizontalAdvance(symbol) if hasattr(fm_sym, "horizontalAdvance") else fm_sym.width(symbol)
            sym_baseline_y = float(pos.y() + baseline_offset_y)
            sym_left = float(pos.x()) - tw_sym / 2.0
            # Superscript: smaller type, raised relative to symbol cap height
            raise_y = max(7.0, float(fm_sym.ascent()) * 0.42)
            bx = sym_left + tw_sym + 1.0
            by = sym_baseline_y - raise_y + float(fm_ch.ascent()) * 0.88
        else:
            # Implicit element (e.g. methylene carbon with no "C" label): charge above-right of atom center
            bx = float(pos.x() + 8)
            by = float(pos.y() - 4 + fm_ch.ascent() * 0.85)

        path = QPainterPath()
        path.addText(bx, by, f_ch, label)
        halo = 1.6 if ch_pt <= 8 else 2.0
        p.strokePath(path, QPen(QColor(255, 255, 255), halo, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, QBrush(QColor(190, 25, 35)))

    def _bond_avoidance_axes(self, nid: int, pos: QPoint) -> tuple[float, float, float, float]:
        """
        Unit vectors (ux, uy) pointing away from the average bond direction from this atom,
        and (vx, vy) perpendicular (to stack labels without sitting on bonds).
        """
        pts: list[QPoint] = []
        for b in self.bonds:
            a, b0, _, __ = _bond_unpack(b)
            other: int | None = None
            if a == nid:
                other = b0
            elif b0 == nid:
                other = a
            if other is None:
                continue
            on = next((x for x in self.nodes if x["id"] == other), None)
            if on is not None:
                pts.append(on["pos"])
        if not pts:
            ux, uy = -0.82, -0.58
            ln = math.hypot(ux, uy)
            ux, uy = ux / ln, uy / ln
        else:
            sx = sy = 0.0
            for pt in pts:
                dx = float(pt.x() - pos.x())
                dy = float(pt.y() - pos.y())
                L = math.hypot(dx, dy)
                if L > 1e-6:
                    sx += dx / L
                    sy += dy / L
            ln = math.hypot(sx, sy)
            if ln < 1e-6:
                ux, uy = 0.0, -1.0
            else:
                ux, uy = -sx / ln, -sy / ln
        vx, vy = -uy, ux
        return ux, uy, vx, vy

    def _annotation_offset(self, nid: int, pos: QPoint, slot: int, dist_mul: float = 1.0) -> QPoint:
        """Place annotations in different quadrants: 0=u (away from bonds), 1=v, 2=-u, 3=-v."""
        ux, uy, vx, vy = self._bond_avoidance_axes(nid, pos)
        d = (self.radius + 11) * dist_mul
        dirs = ((ux, uy), (vx, vy), (-ux, -uy), (-vx, -vy))
        dx, dy = dirs[slot % 4]
        return QPoint(int(round(dx * d)), int(round(dy * d)))

    def _draw_cip_stereo_indicator(self, p: QPainter, n: dict[str, Any], pos: QPoint, code: str) -> None:
        if not code:
            return
        off = self._annotation_offset(n["id"], pos, slot=0)
        font = QFont("Sans", 9, QFont.Bold)
        font.setStyleHint(QFont.SansSerif)
        fm = QFontMetrics(font)
        tw = fm.width(code)
        bx = pos.x() + off.x() - tw // 2
        by = pos.y() + off.y() + fm.ascent() // 3
        path = QPainterPath()
        path.addText(float(bx), float(by), font, code)
        p.strokePath(path, QPen(QColor(255, 255, 255), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, QBrush(QColor(20, 85, 155)))

    def _draw_alkene_ez_bond_label(self, p: QPainter, ni: dict, nj: dict, code: str) -> None:
        """E or Z at the double-bond midpoint, offset perpendicular to the bond (distinct from R/S atom labels)."""
        if code not in ("E", "Z"):
            return
        x1, y1 = float(ni["pos"].x()), float(ni["pos"].y())
        x2, y2 = float(nj["pos"].x()), float(nj["pos"].y())
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        dx, dy = x2 - x1, y2 - y1
        L = max((dx * dx + dy * dy) ** 0.5, 1.0)
        px, py = -dy / L, dx / L
        off = 14.0
        bx, by = mx + px * off, my + py * off
        font = QFont("Sans", 9, QFont.Bold)
        font.setStyleHint(QFont.SansSerif)
        fm = QFontMetrics(font)
        tw = fm.width(code)
        tx = bx - tw / 2
        ty = by + fm.ascent() / 3
        path = QPainterPath()
        path.addText(float(tx), float(ty), font, code)
        p.strokePath(path, QPen(QColor(255, 255, 255), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, QBrush(QColor(145, 75, 12)))

    def _draw_element_symbol_label(
        self,
        p: QPainter,
        pos: QPoint,
        text: str,
        font_pt: int = 12,
        fill: QColor | None = None,
        *,
        halo_w: float = 3.2,
        baseline_offset_y: float = 4,
    ) -> None:
        """Bold element symbol with a light halo so it reads clearly over bonds."""
        if fill is None:
            fill = QColor(0, 0, 0)
        font = QFont("Sans", font_pt, QFont.Bold)
        font.setStyleHint(QFont.SansSerif)
        fm = QFontMetrics(font)
        tw = fm.width(text)
        x = float(pos.x() - tw / 2)
        y = float(pos.y() + baseline_offset_y)
        path = QPainterPath()
        path.addText(x, y, font, text)
        p.strokePath(path, QPen(QColor(255, 255, 255), halo_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, QBrush(fill))

    def _maybe_draw_stereo_label(self, p: QPainter, n: dict[str, Any], pos: QPoint) -> None:
        nid = n["id"]
        if nid not in self._stereo_label_node_ids:
            return
        code = self._stereo_cip_by_node_id.get(nid)
        if code in ("R", "S"):
            self._draw_cip_stereo_indicator(p, n, pos, code)

    def _set_stereo_label_visible(self, node_id: int, visible: bool) -> None:
        if visible:
            self._stereo_label_node_ids.add(node_id)
        else:
            self._stereo_label_node_ids.discard(node_id)
        self._after_sketch_edit()

    def _wedge_triangle_points(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> tuple[QPointF, QPointF, QPointF]:
        """Narrow apex at (x1,y1), wide base centered on (x2,y2), perpendicular to the bond."""
        L = max(math.hypot(x2 - x1, y2 - y1), 1.0)
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        px, py = -uy, ux
        w = _WEDGE_TRI_HALF_WIDTH
        apex = QPointF(x1, y1)
        left = QPointF(x2 - px * w, y2 - py * w)
        right = QPointF(x2 + px * w, y2 + py * w)
        return apex, left, right

    def _draw_wedge_bond(self, p: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
        apex, left, right = self._wedge_triangle_points(x1, y1, x2, y2)
        poly = QPolygonF([apex, left, right])
        p.setPen(QPen(QColor(30, 30, 30), 1))
        p.setBrush(QColor(40, 40, 40))
        p.drawPolygon(poly)
        p.setBrush(Qt.NoBrush)

    def _draw_hash_bond(self, p: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
        apex, left, right = self._wedge_triangle_points(x1, y1, x2, y2)
        path = QPainterPath()
        path.moveTo(apex)
        path.lineTo(left)
        path.lineTo(right)
        path.closeSubpath()
        dash = QPen(QColor(40, 40, 40), 2)
        dash.setStyle(Qt.DashLine)
        dash.setCapStyle(Qt.FlatCap)
        dash.setJoinStyle(Qt.MiterJoin)
        p.setBrush(Qt.NoBrush)
        p.setPen(dash)
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)

    def _draw_bond_line(self, p: QPainter, ni: dict, nj: dict, order: int, stereo: int) -> None:
        """Wedge/hash are only for tetrahedral stereo on **single** bonds; multi bonds are always plain parallels."""
        if order != 1:
            stereo = 0
        x1, y1 = float(ni["pos"].x()), float(ni["pos"].y())
        x2, y2 = float(nj["pos"].x()), float(nj["pos"].y())
        dx, dy = x2 - x1, y2 - y1
        L = max((dx * dx + dy * dy) ** 0.5, 1.0)
        ux, uy = -dy / L, dx / L
        pen = QPen(QColor(40, 40, 40))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if order == 1 and stereo == 1:
            self._draw_wedge_bond(p, x1, y1, x2, y2)
        elif order == 1 and stereo == 2:
            self._draw_hash_bond(p, x1, y1, x2, y2)
        elif order == 1:
            p.drawLine(ni["pos"], nj["pos"])
        else:
            spacing = 6
            offs = [-spacing / 2, spacing / 2] if order == 2 else [-spacing, 0, spacing]
            for off in offs:
                ox, oy = ux * off, uy * off
                p.drawLine(int(x1 + ox), int(y1 + oy), int(x2 + ox), int(y2 + oy))

    def _draw_radial_glow(
        self,
        p: QPainter,
        pos: QPoint,
        outer_px: float,
        core: QColor,
        *,
        mid_stop: float = 0.42,
    ) -> None:
        """Soft halo behind an atom (no hard ring)."""
        cx, cy = float(pos.x()), float(pos.y())
        rad = QRadialGradient(cx, cy, outer_px)
        c0 = QColor(core)
        c0.setAlpha(min(255, int(core.alpha() * 1.25)))
        rad.setColorAt(0.0, c0)
        rad.setColorAt(mid_stop, core)
        edge = QColor(core)
        edge.setAlpha(0)
        rad.setColorAt(1.0, edge)
        p.setBrush(QBrush(rad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(pos, int(outer_px), int(outer_px))

    def _bond_glow_segments(
        self, ni: dict[str, Any], nj: dict[str, Any], order: int, stereo: int
    ) -> list[tuple[float, float, float, float]]:
        """Polylines matching ``_draw_bond_line`` geometry (for under-glow strokes)."""
        if order != 1:
            stereo = 0
        x1, y1 = float(ni["pos"].x()), float(ni["pos"].y())
        x2, y2 = float(nj["pos"].x()), float(nj["pos"].y())
        dx, dy = x2 - x1, y2 - y1
        L = max((dx * dx + dy * dy) ** 0.5, 1.0)
        ux, uy = -dy / L, dx / L
        out: list[tuple[float, float, float, float]] = []
        if order == 1 and stereo == 1:
            apex, left, right = self._wedge_triangle_points(x1, y1, x2, y2)
            mx = (left.x() + right.x()) * 0.5
            my = (left.y() + right.y()) * 0.5
            out.append((x1, y1, mx, my))
            return out
        if order == 1 and stereo == 2:
            apex, left, right = self._wedge_triangle_points(x1, y1, x2, y2)
            mx = (left.x() + right.x()) * 0.5
            my = (left.y() + right.y()) * 0.5
            out.append((x1, y1, mx, my))
            return out
        if order == 1:
            out.append((x1, y1, x2, y2))
            return out
        spacing = 6
        offs = [-spacing / 2, spacing / 2] if order == 2 else [-spacing, 0, spacing]
        for off in offs:
            ox, oy = ux * off, uy * off
            out.append((x1 + ox, y1 + oy, x2 + ox, y2 + oy))
        return out

    def _stroke_segment_glow(
        self,
        p: QPainter,
        segments: list[tuple[float, float, float, float]],
        color: QColor,
        *,
        max_width: float = 12.0,
        layers: int = 7,
    ) -> None:
        """Wide translucent strokes stacked under the bond for a soft glow."""
        for k in range(layers):
            frac = (layers - k) / max(layers, 1)
            w = 2.5 + (max_width - 2.5) * frac
            a = int(max(5, min(88, float(color.alpha()) * (0.10 + 0.14 * frac))))
            pen = QPen(QColor(color.red(), color.green(), color.blue(), min(90, a)))
            pen.setWidthF(w)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            for x1, y1, x2, y2 in segments:
                p.drawLine(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))

    def _draw_bond_accent_overlay(
        self, p: QPainter, ni: dict[str, Any], nj: dict[str, Any], order: int, stereo: int, pen: QPen
    ) -> None:
        """Thin highlight on top of bond geometry (selection / hover)."""
        p.setPen(pen)
        st = stereo if order == 1 else 0
        if order == 1 and st == 1:
            self._draw_wedge_bond(p, float(ni["pos"].x()), float(ni["pos"].y()), float(nj["pos"].x()), float(nj["pos"].y()))
        elif order == 1 and st == 2:
            self._draw_hash_bond(p, float(ni["pos"].x()), float(ni["pos"].y()), float(nj["pos"].x()), float(nj["pos"].y()))
        elif order == 1:
            p.drawLine(ni["pos"], nj["pos"])
        else:
            x1, y1 = ni["pos"].x(), ni["pos"].y()
            x2, y2 = nj["pos"].x(), nj["pos"].y()
            dx, dy = x2 - x1, y2 - y1
            L = max((dx * dx + dy * dy) ** 0.5, 1.0)
            ux, uy = -dy / L, dx / L
            spacing = 6
            offs = [-spacing / 2, spacing / 2] if order == 2 else [-spacing, 0, spacing]
            for off in offs:
                ox, oy = ux * off, uy * off
                p.drawLine(int(x1 + ox), int(y1 + oy), int(x2 + ox), int(y2 + oy))

    def paintEvent(self, ev):
        self._ensure_bonds_sanitized()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        p.setRenderHint(QPainter.Antialiasing, True)

        pen = QPen(QColor(40, 40, 40))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)

        _g_bond_atom = QColor(34, 88, 188, 64)
        _g_atom_sel = QColor(22, 68, 168, 78)
        _g_atom_hover = QColor(34, 88, 188, 64)
        _g_issue = QColor(220, 86, 68, 44)
        _g_stereo = QColor(158, 92, 210, 42)
        _g_chiral_ok = QColor(0, 150, 130, 26)

        # Bond highlight under-glow (selected and/or hovered): same hue; stronger when selected
        bond_glow: set[int] = set(self.selected_bond_indices)
        hb: int | None = None
        if isinstance(self.hover, tuple) and self.hover[0] == "bond":
            try:
                hb = int(self.hover[1])
            except (TypeError, ValueError):
                hb = None
            if hb is not None and 0 <= hb < len(self.bonds):
                bond_glow.add(hb)
        for bi in sorted(bond_glow):
            if bi < 0 or bi >= len(self.bonds):
                continue
            a, b, order, stereo = _bond_unpack(self.bonds[bi])
            ni = next((n for n in self.nodes if n["id"] == a), None)
            nj = next((n for n in self.nodes if n["id"] == b), None)
            if not ni or not nj:
                continue
            segs = self._bond_glow_segments(ni, nj, order, stereo)
            is_sel = bi in self.selected_bond_indices
            self._stroke_segment_glow(
                p,
                segs,
                _g_bond_atom,
                max_width=17.0 if is_sel else 16.0,
                layers=11 if is_sel else 10,
            )

        # bonds
        for bi, bond in enumerate(self.bonds):
            i, j, order, stereo = _bond_unpack(bond)
            ni = next((n for n in self.nodes if n["id"] == i), None)
            nj = next((n for n in self.nodes if n["id"] == j), None)
            if not ni or not nj:
                continue
            self._draw_bond_line(p, ni, nj, order, stereo)
            if order == 2:
                ez = (getattr(self, "_alkene_ez_by_bond_index", None) or {}).get(bi)
                if ez in ("E", "Z"):
                    self._draw_alkene_ez_bond_label(p, ni, nj, str(ez))

        # Accent on top: selected bonds (darker), then hovered bond (same ink when not selected)
        accent_sel = QPen(QColor(22, 72, 168, 242))
        accent_sel.setCapStyle(Qt.RoundCap)
        accent_sel.setJoinStyle(Qt.RoundJoin)
        for bi in sorted(self.selected_bond_indices):
            if bi < 0 or bi >= len(self.bonds):
                continue
            a, b, order, stereo = _bond_unpack(self.bonds[bi])
            ni = next((n for n in self.nodes if n["id"] == a), None)
            nj = next((n for n in self.nodes if n["id"] == b), None)
            if ni and nj:
                accent_sel.setWidthF(2.55)
                self._draw_bond_accent_overlay(p, ni, nj, order, stereo, accent_sel)

        if hb is not None and 0 <= hb < len(self.bonds):
            a, b, order, stereo = _bond_unpack(self.bonds[hb])
            ni = next((n for n in self.nodes if n["id"] == a), None)
            nj = next((n for n in self.nodes if n["id"] == b), None)
            if ni and nj:
                accent_h = QPen(QColor(22, 72, 168, 242))
                accent_h.setCapStyle(Qt.RoundCap)
                accent_h.setJoinStyle(Qt.RoundJoin)
                accent_h.setWidthF(2.15 if hb not in self.selected_bond_indices else 2.65)
                self._draw_bond_accent_overlay(p, ni, nj, order, stereo, accent_h)

        # Very subtle halo for stereocenters with resolved R/S (replaces solid green disk)
        stereo_issue = getattr(self, "_chiral_stereo_issue_ids", set())
        for nid in self._chiral_center_ids:
            if nid in stereo_issue:
                continue
            n = next((x for x in self.nodes if x["id"] == nid), None)
            if n:
                self._draw_radial_glow(p, n["pos"], self.radius * 2.05, _g_chiral_ok, mid_stop=0.36)

        # Atom halos: issues / stereo / selection / hover (drawn under labels)
        for n in self.nodes:
            pos = n["pos"]
            nid = n["id"]
            if nid in self._valence_violations or nid in self._charge_violations:
                self._draw_radial_glow(p, pos, self.radius * 2.35, _g_issue, mid_stop=0.4)
            if nid in stereo_issue:
                self._draw_radial_glow(p, pos, self.radius * 2.2, _g_stereo, mid_stop=0.4)
            if nid in self.selected_nodes:
                self._draw_radial_glow(p, pos, self.radius * 2.45, _g_atom_sel, mid_stop=0.42)
            elif self.hover == nid:
                self._draw_radial_glow(p, pos, self.radius * 2.38, _g_atom_hover, mid_stop=0.41)

        _atom_pen = QPen(QColor(40, 40, 40), 2)
        _atom_pen.setCapStyle(Qt.RoundCap)
        _atom_pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(_atom_pen)

        # atoms (labels on top of halos)
        for n in self.nodes:
            pos = n["pos"]
            el = n["element"]

            if el == WILDCARD_ELEMENT:
                self._draw_element_symbol_label(
                    p, pos, "*", 15, QColor(92, 48, 142), halo_w=3.5, baseline_offset_y=5
                )
                sub = ",".join(_normalize_wildcard_elements(n))
                if len(sub) > 10:
                    sub = sub[:9] + "..."
                self._draw_element_symbol_label(
                    p,
                    QPoint(pos.x(), pos.y() + 11),
                    sub,
                    7,
                    QColor(62, 66, 74),
                    halo_w=2.2,
                    baseline_offset_y=3,
                )
                ch = n.get("charge", 0)
                if ch:
                    self._draw_formal_charge_indicator(
                        p, n, pos, ch, symbol="*", font_pt=15, baseline_offset_y=5
                    )
                self._maybe_draw_stereo_label(p, n, pos)
                continue

            if el == "C":
                has_conn = any((_bond_unpack(b)[0] == n["id"] or _bond_unpack(b)[1] == n["id"]) for b in self.bonds)
                if not has_conn:
                    c = rdkit_default_element_rgb(el)
                    self._draw_element_symbol_label(p, pos, el, 12, QColor(*c))
                ch = n.get("charge", 0)
                if ch:
                    self._draw_formal_charge_indicator(
                        p,
                        n,
                        pos,
                        ch,
                        symbol="C" if not has_conn else None,
                        font_pt=12,
                        baseline_offset_y=4.0,
                    )
                self._maybe_draw_stereo_label(p, n, pos)
                continue

            c = rdkit_default_element_rgb(el)
            self._draw_element_symbol_label(p, pos, el, 12, QColor(*c))

            ch = n.get("charge", 0)
            if ch:
                self._draw_formal_charge_indicator(
                    p, n, pos, ch, symbol=el, font_pt=12, baseline_offset_y=4.0
                )
            self._maybe_draw_stereo_label(p, n, pos)

        p.setBrush(Qt.NoBrush)

        # drag line
        if self._is_dragging and self._drag_start is not None and self._drag_pos is not None:
            start = next((n for n in self.nodes if n["id"] == self._drag_start), None)
            if start:
                sp = start["pos"]
                dpen = QPen(QColor(30, 120, 200))
                dpen.setStyle(Qt.DashLine)
                dpen.setWidth(2)
                p.setPen(dpen)
                p.drawLine(sp, self._drag_pos)

        # selection rectangle
        if self._selection_rect is not None:
            sel_pen = QPen(QColor(130, 160, 220, 140))
            sel_pen.setStyle(Qt.DashLine)
            sel_pen.setWidth(1)
            p.setPen(sel_pen)
            r = self._selection_rect
            p.drawRect(r.left(), r.top(), r.width(), r.height())
