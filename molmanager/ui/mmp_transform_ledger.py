# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Modeless dialog: rank MMP transforms by support and activity effect."""

from __future__ import annotations

import logging
from typing import Any

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

from ..mmp_analysis import (
    MmpPair,
    TransformSummary,
    aggregate_transforms,
    assemble_mmp_table_annotations,
    pairs_for_summary,
    pairs_involving_oid,
    reference_oids_in_pairs,
)
from .qt_widget_utils import make_window_minimizable
from .widgets import NumericTableWidgetItem

logger = logging.getLogger(__name__)

_COL_CORE = 0
_COL_TRANSFORM = 1
_COL_N = 2
_COL_MEDIAN = 3
_COL_MEAN = 4
_COL_WIN = 5
_COL_IMPROVE = 6
_COL_WORSEN = 7
_COL_MIN = 8
_COL_MAX = 9

_HEADERS = (
    "Core",
    "Transform",
    "n",
    "Median Δ",
    "Mean Δ",
    "Win %",
    "Improve",
    "Worsen",
    "Min Δ",
    "Max Δ",
)

_ROW_HEIGHT = 78
_CORE_COL_WIDTH = 110
_TRANSFORM_COL_WIDTH = 220
_FRAG_W = 88
_FRAG_H = 64
_ARROW_W = 28
_CORE_W = 96


def _try_configure_drawer(drawer, width: int) -> None:
    try:
        from ..structure_draw import configure_mol_drawer as cfg

        cfg(drawer, width)
    except Exception:
        pass


def _fmt_delta(value: float) -> str:
    text = f"{value:.4g}"
    if text.startswith("-") or text == "0":
        return text
    return f"+{text}"


