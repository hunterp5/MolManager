"""Background workers (load, render, chemistry tools, export)."""

from __future__ import annotations

import importlib
from typing import Any

# ``chemistry_tools`` pulls in ``medchem_descriptors`` and must not load during ``workers``
# package init (e.g. ``pkasolver_descriptor_support`` imports ``workers.pka_predictor``).
_CHEMISTRY_TOOLS_EXPORTS = frozenset(
    {
        "CalcWorker",
        "ConformerGenParams",
        "ConformerGenerationWorker",
        "CustomCalcWorker",
        "describe_custom_calc_error",
        "format_confs_table_cell",
        "pack_confs_cell",
        "run_conformer_generation",
        "SuperposeConformersWorker",
        "SuperposeParams",
        "run_superpose_conformers",
    }
)


def __getattr__(name: str) -> Any:
    if name in _CHEMISTRY_TOOLS_EXPORTS:
        ct = importlib.import_module(".chemistry_tools", __package__)
        return getattr(ct, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .cluster_worker import ClusterExploreWorker, ClusterWorker
from .export_worker import ExportWorker
from .fingerprint_similarity import FPSimilarityWorker, fingerprint_bitvect_for_ui_choice
from .load_render import Render2DBatchProcessWorker, RenderWorker, UniversalLoadWorker, WashWorker
from .pka_predictor import PKaPredictorSignals, PKaPredictorWorker
from .protomer_generator import ProtomerGeneratorSignals, ProtomerGeneratorWorker
from .rgroup_decomposition import RGroupDecompositionWorker
from .signals import FPSimilaritySignals, SubstructureFilterSignals, WorkerSignals
from .substructure_filter import SubstructureFilterWorker

__all__ = [
    "CalcWorker",
    "ClusterExploreWorker",
    "ClusterWorker",
    "ConformerGenParams",
    "ConformerGenerationWorker",
    "SuperposeConformersWorker",
    "SuperposeParams",
    "CustomCalcWorker",
    "ExportWorker",
    "FPSimilaritySignals",
    "FPSimilarityWorker",
    "PKaPredictorSignals",
    "PKaPredictorWorker",
    "ProtomerGeneratorSignals",
    "ProtomerGeneratorWorker",
    "Render2DBatchProcessWorker",
    "RenderWorker",
    "RGroupDecompositionWorker",
    "SubstructureFilterSignals",
    "SubstructureFilterWorker",
    "UniversalLoadWorker",
    "WashWorker",
    "WorkerSignals",
    "describe_custom_calc_error",
    "fingerprint_bitvect_for_ui_choice",
    "format_confs_table_cell",
    "pack_confs_cell",
    "run_conformer_generation",
    "run_superpose_conformers",
]
