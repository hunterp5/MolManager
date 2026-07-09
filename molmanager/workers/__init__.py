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


from .bulk_similarity import BulkSimilarityResult, BulkSimilarityWorker
from .cluster_worker import ClusterExploreWorker, ClusterWorker
from .export_worker import ExportWorker
from ..rdkit_fingerprints import SIMILARITY_FP_TYPE_LABELS, fingerprint_bitvect_for_ui_choice
from .diverse_subset_worker import (
    DiverseSubsetWorker,
    build_diverse_subset_pool,
    materialize_pool_fingerprints,
    maxmin_diverse_pick_bulk,
    maxmin_diverse_pick_indices,
    maxmin_diverse_pick_lazy,
    run_diverse_subset_pick,
)
from .fingerprint_similarity import (
    FPSimilarityWorker,
    SIMILARITY_METRIC_LABELS,
    pairwise_fingerprint_similarity,
)
from .load_render import (
    AddExplicitHydrogensWorker,
    NeutralizeWorker,
    Render2DBatchHeldJob,
    Render2DBatchProcessWorker,
    RenderWorker,
    UniversalLoadWorker,
    WashWorker,
)
from .pka_predictor import PKaPredictorSignals, PKaPredictorWorker
from .permeability_worker import PermeabilityPredictorSignals, PermeabilityPredictorWorker
from .protomer_generator import ProtomerGeneratorSignals, ProtomerGeneratorWorker
from .protonate_worker import ProtonateSignals, ProtonateWorker
from .pdbqt_generator import PdbqtGenSignals, PdbqtGenRequest, PdbqtGeneratorWorker
from .fragment_decomposition import FragmentDecompositionWorker
from .fragment_recomposition import FragmentRecompositionWorker
from .rgroup_decomposition import RGroupDecompositionWorker
from .signals import (
    BulkSimilaritySignals,
    DiverseSubsetSignals,
    FPSimilaritySignals,
    SqliteRebuildSignals,
    SubstructureFilterSignals,
    WorkerSignals,
)
from .sqlite_rebuild import SqliteRebuildWorker
from .substructure_filter import SubstructureFilterWorker

__all__ = [
    "CalcWorker",
    "BulkSimilarityResult",
    "BulkSimilaritySignals",
    "BulkSimilarityWorker",
    "ClusterExploreWorker",
    "ClusterWorker",
    "ConformerGenParams",
    "ConformerGenerationWorker",
    "SuperposeConformersWorker",
    "SuperposeParams",
    "CustomCalcWorker",
    "DiverseSubsetSignals",
    "DiverseSubsetWorker",
    "build_diverse_subset_pool",
    "ExportWorker",
    "FragmentDecompositionWorker",
    "FragmentRecompositionWorker",
    "FPSimilaritySignals",
    "FPSimilarityWorker",
    "SIMILARITY_FP_TYPE_LABELS",
    "SIMILARITY_METRIC_LABELS",
    "PKaPredictorSignals",
    "PKaPredictorWorker",
    "PermeabilityPredictorSignals",
    "PermeabilityPredictorWorker",
    "ProtomerGeneratorSignals",
    "ProtomerGeneratorWorker",
    "Render2DBatchHeldJob",
    "Render2DBatchProcessWorker",
    "RenderWorker",
    "RGroupDecompositionWorker",
    "SqliteRebuildSignals",
    "SqliteRebuildWorker",
    "SubstructureFilterSignals",
    "SubstructureFilterWorker",
    "AddExplicitHydrogensWorker",
    "NeutralizeWorker",
    "UniversalLoadWorker",
    "WashWorker",
    "WorkerSignals",
    "ProtonateSignals",
    "ProtonateWorker",
    "PdbqtGenSignals",
    "PdbqtGenRequest",
    "PdbqtGeneratorWorker",
    "describe_custom_calc_error",
    "fingerprint_bitvect_for_ui_choice",
    "materialize_pool_fingerprints",
    "maxmin_diverse_pick_bulk",
    "maxmin_diverse_pick_indices",
    "maxmin_diverse_pick_lazy",
    "run_diverse_subset_pick",
    "format_confs_table_cell",
    "pack_confs_cell",
    "run_conformer_generation",
    "run_superpose_conformers",
]