class MmpTransformLedgerDialog(QDialog):
    """Browse MMP transforms aggregated from a pair list; drill into the pair browser."""

    def __init__(
        self,
        parent: Any,
        pairs: list[MmpPair],
        *,
        activity_column: str,
    ):
        super().__init__(parent)
        self._app = parent
        self._pairs: list[MmpPair] = []
        self._pairs_for_summaries: list[MmpPair] = []
        self._summaries: list[TransformSummary] = []
        self._activity_column = activity_column
        self._reference_oid: int | None = None
        self._preview_cache: dict[tuple, QPixmap] = {}
        self._transform_icon_cache: dict[str, QPixmap] = {}
        self._core_icon_cache: dict[str, QPixmap] = {}

        self.setWindowTitle("MMP Transform Ledger")
        self.resize(980, 640)
        self.setMinimumSize(720, 480)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        root = QVBoxLayout(self)
        self._meta = QLabel()
        self._meta.setWordWrap(True)
        root.addWidget(self._meta)

        ref_row = QHBoxLayout()
        self._btn_ref_selected = QPushButton("Selected as Reference")
        self._btn_ref_selected.setToolTip(
            "Use the currently selected table molecule as the reference (exactly one row). "
            "Shows only pairs involving that molecule; transforms and Δ are oriented "
            "as reference → partner."
        )
        self._btn_ref_clear = QPushButton("Clear Reference")
        self._btn_ref_clear.setToolTip(
            "Show all pairs again (lexicographic transform orientation)."
        )
        self._btn_ref_clear.setEnabled(False)
        self._ref_status = QLabel("Reference: (all pairs)")
        self._ref_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ref_row.addWidget(self._btn_ref_selected)
        ref_row.addWidget(self._btn_ref_clear)
        ref_row.addWidget(self._ref_status, 1)
        root.addLayout(ref_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Substring match on core or transform SMILES…")
        self._filter_edit.setClearButtonEnabled(True)
        filter_row.addWidget(self._filter_edit, 1)
        root.addLayout(filter_row)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setIconSize(QSize(_TRANSFORM_COL_WIDTH - 12, _ROW_HEIGHT - 8))
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        self._table.setSortingEnabled(True)
        # Prevent long content / header modes from driving an expanding min-size loop on Windows.
        self._table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_CORE, QHeaderView.Fixed)
        hdr.setSectionResizeMode(_COL_TRANSFORM, QHeaderView.Fixed)
        self._table.setColumnWidth(_COL_CORE, _CORE_COL_WIDTH)
        self._table.setColumnWidth(_COL_TRANSFORM, _TRANSFORM_COL_WIDTH)
        for col in range(2, len(_HEADERS)):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            self._table.setColumnWidth(col, 78)
        root.addWidget(self._table, 1)

        preview = QHBoxLayout()
        self._core_panel = self._make_frag_panel("Core (unchanged)")
        self._from_panel = self._make_frag_panel("From")
        self._to_panel = self._make_frag_panel("To")
        preview.addWidget(self._core_panel["box"], 1)
        preview.addWidget(self._from_panel["box"], 1)
        preview.addWidget(self._to_panel["box"], 1)
        root.addLayout(preview)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._detail)

        actions = QHBoxLayout()
        self._btn_browse = QPushButton("Browse pairs")
        self._btn_browse.setToolTip("Open the pair browser for the selected transform")
        self._btn_browse_all = QPushButton("Browse all pairs")
        self._btn_browse_all.setToolTip("Open the pair browser with every MMP pair")
        self._btn_cliffs = QPushButton("Activity Cliffs")
        self._btn_cliffs.setToolTip(
            "Open the activity-cliff scatter for the pairs currently shown in this ledger "
            "(respects reference filter)."
        )
        self._btn_network = QPushButton("Pair Network")
        self._btn_network.setToolTip(
            "Open the MMP pair neighborhood graph for the pairs currently shown in this ledger "
            "(respects reference filter)."
        )
        self._btn_apply = QPushButton("Apply to seed")
        self._btn_apply.setToolTip(
            "Apply the selected transform to the seed molecule (main-table selection, "
            "or the reference if nothing is selected) and add product(s) to the table."
        )
        self._btn_apply.setEnabled(False)
        self._btn_write = QPushButton("Write to table")
        self._btn_write.setToolTip(
            "Write MMP_Partners / MMP_Transforms / MMP_Delta columns for all pairs"
        )
        actions.addWidget(self._btn_browse)
        actions.addWidget(self._btn_browse_all)
        actions.addWidget(self._btn_cliffs)
        actions.addWidget(self._btn_network)
        actions.addWidget(self._btn_apply)
        actions.addWidget(self._btn_write)
        actions.addStretch()
        root.addLayout(actions)

        self._filter_edit.textChanged.connect(self._apply_filter)
        self._btn_ref_selected.clicked.connect(self._reference_from_table_selection)
        self._btn_ref_clear.clicked.connect(self._clear_reference)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellDoubleClicked.connect(lambda *_a: self._browse_selected())
        self._btn_browse.clicked.connect(self._browse_selected)
        self._btn_browse_all.clicked.connect(self._browse_all)
        self._btn_cliffs.clicked.connect(self._open_activity_cliffs)
        self._btn_network.clicked.connect(self._open_pair_network)
        self._btn_apply.clicked.connect(self._apply_selected_to_seed)
        self._btn_write.clicked.connect(self._write_all_to_table)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self._refresh_previews)

        make_window_minimizable(self)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.set_pairs(pairs, activity_column=activity_column)

    def _make_frag_panel(self, title: str) -> dict[str, Any]:
        box = QGroupBox(title)
        lyt = QVBoxLayout(box)
        struct = QLabel()
        struct.setAlignment(Qt.AlignCenter)
        struct.setMinimumHeight(140)
        struct.setMaximumHeight(200)
        struct.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        struct.setStyleSheet(
            "background-color: palette(base); border: 1px solid palette(mid); border-radius: 4px;"
        )
        smiles = QLabel("—")
        smiles.setAlignment(Qt.AlignCenter)
        smiles.setTextInteractionFlags(Qt.TextSelectableByMouse)
        smiles.setWordWrap(False)
        lyt.addWidget(struct, 1)
        lyt.addWidget(smiles)
        return {"box": box, "struct": struct, "smiles": smiles}

    def set_pairs(self, pairs: list[MmpPair], *, activity_column: str | None = None) -> None:
        """Replace the underlying pair list and rebuild the ledger table."""
        self._pairs = list(pairs or [])
        if activity_column is not None:
            self._activity_column = activity_column
        # Drop reference if it no longer appears in the new pair set.
        if self._reference_oid is not None:
            present = set(reference_oids_in_pairs(self._pairs))
            if self._reference_oid not in present:
                self._reference_oid = None
        self._preview_cache.clear()
        self._transform_icon_cache.clear()
        self._core_icon_cache.clear()
        self._rebuild_summaries()

    def _visible_pairs(self) -> list[MmpPair]:
        if self._reference_oid is None:
            return list(self._pairs)
        return pairs_involving_oid(self._pairs, self._reference_oid)

    def _rebuild_summaries(self) -> None:
        self._summaries = aggregate_transforms(
            self._pairs, reference_oid=self._reference_oid
        )
        self._pairs_for_summaries = self._visible_pairs()
        n_pairs = len(self._pairs_for_summaries)
        n_tx = len(self._summaries)
        act = self._activity_column or "activity"
        if self._reference_oid is not None:
            self._meta.setText(
                f"Reference ID {self._reference_oid}  ·  "
                f"{n_tx} transform(s) from {n_pairs} pair(s)  ·  "
                f"Δ = partner − reference ({act})"
            )
            self._ref_status.setText(f"Reference: ID {self._reference_oid}")
        else:
            self._meta.setText(
                f"{n_tx} core+transform rule(s) from {n_pairs} pair(s)  ·  "
                f"Δ relative to {act}  ·  sides ordered lexicographically"
            )
            self._ref_status.setText("Reference: (all pairs)")
        self._btn_ref_clear.setEnabled(self._reference_oid is not None)
        self._populate_table()
        self._btn_browse_all.setEnabled(n_pairs > 0)
        self._btn_write.setEnabled(len(self._pairs) > 0)
        self._btn_apply.setEnabled(self._table.rowCount() > 0)
        if self._table.rowCount() > 0:
            self._table.selectRow(0)
        else:
            self._clear_preview()
            self._btn_apply.setEnabled(False)

    def _reference_from_table_selection(self) -> None:
        app = self._app
        if app is None:
            return
        try:
            oids = sorted(int(o) for o in app._selected_oids_set())
        except Exception:
            oids = []
        if len(oids) != 1:
            try:
                app.status_label.setText(
                    "MMP ledger: select exactly one table molecule for reference."
                )
            except Exception:
                pass
            return
        oid = oids[0]
        present = set(reference_oids_in_pairs(self._pairs))
        if oid not in present:
            try:
                app.status_label.setText(
                    f"MMP ledger: ID {oid} has no matched pairs in this result set."
                )
            except Exception:
                pass
            return
        self._reference_oid = oid
        self._rebuild_summaries()

    def _clear_reference(self) -> None:
        if self._reference_oid is None:
            return
        self._reference_oid = None
        self._rebuild_summaries()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_timer.start()

    def _populate_table(self) -> None:
        filter_text = (self._filter_edit.text() or "").strip().lower()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for summary in self._summaries:
            hay = f"{summary.core} {summary.transform}".lower()
            if filter_text and filter_text not in hay:
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._set_row(row, summary)
        self._table.setSortingEnabled(True)
        self._btn_browse.setEnabled(self._table.rowCount() > 0)

    def _set_row(self, row: int, summary: TransformSummary) -> None:
        key = (summary.core, summary.transform)

        core_item = QTableWidgetItem()
        core_item.setData(Qt.UserRole, key)
        core_item.setToolTip(summary.core or "(no core SMILES)")
        core_item.setFlags(core_item.flags() & ~Qt.ItemIsEditable)
        core_pm = self._core_icon(summary.core)
        if core_pm is not None and not core_pm.isNull():
            core_item.setIcon(QIcon(core_pm))
        else:
            core_item.setText("—")
            core_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, _COL_CORE, core_item)

        transform_item = QTableWidgetItem()
        transform_item.setData(Qt.UserRole, key)
        transform_item.setToolTip(summary.transform)
        transform_item.setFlags(transform_item.flags() & ~Qt.ItemIsEditable)
        pm = self._transform_icon(summary)
        if pm is not None and not pm.isNull():
            transform_item.setIcon(QIcon(pm))
        else:
            transform_item.setText("→")
            transform_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, _COL_TRANSFORM, transform_item)
        self._table.setRowHeight(row, _ROW_HEIGHT)

        def _num(col: int, value: float | int, *, display: str | None = None) -> None:
            item = NumericTableWidgetItem()
            item.setData(Qt.EditRole, float(value))
            item.setText(display if display is not None else str(value))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, col, item)

        _num(_COL_N, summary.n, display=str(summary.n))
        _num(_COL_MEDIAN, summary.median_delta, display=_fmt_delta(summary.median_delta))
        _num(_COL_MEAN, summary.mean_delta, display=_fmt_delta(summary.mean_delta))
        win_pct = 100.0 * summary.win_rate
        _num(_COL_WIN, win_pct, display=f"{win_pct:.0f}%")
        _num(_COL_IMPROVE, summary.n_improve, display=str(summary.n_improve))
        _num(_COL_WORSEN, summary.n_worsen, display=str(summary.n_worsen))
        _num(_COL_MIN, summary.min_delta, display=_fmt_delta(summary.min_delta))
        _num(_COL_MAX, summary.max_delta, display=_fmt_delta(summary.max_delta))

    def _core_icon(self, core: str) -> QPixmap | None:
        key = core or ""
        cached = self._core_icon_cache.get(key)
        if cached is not None:
            return cached
        if not key:
            return None
        pm = self._render_frag_fixed(key, _CORE_W, _FRAG_H)
        if pm is not None and not pm.isNull():
            self._core_icon_cache[key] = pm
        return pm

    def _transform_icon(self, summary: TransformSummary) -> QPixmap | None:
        cache_key = f"{summary.core}|{summary.transform}"
        cached = self._transform_icon_cache.get(cache_key)
        if cached is not None:
            return cached
        pm = self._render_transform_pair(summary.sidechain_from, summary.sidechain_to)
        if pm is not None and not pm.isNull():
            self._transform_icon_cache[cache_key] = pm
        return pm

    def _render_transform_pair(self, side_from: str, side_to: str) -> QPixmap | None:
        left = self._render_frag_fixed(side_from, _FRAG_W, _FRAG_H)
        right = self._render_frag_fixed(side_to, _FRAG_W, _FRAG_H)
        if left is None and right is None:
            return None
        if left is None:
            left = QPixmap(_FRAG_W, _FRAG_H)
            left.fill(Qt.transparent)
        if right is None:
            right = QPixmap(_FRAG_W, _FRAG_H)
            right.fill(Qt.transparent)
        total_w = _FRAG_W + _ARROW_W + _FRAG_W
        out = QPixmap(total_w, _FRAG_H)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        try:
            painter.drawPixmap(0, 0, left)
            painter.setPen(self.palette().color(self.palette().WindowText))
            painter.drawText(_FRAG_W, 0, _ARROW_W, _FRAG_H, Qt.AlignCenter, "→")
            painter.drawPixmap(_FRAG_W + _ARROW_W, 0, right)
        finally:
            painter.end()
        return out

    def _render_frag_fixed(self, smiles: str, pw: int, ph: int) -> QPixmap | None:
        if not smiles:
            return None
        cache_key = (smiles, pw, ph, "fixed")
        cached = self._preview_cache.get(cache_key)
        if cached is not None and not cached.isNull():
            return cached
        try:
            mol = Chem.MolFromSmiles(smiles)
        except Exception:
            mol = None
        if mol is None:
            return None
        pm = self._render_mol(mol, pw, ph)
        if pm is not None and not pm.isNull():
            self._preview_cache[cache_key] = pm
        return pm

    def _apply_filter(self, _text: str = "") -> None:
        prev = self._selected_key()
        self._populate_table()
        if prev:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, _COL_TRANSFORM)
                if item is not None and item.data(Qt.UserRole) == prev:
                    self._table.selectRow(row)
                    return
        if self._table.rowCount() > 0:
            self._table.selectRow(0)
        else:
            self._clear_preview()

    def _selected_key(self) -> tuple[str, str] | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), _COL_TRANSFORM)
        if item is None:
            item = self._table.item(rows[0].row(), _COL_CORE)
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        if isinstance(value, tuple) and len(value) == 2:
            return str(value[0]), str(value[1])
        return None

    def _selected_summary(self) -> TransformSummary | None:
        key = self._selected_key()
        if key is None:
            return None
        core, transform = key
        for s in self._summaries:
            if s.core == core and s.transform == transform:
                return s
        return None

    def _on_selection_changed(self) -> None:
        summary = self._selected_summary()
        self._btn_browse.setEnabled(summary is not None)
        self._btn_apply.setEnabled(summary is not None)
        if summary is None:
            self._clear_preview()
            return
        self._core_panel["box"].setTitle("Core (unchanged)")
        self._from_panel["box"].setTitle("From" + (" (reference)" if self._reference_oid is not None else ""))
        self._to_panel["box"].setTitle("To" + (" (partner)" if self._reference_oid is not None else ""))
        for panel, smiles in (
            (self._core_panel, summary.core),
            (self._from_panel, summary.sidechain_from),
            (self._to_panel, summary.sidechain_to),
        ):
            panel["smiles"].hide()
            panel["smiles"].setText("")
            panel["struct"].setToolTip(smiles or "")
        self._detail.setText(
            f"n={summary.n}  ·  "
            f"median Δ={_fmt_delta(summary.median_delta)}  ·  "
            f"win {100.0 * summary.win_rate:.0f}% "
            f"({summary.n_improve} improve / {summary.n_worsen} worsen"
            + (f" / {summary.n_flat} flat" if summary.n_flat else "")
            + ")"
        )
        self._detail.setToolTip(f"{summary.core}  |  {summary.transform}")
        self._refresh_previews()

    def _clear_preview(self) -> None:
        for panel in (self._core_panel, self._from_panel, self._to_panel):
            panel["struct"].clear()
            panel["struct"].setPixmap(QPixmap())
            panel["struct"].setText("")
            panel["struct"].setToolTip("")
            panel["smiles"].setText("—")
            panel["smiles"].setToolTip("")
        self._detail.setText("")
        self._detail.setToolTip("")

    def _refresh_previews(self) -> None:
        summary = self._selected_summary()
        if summary is None:
            return
        self._render_fragment(self._core_panel, summary.core)
        self._render_fragment(self._from_panel, summary.sidechain_from)
        self._render_fragment(self._to_panel, summary.sidechain_to)

    def _preview_pixel_size(self, label: QLabel) -> tuple[int, int, float]:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        lw = max(min(label.width(), 480), 160)
        lh = max(min(label.height(), 200), 120)
        return int(lw * dpr), int(lh * dpr), dpr

    def _render_fragment(self, panel: dict[str, Any], smiles: str) -> None:
        label: QLabel = panel["struct"]
        mol = None
        if smiles:
            try:
                mol = Chem.MolFromSmiles(smiles)
            except Exception:
                mol = None
        pw, ph, dpr = self._preview_pixel_size(label)
        if mol is None:
            label.clear()
            label.setPixmap(QPixmap())
            label.setText("(no fragment)")
            return
        cache_key = (smiles, pw, ph)
        cached = self._preview_cache.get(cache_key)
        if cached is not None and not cached.isNull():
            pm = cached
        else:
            pm = self._render_mol(mol, pw, ph)
            if pm is not None and not pm.isNull():
                self._preview_cache[cache_key] = pm
        if pm is None or pm.isNull():
            label.clear()
            label.setPixmap(QPixmap())
            label.setText("(render failed)")
            return
        if pm.width() != pw or pm.height() != ph:
            pm = pm.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        label.setPixmap(pm)
        label.setText("")

    def _render_mol(self, mol: Chem.Mol, pw: int, ph: int) -> QPixmap | None:
        try:
            drawer = rdMolDraw2D.MolDraw2DCairo(pw, ph)
            _try_configure_drawer(drawer, pw)
            rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
            drawer.FinishDrawing()
            img = QImage.fromData(drawer.GetDrawingText())
            return QPixmap.fromImage(img)
        except Exception:
            return None

    def _browse_selected(self) -> None:
        summary = self._selected_summary()
        app = self._app
        if summary is None or app is None:
            return
        subset = pairs_for_summary(self._pairs, summary)
        if not subset:
            return
        try:
            app._open_mmp_browser(subset, activity_column=self._activity_column)
        except Exception:
            pass

    def _browse_all(self) -> None:
        app = self._app
        visible = self._visible_pairs()
        if app is None or not visible:
            return
        try:
            app._open_mmp_browser(visible, activity_column=self._activity_column)
        except Exception:
            pass

    def _open_activity_cliffs(self) -> None:
        app = self._app
        visible = self._visible_pairs()
        if app is None or not visible:
            return
        try:
            app._open_activity_cliff_map(
                visible,
                activity_column=self._activity_column,
                x_mode="heavy_atoms",
            )
        except Exception:
            logger.exception("Open activity cliffs from ledger failed")

    def _open_pair_network(self) -> None:
        app = self._app
        visible = self._visible_pairs()
        if app is None or not visible:
            return
        try:
            app._open_mmp_neighborhood_map(
                visible,
                activity_column=self._activity_column,
            )
        except Exception:
            logger.exception("Open pair network from ledger failed")

    def _resolve_seed_oids(self) -> list[int]:
        """Table selection if any; otherwise the active reference OID."""
        app = self._app
        oids: list[int] = []
        if app is not None:
            try:
                oids = sorted(int(o) for o in app._selected_oids_set())
            except Exception:
                oids = []
        if oids:
            return oids
        if self._reference_oid is not None:
            return [int(self._reference_oid)]
        return []

    def _apply_selected_to_seed(self) -> None:
        summary = self._selected_summary()
        app = self._app
        if summary is None or app is None:
            return
        seed_oids = self._resolve_seed_oids()
        if not seed_oids:
            try:
                app.status_label.setText(
                    "MMP design: select a seed molecule in the table "
                    "(or set a reference)."
                )
            except Exception:
                pass
            return
        try:
            app.apply_mmp_transform_to_seeds(
                seed_oids,
                side_from=summary.sidechain_from,
                side_to=summary.sidechain_to,
                transform=summary.transform,
                core=summary.core,
            )
        except Exception:
            logger.exception("MMP apply to seed failed")

    def _write_all_to_table(self) -> None:
        app = self._app
        if app is None or not self._pairs:
            return
        rows, headers = assemble_mmp_table_annotations(
            self._pairs, activity_column=self._activity_column
        )
        if not rows:
            return
        try:
            app.on_calc_finished(rows, headers, progress_label="MMP")
        except Exception:
            pass
