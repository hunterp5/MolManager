"""Bond records used throughout the sketch canvas (packed tuples)."""

from typing import Any

# stereo field (meaningful when order == 1):
BOND_STEREO_PLAIN = 0
BOND_STEREO_WEDGE = 1
BOND_STEREO_HASH = 2
BOND_STEREO_WAVY = 3  # unspecified / either configuration
BOND_STEREO_DATIVE = 4  # coordination arrow (maps to RDKit BondType.DATIVE)

BOND_STEREO_VALUES = (
    BOND_STEREO_PLAIN,
    BOND_STEREO_WEDGE,
    BOND_STEREO_HASH,
    BOND_STEREO_WAVY,
    BOND_STEREO_DATIVE,
)


def _bond_record_ok(b: Any) -> bool:
    return isinstance(b, (tuple, list)) and len(b) >= 3


def _bond_unpack(b: Any) -> tuple[int, int, int, int]:
    if not _bond_record_ok(b):
        raise TypeError(f"bond must be a sequence of at least 3 ints, got {type(b).__name__!r}")
    a, bo, o = int(b[0]), int(b[1]), int(b[2])
    s = int(b[3]) if len(b) > 3 else 0
    return a, bo, o, s


def _bond_make(a: int, b: int, o: int, s: int = 0) -> tuple[int, int, int, int]:
    return (a, b, o, s)


def _bond_same_undirected(b1: tuple, b2: tuple) -> bool:
    a1, b1, o1, s1 = _bond_unpack(b1)
    a2, b2, o2, s2 = _bond_unpack(b2)
    if o1 != o2 or s1 != s2:
        return False
    return (a1 == a2 and b1 == b2) or (a1 == b2 and b1 == a2)


def reorient_wedged_bonds_tip_away_from_multiples(
    bonds: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """
    Wedge/hash narrow end is stored as the **first** atom in each bond tuple (see painter).

    Rules (IUPAC ST + sketcher convention):
    - Tip must not originate at an sp² / multiply-bonded atom (swap or clear).
    - Otherwise tip prefers the **more substituted** endpoint (more heavy neighbors).
    """
    mult: set[int] = set()
    deg: dict[int, int] = {}
    for b in bonds:
        a, bo, o, _s = _bond_unpack(b)
        deg[a] = deg.get(a, 0) + 1
        deg[bo] = deg.get(bo, 0) + 1
        if o >= 2:
            mult.add(a)
            mult.add(bo)
    out: list[tuple[int, int, int, int]] = []
    for b in bonds:
        a, bo, o, s = _bond_unpack(b)
        if o == 1 and s in (BOND_STEREO_WEDGE, BOND_STEREO_HASH):
            da = a in mult
            db = bo in mult
            if da and not db:
                a, bo = bo, a
            elif da and db:
                s = BOND_STEREO_PLAIN
            elif not da and not db:
                # Thin tip at the more substituted origin.
                if deg.get(bo, 0) > deg.get(a, 0):
                    a, bo = bo, a
        out.append(_bond_make(a, bo, o, s))
    return out


def clear_stereo_bonds_between_centers(
    bonds: list[tuple[int, int, int, int]],
    chiral_center_ids: set[int],
) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int]]]:
    """
    Drop wedge/hash on bonds that connect two stereocenters (IUPAC ST-0.5).

    Returns ``(new_bonds, cleared_pairs)`` where cleared_pairs are ``(a, b)`` atom ids.
    """
    if not chiral_center_ids:
        return list(bonds), []
    out: list[tuple[int, int, int, int]] = []
    cleared: list[tuple[int, int]] = []
    for b in bonds:
        a, bo, o, s = _bond_unpack(b)
        if (
            o == 1
            and s in (BOND_STEREO_WEDGE, BOND_STEREO_HASH)
            and a in chiral_center_ids
            and bo in chiral_center_ids
        ):
            out.append(_bond_make(a, bo, o, BOND_STEREO_PLAIN))
            cleared.append((a, bo))
        else:
            out.append(_bond_make(a, bo, o, s))
    return out, cleared


def sanitize_sketch_stereo_bonds(
    bonds: list[tuple[int, int, int, int]],
    *,
    chiral_center_ids: set[int] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Reorient tips away from multiples, then clear illegal stereo-between-centers."""
    out = reorient_wedged_bonds_tip_away_from_multiples(bonds)
    if chiral_center_ids:
        out, _ = clear_stereo_bonds_between_centers(out, chiral_center_ids)
    return out