"""QSAR tool window (Tools menu)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox


class QsarMixin:
    def open_qsar_dialog(self) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                "QSAR",
                "Open a file or add rows with activity and descriptor data first.",
            )
            return
        from ..dialogs.qsar import QSARDialog
        from ..singleton_modeless_dialog import reuse_or_show_modeless_singleton

        def _factory():
            d = QSARDialog(self)
            self._prepare_tool_dialog(d)
            d.setAttribute(Qt.WA_DeleteOnClose, True)
            return d

        reuse_or_show_modeless_singleton(
            self,
            "_qsar_dialog",
            _factory,
            self._on_qsar_dialog_destroyed,
            on_reused_visible=lambda dlg: self._sync_dialog_only_selected_scope(dlg),
        )

    def _on_qsar_dialog_destroyed(self) -> None:
        self._qsar_dialog = None
