"""Bond records used throughout the sketch canvas (packed tuples)."""

from typing import Any


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

    That atom must be the tetrahedral stereocenter: a wedged single bond may **terminate**
    at an sp² / multiply-bonded atom but must not **originate** there. If RDKit (or bad data)
    puts the tip on an atom incident to a double/triple bond, swap endpoints; if both ends
    are multiply-bonded, drop wedge/hash for that bond.
    """
    mult: set[int] = set()
    for b in bonds:
        a, bo, o, _s = _bond_unpack(b)
        if o >= 2:
            mult.add(a)
            mult.add(bo)
    out: list[tuple[int, int, int, int]] = []
    for b in bonds:
        a, bo, o, s = _bond_unpack(b)
        if o == 1 and s in (1, 2):
            da = a in mult
            db = bo in mult
            if da and not db:
                a, bo = bo, a
            elif da and db:
                s = 0
        out.append(_bond_make(a, bo, o, s))
    return out
