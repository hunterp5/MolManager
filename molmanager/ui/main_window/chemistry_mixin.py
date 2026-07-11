"""Chemistry tools, ingest, rendering, and prediction entry points for the main window."""

from __future__ import annotations

from .conformers_descriptors_mixin import ConformersDescriptorsMixin
from .fragment_tools_mixin import FragmentToolsMixin
from .ingest_render_mixin import IngestRenderMixin
from .plot_tools_mixin import PlotToolsMixin
from .prepare_structures_mixin import PrepareStructuresMixin
from .reaction_tools_mixin import ReactionToolsMixin
from .tools_sql_predict_mixin import ToolsSqlPredictMixin


class ChemistryMixin(
    PlotToolsMixin,
    IngestRenderMixin,
    PrepareStructuresMixin,
    ConformersDescriptorsMixin,
    FragmentToolsMixin,
    ReactionToolsMixin,
    ToolsSqlPredictMixin,
):
    """Composite mixin: plot UI, ingest/render, structure prep, conformers, fragments, SQL/predictions."""
