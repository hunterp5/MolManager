"""Plot dialog: matplotlib scatter linked to the main table."""

from __future__ import annotations

__all__ = ["PlotDialog"]

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import proj3d
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSlider,
    QVBoxLayout,
)

from ..utils import safe_float


class PlotDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Plot Data")
        self.resize(800, 600)

        self._proj3d = proj3d

        l = QVBoxLayout(self)

        self.figure = Figure(figsize=(6, 4))
        self.canvas = FigureCanvas(self.figure)
        l.addWidget(self.canvas)

        main_h = QHBoxLayout()

        left_v = QVBoxLayout()
        self.toolbar = NavigationToolbar(self.canvas, self)
        left_v.addWidget(self.toolbar)
        left_v.addWidget(self.canvas)
        main_h.addLayout(left_v, 3)

        ctrl_v = QVBoxLayout()
        gb_mode = QGroupBox("Plot Mode")
        gbm_lyt = QHBoxLayout(gb_mode)
        gbm_lyt.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["2D", "3D"])
        gbm_lyt.addWidget(self.mode_combo)
        gbm_lyt.addStretch()
        ctrl_v.addWidget(gb_mode)

        gb_sel = QGroupBox("Axes")
        gb_sel_lyt = QFormLayout(gb_sel)
        self.x_combo = QComboBox()
        gb_sel_lyt.addRow("X:", self.x_combo)
        self.y_combo = QComboBox()
        gb_sel_lyt.addRow("Y:", self.y_combo)
        self.z_combo = QComboBox()
        gb_sel_lyt.addRow("Z:", self.z_combo)
        ctrl_v.addWidget(gb_sel)

        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._plot_scope_has_selection = n_sel > 0
        self.only_selected_cb = QCheckBox("Plot only selected rows")
        self._only_selected_scope_prefix = "Plot only selected rows"
        if self._plot_scope_has_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        ctrl_v.addWidget(self.only_selected_cb)

        self.plot_btn = QPushButton("Plot")
        self.plot_btn.clicked.connect(self.plot)
        ctrl_v.addWidget(self.plot_btn)
        self.reset_btn = QPushButton("Reset Plot")
        self.reset_btn.clicked.connect(self.reset_plot)
        ctrl_v.addWidget(self.reset_btn)

        cols = list(self.parent_app.global_bounds.keys()) if getattr(self.parent_app, "global_bounds", None) else []
        if not cols:
            cols = [h for h in self.parent_app.headers[2:]]
        self.x_combo.addItems(cols)
        self.y_combo.addItems(cols)
        self.z_combo.addItems(cols)

        gb_ranges = QGroupBox("Axis Ranges")
        gb_r_lyt = QVBoxLayout(gb_ranges)
        xr = QHBoxLayout()
        xr.addWidget(QLabel("Xmin:"))
        self.xmin = QLineEdit()
        self.xmin.setFixedWidth(80)
        xr.addWidget(self.xmin)
        xr.addWidget(QLabel("Xmax:"))
        self.xmax = QLineEdit()
        self.xmax.setFixedWidth(80)
        xr.addWidget(self.xmax)
        gb_r_lyt.addLayout(xr)
        self.x_slider_min = QSlider(Qt.Horizontal)
        self.x_slider_max = QSlider(Qt.Horizontal)
        gb_r_lyt.addWidget(self.x_slider_min)
        gb_r_lyt.addWidget(self.x_slider_max)

        yr = QHBoxLayout()
        yr.addWidget(QLabel("Ymin:"))
        self.ymin = QLineEdit()
        self.ymin.setFixedWidth(80)
        yr.addWidget(self.ymin)
        yr.addWidget(QLabel("Ymax:"))
        self.ymax = QLineEdit()
        self.ymax.setFixedWidth(80)
        yr.addWidget(self.ymax)
        gb_r_lyt.addLayout(yr)
        self.y_slider_min = QSlider(Qt.Horizontal)
        self.y_slider_max = QSlider(Qt.Horizontal)
        gb_r_lyt.addWidget(self.y_slider_min)
        gb_r_lyt.addWidget(self.y_slider_max)

        zr = QHBoxLayout()
        self.zmin_label = QLabel("Zmin:")
        zr.addWidget(self.zmin_label)
        self.zmin = QLineEdit()
        self.zmin.setFixedWidth(80)
        zr.addWidget(self.zmin)
        self.zmax_label = QLabel("Zmax:")
        zr.addWidget(self.zmax_label)
        self.zmax = QLineEdit()
        self.zmax.setFixedWidth(80)
        zr.addWidget(self.zmax)
        gb_r_lyt.addLayout(zr)
        self.z_slider_min = QSlider(Qt.Horizontal)
        self.z_slider_max = QSlider(Qt.Horizontal)
        gb_r_lyt.addWidget(self.z_slider_min)
        gb_r_lyt.addWidget(self.z_slider_max)

        ctrl_v.addWidget(gb_ranges)
        ctrl_v.addStretch()
        main_h.addLayout(ctrl_v, 1)
        l.addLayout(main_h)

        self.x_scale = 100
        self.y_scale = 100
        self.z_scale = 100
        self.plotted_oids = []
        self._highlight_artist = None
        self.canvas.mpl_connect("pick_event", self._on_pick)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        self.x_combo.currentIndexChanged.connect(self._on_axis_change)
        self.y_combo.currentIndexChanged.connect(self._on_axis_change)
        self.z_combo.currentIndexChanged.connect(self._on_axis_change)
        self.xmin.editingFinished.connect(self.plot)
        self.xmax.editingFinished.connect(self.plot)
        self.ymin.editingFinished.connect(self.plot)
        self.ymax.editingFinished.connect(self.plot)
        self.zmin.editingFinished.connect(self.plot)
        self.zmax.editingFinished.connect(self.plot)
        self.x_slider_min.valueChanged.connect(lambda v: self._slider_to_edit(self.x_slider_min, self.xmin, self.x_scale))
        self.x_slider_max.valueChanged.connect(lambda v: self._slider_to_edit(self.x_slider_max, self.xmax, self.x_scale))
        self.y_slider_min.valueChanged.connect(lambda v: self._slider_to_edit(self.y_slider_min, self.ymin, self.y_scale))
        self.y_slider_max.valueChanged.connect(lambda v: self._slider_to_edit(self.y_slider_max, self.ymax, self.y_scale))
        self.z_slider_min.valueChanged.connect(lambda v: self._slider_to_edit(self.z_slider_min, self.zmin, self.z_scale))
        self.z_slider_max.valueChanged.connect(lambda v: self._slider_to_edit(self.z_slider_max, self.zmax, self.z_scale))

        self.z_combo.setVisible(False)
        self.zmin.setVisible(False)
        self.zmax.setVisible(False)
        self.zmin_label.setVisible(False)
        self.zmax_label.setVisible(False)
        self.z_slider_min.setVisible(False)
        self.z_slider_max.setVisible(False)

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._force_close = False

        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WidgetWithChildrenShortcut)
        esc.activated.connect(self._escape_asks_close)

    def _user_confirms_close(self) -> bool:
        return (
            QMessageBox.question(
                self,
                "Close Plot",
                "Are you sure you want to close the plot window?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )

    def _escape_asks_close(self) -> None:
        if self._user_confirms_close():
            self._force_close = True
            self.close()

    def plot(self):
        xname = self.x_combo.currentText()
        yname = self.y_combo.currentText()
        if not xname or not yname:
            return

        all_pts = []
        h_map = {h: i for i, h in enumerate(self.parent_app.headers)}
        xi = h_map.get(xname)
        yi = h_map.get(yname)
        if xi is None or yi is None:
            return

        n_sel_now = len(self.parent_app._selected_logical_rows()) if self.parent_app is not None else 0
        only_sel = n_sel_now > 0 and self.only_selected_cb.isChecked()
        allowed = self.parent_app._selected_oids_set() if only_sel else None
        if only_sel and not allowed:
            QMessageBox.warning(self, "Plot", "“Plot only selected rows” is checked but nothing is selected.")
            return

        for oid in self.parent_app.mols.keys():
            if allowed is not None and oid not in allowed:
                continue
            r = self.parent_app.get_row_by_id(oid)
            if r == -1:
                continue
            m = self.parent_app._table_model
            xv = safe_float(m.cell_text(r, xi))
            yv = safe_float(m.cell_text(r, yi))
            if xv is not None and yv is not None:
                all_pts.append((oid, xv, yv))

        try:
            xmin = float(self.xmin.text()) if self.xmin.text().strip() else None
        except Exception:
            xmin = None
        try:
            xmax = float(self.xmax.text()) if self.xmax.text().strip() else None
        except Exception:
            xmax = None
        try:
            ymin = float(self.ymin.text()) if self.ymin.text().strip() else None
        except Exception:
            ymin = None
        try:
            ymax = float(self.ymax.text()) if self.ymax.text().strip() else None
        except Exception:
            ymax = None

        fx, fy, fz = [], [], []
        self.plotted_oids = []
        is3d = self.mode_combo.currentText() == "3D"
        zname = self.z_combo.currentText() if is3d else None
        zi = h_map.get(zname) if is3d else None
        for oid, xv, yv in all_pts:
            if is3d:
                r = self.parent_app.get_row_by_id(oid)
                zv = safe_float(self.parent_app._table_model.cell_text(r, zi)) if zi is not None else None
                if zv is None:
                    continue
            else:
                zv = None
            if xmin is not None and xv < xmin:
                continue
            if xmax is not None and xv > xmax:
                continue
            if ymin is not None and yv < ymin:
                continue
            if ymax is not None and yv > ymax:
                continue
            if is3d and (self.zmin.text().strip() or self.zmax.text().strip()):
                try:
                    zmin = float(self.zmin.text()) if self.zmin.text().strip() else None
                except Exception:
                    zmin = None
                try:
                    zmax = float(self.zmax.text()) if self.zmax.text().strip() else None
                except Exception:
                    zmax = None
                if zmin is not None and zv < zmin:
                    continue
                if zmax is not None and zv > zmax:
                    continue
            fx.append(xv)
            fy.append(yv)
            fz.append(zv)
            self.plotted_oids.append(oid)

        def set_axis_controls(vals, s_min, s_max, edit_min, edit_max, scale):
            if not vals:
                return
            lo = min(vals)
            hi = max(vals)
            if lo == hi:
                lo -= 1
                hi += 1
            s_lo = int(lo * scale)
            s_hi = int(hi * scale)
            s_min.blockSignals(True)
            s_max.blockSignals(True)
            try:
                s_min.setRange(s_lo, s_hi)
                s_max.setRange(s_lo, s_hi)
                s_min.setValue(s_lo)
                s_max.setValue(s_hi)
            except Exception:
                pass
            s_min.blockSignals(False)
            s_max.blockSignals(False)
            edit_min.setText(("{:.0f}" if scale == 1 else "{:.2f}").format(lo))
            edit_max.setText(("{:.0f}" if scale == 1 else "{:.2f}").format(hi))

        x_scale = getattr(self, "x_scale", 100)
        y_scale = getattr(self, "y_scale", 100)
        z_scale = getattr(self, "z_scale", 100)
        set_axis_controls(fx, self.x_slider_min, self.x_slider_max, self.xmin, self.xmax, x_scale)
        set_axis_controls(fy, self.y_slider_min, self.y_slider_max, self.ymin, self.ymax, y_scale)
        if is3d:
            set_axis_controls(fz, self.z_slider_min, self.z_slider_max, self.zmin, self.zmax, z_scale)

        self.figure.clear()
        if is3d:
            ax = self.figure.add_subplot(111, projection="3d")
            sc = ax.scatter(fx, fy, fz, s=40, alpha=0.8, picker=5)
            ax.set_zlabel(zname)
        else:
            ax = self.figure.add_subplot(111)
            sc = ax.scatter(fx, fy, s=40, alpha=0.8, picker=5)
        ax.set_xlabel(xname)
        ax.set_ylabel(yname)
        if xmin is not None or xmax is not None:
            ax.set_xlim(left=xmin, right=xmax)
        if ymin is not None or ymax is not None:
            ax.set_ylim(bottom=ymin, top=ymax)
        ax.grid(True)
        self.canvas.draw()

        self._last_scatter = sc

    def _on_pick(self, event):
        try:
            ax = self._last_scatter.axes
            ind = None
            if hasattr(event, "ind") and getattr(event, "ind"):
                ind = int(event.ind[0])
            else:
                me = getattr(event, "mouseevent", None)
                if me is None:
                    return
                mx, my = me.x, me.y
                pts = []
                if hasattr(self._last_scatter, "get_offsets"):
                    offs = self._last_scatter.get_offsets()
                    for x, y in offs:
                        px, py = ax.transData.transform((x, y))
                        pts.append((px, py))
                else:
                    try:
                        xs = list(self._last_scatter._offsets3d[0])
                        ys = list(self._last_scatter._offsets3d[1])
                        zs = list(self._last_scatter._offsets3d[2])
                        for x, y, z in zip(xs, ys, zs):
                            x2, y2, _ = self._proj3d.proj_transform(x, y, z, ax.get_proj())
                            px, py = ax.transData.transform((x2, y2))
                            pts.append((px, py))
                    except Exception:
                        pts = []
                if not pts:
                    return
                best_i, best_d = None, None
                for i, (px, py) in enumerate(pts):
                    d = (px - mx) ** 2 + (py - my) ** 2
                    if best_d is None or d < best_d:
                        best_d, best_i = d, i
                ind = best_i

            if ind is None or ind >= len(self.plotted_oids):
                return

            oid = self.plotted_oids[ind]
            self.parent_app.table.clearSelection()
            r = self.parent_app.get_row_by_id(oid)
            if r != -1:
                self.parent_app.table.selectRow(r)
                try:
                    idx = self.parent_app._table_model.index(r, 0)
                    self.parent_app.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
                except Exception:
                    pass

            try:
                if getattr(self, "_highlight_artist", None) is not None:
                    try:
                        self._highlight_artist.remove()
                    except Exception:
                        pass
                if hasattr(self._last_scatter, "get_offsets"):
                    offs = self._last_scatter.get_offsets()
                    if len(offs) > ind:
                        x, y = offs[ind]
                        (self._highlight_artist,) = ax.plot(
                            [x],
                            [y],
                            marker="o",
                            markersize=12,
                            markeredgecolor="red",
                            markerfacecolor="none",
                            markeredgewidth=2,
                        )
                self.canvas.draw()
            except Exception:
                pass
        except Exception:
            pass

    def _on_axis_change(self):
        xname = self.x_combo.currentText()
        yname = self.y_combo.currentText()

        def setup(name, s_min, s_max, edit_min, edit_max):
            meta = self.parent_app.global_bounds.get(name)
            scale = 1 if meta and meta.get("is_int") else 100
            if meta:
                lo, hi = meta["min"], meta["max"]
                s_min.setRange(int(lo * scale), int(hi * scale))
                s_max.setRange(int(lo * scale), int(hi * scale))
                s_min.setValue(int(lo * scale))
                s_max.setValue(int(hi * scale))
                edit_min.setText(("{:.0f}" if scale == 1 else "{:.2f}").format(lo))
                edit_max.setText(("{:.0f}" if scale == 1 else "{:.2f}").format(hi))
            else:
                s_min.setRange(0, 100)
                s_max.setRange(0, 100)
                s_min.setValue(0)
                s_max.setValue(100)
                edit_min.setText("")
                edit_max.setText("")
            return scale

        self.x_scale = setup(xname, self.x_slider_min, self.x_slider_max, self.xmin, self.xmax)
        self.y_scale = setup(yname, self.y_slider_min, self.y_slider_max, self.ymin, self.ymax)
        if getattr(self, "z_combo", None) and self.z_combo.isVisible():
            zname = self.z_combo.currentText()
            self.z_scale = setup(zname, self.z_slider_min, self.z_slider_max, self.zmin, self.zmax)
        else:
            self.z_scale = 100
        self.plot()

    def _on_mode_change(self, idx):
        is3d = self.mode_combo.currentText() == "3D"
        self.z_combo.setVisible(is3d)
        self.zmin.setVisible(is3d)
        self.zmax.setVisible(is3d)
        try:
            self.zmin_label.setVisible(is3d)
            self.zmax_label.setVisible(is3d)
        except Exception:
            pass
        self.z_slider_min.setVisible(is3d)
        self.z_slider_max.setVisible(is3d)
        cols = list(self.parent_app.global_bounds.keys()) if getattr(self.parent_app, "global_bounds", None) else [h for h in self.parent_app.headers[2:]]
        self.z_combo.blockSignals(True)
        self.z_combo.clear()
        self.z_combo.addItems(cols)
        self.z_combo.blockSignals(False)
        try:
            self._on_axis_change()
        except Exception:
            try:
                self.plot()
            except Exception:
                pass

    def _slider_to_edit(self, slider, edit, scale):
        try:
            v = slider.value() / (scale if scale else 100)
            edit.setText("{:.0f}".format(v) if scale == 1 else "{:.2f}".format(v))
            try:
                self.plot()
            except Exception:
                pass
        except Exception:
            pass

    def reset_plot(self):
        try:
            self._on_axis_change()
            if self.mode_combo.currentText() == "3D":
                try:
                    self._on_mode_change(self.mode_combo.currentIndex())
                except Exception:
                    pass
            self.plot()
        except Exception:
            pass

    def closeEvent(self, event):
        if self._force_close:
            self._force_close = False
            event.accept()
            return
        if self._user_confirms_close():
            event.accept()
        else:
            event.ignore()
