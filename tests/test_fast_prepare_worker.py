"""Fast Prepare worker chemistry."""

from __future__ import annotations

from rdkit import Chem

from molmanager.medchem_descriptors import mol_net_formal_charge
from molmanager.utils import mol_to_canonical_smiles
from molmanager.workers.fast_prepare import fast_prepare_one_row


def test_fast_prepare_one_row_disconnects_and_neutralizes_salt() -> None:
    raw = "CCCCCCCCCCCC.Cc1ccc(S(=O)(=O)[O-])cc1"
    mol = Chem.MolFromSmiles(raw)
    assert mol is not None
    oid, blob, fragments, ok = fast_prepare_one_row(1, mol.ToBinary(), raw)
    assert ok
    assert fragments
    out = Chem.Mol(blob)
    assert mol_net_formal_charge(out) == 0
    assert out.GetNumHeavyAtoms() == 12
    assert "Cc1ccc(S(=O)(=O)[O-])cc1" in fragments or mol_to_canonical_smiles(
        Chem.MolFromSmiles("Cc1ccc(S(=O)(=O)[O-])cc1")
    ) in fragments


def test_fast_prepare_one_row_parses_from_text_when_no_mol_bytes() -> None:
    raw = "CCO.CC"
    oid, blob, fragments, ok = fast_prepare_one_row(2, b"", raw)
    assert ok
    out = Chem.Mol(blob)
    assert mol_to_canonical_smiles(out) == mol_to_canonical_smiles(Chem.MolFromSmiles("CCO"))
    assert fragments


def test_fast_prepare_one_row_skips_invalid() -> None:
    oid, blob, fragments, ok = fast_prepare_one_row(3, b"", "not_a_structure")
    assert not ok
    assert not blob
