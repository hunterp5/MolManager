"""Sketcher SMILES/SMARTS export (wildcards, isotopes, charges) and H toggle helpers."""

import math

from PyQt5.QtCore import QPoint
from rdkit import Chem

from molmanager.smarts_patterns import mol_from_smarts
from molmanager.ui.sketcher.bonds import _bond_make
from molmanager.ui.sketcher.chem import _rdkit_atom_from_sketch_node, _sketch_element_from_rdkit_atom
from molmanager.ui.sketcher.constants import DEFAULT_WILDCARD_ELEMENTS, WILDCARD_ELEMENT
from molmanager.ui.sketcher.widget import SketchWidget
from molmanager.ui.sketcher.wildcards import _wildcard_query_smarts


def test_wildcard_smarts_uses_atomic_numbers_and_charge():
    assert _wildcard_query_smarts(["N", "O"], formal_charge=1) == "[#7,#8;+]"
    assert _wildcard_query_smarts(["C"], formal_charge=-1) == "[#6;-]"
    # Aliphatic letters must not be used — they miss aromatic atoms in search.
    assert "C" not in _wildcard_query_smarts(["C", "N"])
    a = _rdkit_atom_from_sketch_node(
        {"element": WILDCARD_ELEMENT, "wildcard_els": ["N", "O"]},
        formal_charge=1,
    )
    rw = Chem.RWMol()
    rw.AddAtom(a)
    smt = Chem.MolToSmarts(rw.GetMol())
    assert "+" in smt
    assert "#7" in smt and "#8" in smt


def test_deuterium_and_tritium_atoms():
    d = _rdkit_atom_from_sketch_node({"element": "D"})
    assert d.GetAtomicNum() == 1 and d.GetIsotope() == 2
    t = _rdkit_atom_from_sketch_node({"element": "T"})
    assert t.GetAtomicNum() == 1 and t.GetIsotope() == 3
    assert _sketch_element_from_rdkit_atom(d) == "D"
    assert _sketch_element_from_rdkit_atom(t) == "T"


def test_sketch_deuterium_smiles(qapp):  # noqa: ARG001
    w = SketchWidget()
    ids = []
    for i, el in enumerate(("C", "D")):
        nid = w.next_id
        w.next_id += 1
        w.nodes.append({"id": nid, "pos": QPoint(i * 40, 0), "element": el})
        ids.append(nid)
    w.bonds.append(_bond_make(ids[0], ids[1], 1, 0))
    smi = w.to_smiles()
    assert smi
    assert "[2H]" in smi or "D" in smi or smi.endswith("C") is False
    m = Chem.MolFromSmiles(smi)
    assert m is not None


def test_sketch_wildcard_export(qapp):  # noqa: ARG001
    w = SketchWidget()
    nid = w.next_id
    w.next_id += 1
    w.nodes.append(
        {
            "id": nid,
            "pos": QPoint(0, 0),
            "element": WILDCARD_ELEMENT,
            "wildcard_els": list(DEFAULT_WILDCARD_ELEMENTS),
            "charge": 1,
        }
    )
    smt = w.to_smarts()
    assert smt
    assert "+" in smt
    assert "#6" in smt and "#7" in smt and "#8" in smt
    # Copy-SMILES path for wildcards uses SMARTS so element lists survive.
    assert w.to_smiles() == smt
    q = mol_from_smarts(smt)
    assert q is not None
    assert Chem.MolFromSmiles("C[NH3+]").HasSubstructMatch(q)
    assert not Chem.MolFromSmiles("c1ccccc1").HasSubstructMatch(q)

    w2 = SketchWidget()
    nid = w2.next_id
    w2.next_id += 1
    w2.nodes.append(
        {
            "id": nid,
            "pos": QPoint(0, 0),
            "element": WILDCARD_ELEMENT,
            "wildcard_els": list(DEFAULT_WILDCARD_ELEMENTS),
        }
    )
    q2 = mol_from_smarts(w2.to_smarts())
    assert q2 is not None
    assert Chem.MolFromSmiles("c1ccccc1").HasSubstructMatch(q2)
    assert Chem.MolFromSmiles("CCO").HasSubstructMatch(q2)


def test_sketch_wildcard_aromatic_ring_matches_substructure(qapp):  # noqa: ARG001
    """Kekulé ring with a C/N wildcard must match aromatic benzene/pyridine (Search path)."""
    w = SketchWidget()
    ids = []
    for i in range(6):
        ang = math.radians(60 * i - 90)
        nid = w.next_id
        w.next_id += 1
        if i == 0:
            w.nodes.append(
                {
                    "id": nid,
                    "pos": QPoint(int(80 * math.cos(ang)), int(80 * math.sin(ang))),
                    "element": WILDCARD_ELEMENT,
                    "wildcard_els": ["C", "N"],
                }
            )
        else:
            w.nodes.append(
                {
                    "id": nid,
                    "pos": QPoint(int(80 * math.cos(ang)), int(80 * math.sin(ang))),
                    "element": "C",
                }
            )
        ids.append(nid)
    for i in range(6):
        order = 2 if i % 2 == 0 else 1
        w.bonds.append(_bond_make(ids[i], ids[(i + 1) % 6], order, 0))

    smt = w.to_smarts()
    assert smt
    assert "#6" in smt and "#7" in smt
    # Aromatic bond markers after sanitize (not Kekulé =/- only).
    assert ":" in smt
    q = mol_from_smarts(smt)
    assert q is not None
    assert Chem.MolFromSmiles("c1ccccc1").HasSubstructMatch(q)
    assert Chem.MolFromSmiles("c1ccncc1").HasSubstructMatch(q)
    assert Chem.MolFromSmiles("c1ccccc1O").HasSubstructMatch(q)


def test_toggle_explicit_hydrogens(qapp):  # noqa: ARG001
    w = SketchWidget()
    nid = w.next_id
    w.next_id += 1
    w.nodes.append({"id": nid, "pos": QPoint(0, 0), "element": "C"})
    assert not w.sketch_has_explicit_hydrogens()
    ok, _ = w.add_explicit_hydrogens_from_implicit()
    assert ok
    assert w.sketch_has_explicit_hydrogens()
    ok, _ = w.remove_explicit_hydrogens_from_sketch()
    assert ok
    assert not w.sketch_has_explicit_hydrogens()


def test_load_preserves_aromatic_kekule_doubles_with_stereo_h(qapp) -> None:  # noqa: ARG001
    """Sanitize after stereo-H must not leave aromatic rings as all singles."""
    from molmanager.ui.sketcher.bonds import _bond_unpack

    mol = Chem.MolFromSmiles("C[C@H](O)c1ccccc1")
    w = SketchWidget()
    w.resize(600, 400)
    assert w.load_from_rdkit_mol(mol)
    orders = [_bond_unpack(b)[2] for b in w.bonds]
    assert orders.count(2) >= 3, f"expected Kekulé doubles in phenyl, got {orders}"
