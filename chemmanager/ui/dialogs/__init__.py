"""Modal and modeless tool dialogs (split into submodules for maintainability)."""

from __future__ import annotations

from ..plot import PlotDialog
from ..sketcher import SketchWidget, SketcherDialog
from .calculator import CalculatorDialog
from .cluster import ClusterDialog
from .fp_similarity import FPSimilarityDialog
from .mol_tools import (
    DisconnectFragmentsDialog,
    GenerateConformationsDialog,
    RGroupDecompDialogParams,
    RGroupDecompositionDialog,
    SuperposeConformersDialog,
)
from .pka import PKaPredictorDialog
from .properties import PropertyDialog
from .protomer import ProtomerGeneratorDialog
from .render_2d import Render2DStructureDialog
from .scope import selection_scope_checked

__all__ = [
    "CalculatorDialog",
    "ClusterDialog",
    "DisconnectFragmentsDialog",
    "FPSimilarityDialog",
    "GenerateConformationsDialog",
    "PKaPredictorDialog",
    "PlotDialog",
    "PropertyDialog",
    "ProtomerGeneratorDialog",
    "RGroupDecompDialogParams",
    "RGroupDecompositionDialog",
    "Render2DStructureDialog",
    "SketchWidget",
    "SketcherDialog",
    "SuperposeConformersDialog",
    "selection_scope_checked",
]
