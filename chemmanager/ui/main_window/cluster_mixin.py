"""Cluster dialog and cluster worker signal handlers (kept out of :class:`ChemistryMixin`)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox


class ClusterMixin:
    def open_cluster_dialog(self) -> None:
        if not self.headers:
            QMessageBox.information(
                self,
                "Cluster",
                "Open a file or start a session first.",
            )
            return
        from ..dialogs import ClusterDialog

        dlg = getattr(self, "_cluster_dialog", None)
        if dlg is not None:
            try:
                dlg._refresh_structure_sources()
                self._sync_dialog_only_selected_scope(dlg)
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                return
            except RuntimeError:
                self._cluster_dialog = None
        d = ClusterDialog(self)
        self._cluster_dialog = d
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.destroyed.connect(self._on_cluster_dialog_destroyed)
        d.show()
        d.raise_()
        d.activateWindow()

    def _on_cluster_dialog_destroyed(self) -> None:
        self._cluster_dialog = None

    def on_cluster_failed(self, message: str) -> None:
        self._clear_tool_progress()
        if message == "Cancelled.":
            self.status_label.setText(self._consume_partial_results_notice() or "Cancelled.")
        else:
            self.status_label.setText("Ready.")
        dlg = getattr(self, "_cluster_dialog", None)
        if dlg is not None:
            try:
                dlg.enable_run_after_job()
            except RuntimeError:
                pass
        if message and message != "Cancelled.":
            QMessageBox.warning(self, "Cluster", message or "Clustering failed.")

    def on_cluster_explore_finished(self, results: list) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        dlg = getattr(self, "_cluster_dialog", None)
        if dlg is not None:
            try:
                dlg.fill_explore_results(results)
            except RuntimeError:
                pass
