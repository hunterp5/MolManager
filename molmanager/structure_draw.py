"""RDKit 2D structure rendering for the compound table (no Qt)."""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

from .display_constants import STRUCTURE_DEPICT_BOND_LINE_WIDTH, STRUCTURE_DEPICT_WIDTH


def structure_cairo_dimensions(target_w: int, target_h: int) -> tuple[int, int]:
    """Return (width, height) for MolDraw2DCairo (same as the display target)."""
    return int(target_w), int(target_h)


def configure_mol_drawer(drawer: rdMolDraw2D.MolDraw2D, target_w: int) -> None:
    """Thinner bonds at table resolution; scale stroke with zoomed (2×) depictions."""
    opts = drawer.drawOptions()
    base_w = max(1.0, float(STRUCTURE_DEPICT_WIDTH))
    ratio = max(1.0, float(target_w) / base_w)
    opts.bondLineWidth = float(STRUCTURE_DEPICT_BOND_LINE_WIDTH) * ratio


def _apply_table_draw_options(drawer: rdMolDraw2D.MolDraw2DCairo, target_w: int) -> None:
    configure_mol_drawer(drawer, target_w)


def render_molecule_png(mol: Chem.Mol, target_w: int, target_h: int) -> bytes:
    """Draw *mol* to PNG bytes at the requested table / zoom size."""
    cw, ch = structure_cairo_dimensions(target_w, target_h)
    drawer = rdMolDraw2D.MolDraw2DCairo(int(cw), int(ch))
    configure_mol_drawer(drawer, int(cw))
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()
