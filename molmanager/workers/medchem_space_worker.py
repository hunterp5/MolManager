"""Background BOILED-Egg / golden-triangle dataset builds."""

from __future__ import annotations

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

from ..medchem_space import MedChemRowSnapshot, MedChemSpaceBuildResult, build_medchem_space_result


class MedChemSpaceSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class MedChemSpaceWorker(QRunnable):
    def __init__(self, params: dict, signals: MedChemSpaceSignals):
        super().__init__()
        self.params = dict(params)
        self.signals = signals

    def run(self) -> None:
        try:
            snapshots = list(self.params.get("snapshots") or [])
            result = build_medchem_space_result(
                snapshots,
                plot_kind=str(self.params.get("plot_kind") or "boiled_egg"),
                tpsa_col=self.params.get("tpsa_col"),
                logp_col=self.params.get("logp_col"),
                mw_col=self.params.get("mw_col"),
                wlogp_col=self.params.get("wlogp_col"),
                use_table_columns_only=bool(self.params.get("use_table_columns_only")),
                max_plot_points=self.params.get("max_plot_points"),
                oid_smiles=dict(self.params.get("oid_smiles") or {}),
                progress_state=self.params.get("progress_state"),
                progress_label=str(self.params.get("progress_label") or "Medchem plot"),
            )
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
