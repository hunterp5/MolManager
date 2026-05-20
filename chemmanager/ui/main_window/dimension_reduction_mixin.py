"""PCA, t-SNE, and UMAP dialogs (Data menu)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox


class DimensionReductionMixin:
    def open_pca_dialog(self) -> None:
        self._open_dimension_reduction_dialog("pca")

    def open_tsne_dialog(self) -> None:
        self._open_dimension_reduction_dialog("tsne")

    def open_umap_dialog(self) -> None:
        self._open_dimension_reduction_dialog("umap")

    def _open_dimension_reduction_dialog(self, kind: str) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                "Data",
                "Open a file or add rows so the table has numeric data to analyze.",
            )
            return
        if kind == "pca":
            from ..dialogs.dimensionality_reduction import PCADialog

            attr = "_pca_dialog"
            factory = lambda: PCADialog(self)
            destroyed = self._on_pca_dialog_destroyed
        elif kind == "tsne":
            from ..dialogs.dimensionality_reduction import TSNEVisualizationDialog

            attr = "_tsne_dialog"
            factory = lambda: TSNEVisualizationDialog(self)
            destroyed = self._on_tsne_dialog_destroyed
        else:
            from ..dialogs.dimensionality_reduction import UMAPVisualizationDialog

            attr = "_umap_dialog"
            factory = lambda: UMAPVisualizationDialog(self)
            destroyed = self._on_umap_dialog_destroyed

        dlg = getattr(self, attr, None)
        if dlg is not None:
            try:
                dlg._reload_columns()
                self._sync_dialog_only_selected_scope(dlg)
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                return
            except RuntimeError:
                setattr(self, attr, None)
        d = factory()
        setattr(self, attr, d)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.destroyed.connect(destroyed)
        d.show()
        d.raise_()
        d.activateWindow()

    def _on_pca_dialog_destroyed(self) -> None:
        self._pca_dialog = None

    def _on_tsne_dialog_destroyed(self) -> None:
        self._tsne_dialog = None

    def _on_umap_dialog_destroyed(self) -> None:
        self._umap_dialog = None
