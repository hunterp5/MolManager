"""
Chemical sketcher UI split across ``constants``, ``bonds``, ``wildcards``, ``chem``, ``sketch_graph``, ``sketch_rdkit``, ``widget``, and ``dialog``.

Public imports match the former ``MolManager.ui.sketcher`` module path.

**Stereochemistry:** single-bond wedge/hash encode tetrahedral configuration (narrow end = stereocenter).
Alkene **E/Z** is inferred from 2D geometry in ``alkene_stereo``. Tautomers, atropisomers, and arbitrary
diastereomer sets are not auto-enumerated; see ``docs/STEREO_AND_ISOMERISM.md``.

**Bonds & valence:** internal bond ``order`` is 1–3 (single/double/triple); valence warnings sum incident
orders vs element/charge caps. Aromatic RDKit bonds load as order 1; see ``docs/VALENCE_BONDS_AND_AROMATICITY.md``.
"""

from .constants import (
    CLIPBOARD_PREFIX,
    DEFAULT_WILDCARD_ELEMENTS,
    SKETCH_ELEMENT_SYMBOLS,
    SKETCH_RING_TEMPLATES,
    TOOLBAR_ELEMENT_SYMBOLS,
    WILDCARD_ELEMENT,
    WILDCARD_ELEMENT_CHOICES,
)
from .dialog import SketcherDialog
from .widget import SketchWidget

__all__ = [
    "CLIPBOARD_PREFIX",
    "DEFAULT_WILDCARD_ELEMENTS",
    "SKETCH_ELEMENT_SYMBOLS",
    "SKETCH_RING_TEMPLATES",
    "SketcherDialog",
    "SketchWidget",
    "TOOLBAR_ELEMENT_SYMBOLS",
    "WILDCARD_ELEMENT",
    "WILDCARD_ELEMENT_CHOICES",
]
