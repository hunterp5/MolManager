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

"""BOILED-Egg and golden-triangle plots (Data menu)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox


class MedChemSpaceMixin:
    def open_boiled_egg_plot(self) -> None:
        self._open_medchem_space_dialog(
            plot_kind="boiled_egg",
            title="BOILED-Egg plot",
            attr="_boiled_egg_dialog",
            destroyed=self._on_boiled_egg_dialog_destroyed,
        )

    def open_golden_triangle_plot(self) -> None:
        self._open_medchem_space_dialog(
            plot_kind="golden_triangle",
            title="Golden Triangle plot",
            attr="_golden_triangle_dialog",
            destroyed=self._on_golden_triangle_dialog_destroyed,
        )

    def open_radar_plot(self) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                "Data",
                "Open a file or add rows with numeric columns to build a radar plot.",
            )
            return
        from ..dialogs.radar_plot import RadarPlotDialog

        dlg = getattr(self, "_radar_plot_dialog", None)
        if dlg is not None:
            try:
                self._sync_dialog_only_selected_scope(dlg)
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                return
            except RuntimeError:
                self._radar_plot_dialog = None
        d = RadarPlotDialog(self)
        self._radar_plot_dialog = d
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.destroyed.connect(self._on_radar_plot_dialog_destroyed)
        d.show()
        d.raise_()
        d.activateWindow()

    def _on_radar_plot_dialog_destroyed(self) -> None:
        self._radar_plot_dialog = None

    def _open_medchem_space_dialog(
        self,
        *,
        plot_kind: str,
        title: str,
        attr: str,
        destroyed,
    ) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                "Data",
                "Open a file or add rows with structures to plot medicinal chemistry space.",
            )
            return
        from ..dialogs.medchem_space import MedChemSpaceDialog

        dlg = getattr(self, attr, None)
        if dlg is not None:
            try:
                self._sync_dialog_only_selected_scope(dlg)
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                return
            except RuntimeError:
                setattr(self, attr, None)
        docked = getattr(self, "_docked_plot_widget", None)
        if docked is not None and getattr(docked, "_plot_kind", None) == plot_kind:
            self.show_docked_plot_panel()
            self._sync_dialog_only_selected_scope(docked)
            self.status_label.setText(f"{title}: docked beside the table.")
            return
        d = MedChemSpaceDialog(self, plot_kind=plot_kind, window_title=title)
        setattr(self, attr, d)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.destroyed.connect(destroyed)
        d.show()
        d.raise_()
        d.activateWindow()

    def _on_boiled_egg_dialog_destroyed(self) -> None:
        self._boiled_egg_dialog = None

    def _on_golden_triangle_dialog_destroyed(self) -> None:
        self._golden_triangle_dialog = None
