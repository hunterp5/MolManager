# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Ligand hydrogen preparation before Meeko PDBQT conversion."""

from __future__ import annotations

import pytest

pytest.importorskip("rdkit")

from rdkit import Chem
from rdkit.Chem import AllChem

from molmanager.workers.pdbqt_generator import prepare_ligand_with_hydrogens


def test_prepare_ligand_with_hydrogens_adds_explicit_h_to_smiles():
    mol = Chem.MolFromSmiles("c1ccccc1")
    assert mol is not None
    assert mol.GetNumAtoms() == 6

    prepared = prepare_ligand_with_hydrogens(mol)
    assert prepared is not None
    assert prepared.GetNumAtoms() > mol.GetNumAtoms()
    assert prepared.GetNumConformers() >= 1
    assert any(atom.GetAtomicNum() == 1 for atom in prepared.GetAtoms())


def test_prepare_ligand_with_hydrogens_keeps_existing_3d_coords():
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    mol = Chem.AddHs(mol)

    assert AllChem.EmbedMolecule(mol, AllChem.ETKDG()) == 0
    n_heavy = mol.GetNumHeavyAtoms()

    prepared = prepare_ligand_with_hydrogens(mol)
    assert prepared is not None
    assert prepared.GetNumHeavyAtoms() == n_heavy
    assert prepared.GetNumAtoms() > n_heavy
    assert prepared.GetNumConformers() >= 1


def test_meeko_rdkit_compat_adds_mol_has_query():
    from molmanager.workers.pdbqt_generator import _apply_meeko_rdkit_compat

    _apply_meeko_rdkit_compat()
    mol = Chem.MolFromSmiles("c1ccccc1")
    assert mol is not None
    assert hasattr(mol, "HasQuery")
    assert mol.HasQuery() is False


def test_write_ligand_pdbqt_file(tmp_path):
    pytest.importorskip("meeko")
    from molmanager.workers.pdbqt_generator import (
        _write_ligand_pdbqt_file,
        prepare_ligand_with_hydrogens,
    )

    mol = Chem.MolFromSmiles("CCO")
    prepared = prepare_ligand_with_hydrogens(mol)
    assert prepared is not None
    out = tmp_path / "ligand.pdbqt"
    err = _write_ligand_pdbqt_file([prepared], out)
    assert err is None
    assert out.stat().st_size > 0
