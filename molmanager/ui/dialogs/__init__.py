"""Modal and modeless tool dialogs (split into submodules for maintainability)."""

from __future__ import annotations

from ..plot import PlotDialog
from ..sketcher import SketchWidget, SketcherDialog
from .calculator import CalculatorDialog
from .bulk_similarity import BulkSimilarityDialog
from .cluster import ClusterDialog
from .diverse_subset import DiverseSubsetDialog
from .fp_similarity import FPSimilarityDialog
from .mol_tools import (
    CoreBasedDecompDialogParams,
    CoreBasedDecompositionDialog,
    DisconnectFragmentsDialog,
    FastPrepareDialog,
    AddExplicitHydrogensDialog,
    NeutralizeDialog,
    FragmentDecompDialogParams,
    FragmentDecompositionDialog,
    FragmentRecompDialogParams,
    FragmentRecompositionDialog,
    GenerateConformationsDialog,
    GenerateSingleConformationDialog,
    SuperposeConformersDialog,
)
from .permeability import PermeabilityPredictorDialog
from .pka import PKaPredictorDialog
from .properties import PropertyDialog
from .protomer import ProtomerGeneratorDialog
from .pdbqt_generator import PdbqtGeneratorDialog
from .protonate import ProtonateDialog
from .qsar import QSARDialog
from .render_2d import Render2DStructureDialog
from .scope import selection_scope_checked

__all__ = [
    "BulkSimilarityDialog",
    "CalculatorDialog",
    "ClusterDialog",
    "CoreBasedDecompDialogParams",
    "CoreBasedDecompositionDialog",
    "DisconnectFragmentsDialog",
    "FastPrepareDialog",
    "AddExplicitHydrogensDialog",
    "NeutralizeDialog",
    "FragmentDecompDialogParams",
    "FragmentDecompositionDialog",
    "FragmentRecompDialogParams",
    "FragmentRecompositionDialog",
    "DiverseSubsetDialog",
    "FPSimilarityDialog",
    "GenerateConformationsDialog",
    "GenerateSingleConformationDialog",
    "PermeabilityPredictorDialog",
    "PKaPredictorDialog",
    "PlotDialog",
    "PropertyDialog",
    "ProtomerGeneratorDialog",
    "PdbqtGeneratorDialog",
    "ProtonateDialog",
    "QSARDialog",
    "Render2DStructureDialog",
    "SketchWidget",
    "SketcherDialog",
    "SuperposeConformersDialog",
    "selection_scope_checked",
]
