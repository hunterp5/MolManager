"""RGB colors matching RDKit MolDraw2D ``assignDefaultPalette`` (MolDraw2DHelpers.h)."""

from __future__ import annotations

from rdkit import Chem

# Sparse palette from RDKit; any other Z uses ``-1`` entry (black).
_Z_TO_RGB: dict[int, tuple[int, int, int]] = {
    -1: (0, 0, 0),
    0: (26, 26, 26),
    1: (0, 0, 0),
    6: (0, 0, 0),
    7: (0, 0, 255),
    8: (255, 0, 0),
    9: (51, 204, 204),
    15: (255, 128, 0),
    16: (204, 204, 0),
    17: (0, 204, 0),
    35: (128, 77, 26),
    53: (161, 31, 239),
    201: (173, 217, 230),
}


def rdkit_default_element_rgb(symbol: str) -> tuple[int, int, int]:
    """
    Return 8-bit RGB for ``symbol`` using the same atomic-number keys as RDKit's default MolDraw2D palette.

    Deuterium/tritium symbols ``D`` / ``T`` are treated like hydrogen (Z=1). Unknown symbols fall back
    to the palette's ``-1`` color (black), matching RDKit when no entry exists for that Z.
    """
    sym = (symbol or "").strip()
    if not sym:
        return _Z_TO_RGB[-1]
    if sym in ("D", "T"):
        sym = "H"
    try:
        an = int(Chem.GetPeriodicTable().GetAtomicNumber(sym))
    except Exception:
        return _Z_TO_RGB[-1]
    if an in _Z_TO_RGB:
        return _Z_TO_RGB[an]
    return _Z_TO_RGB[-1]
