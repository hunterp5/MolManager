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
            title="Golden triangle plot",
            attr="_golden_triangle_dialog",
            destroyed=self._on_golden_triangle_dialog_destroyed,
        )

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
            self._plot_panel.setVisible(True)
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
