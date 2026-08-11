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

"""Tests for sketcher alkene E/Z inference."""

from rdkit import Chem
from rdkit.Chem.rdchem import Conformer

from molmanager.ui.sketcher.alkene_stereo import infer_alkene_ez_for_sketch_mol


def _mol_fccl_trans():
    m2 = Chem.RWMol()
    for sym in ["F", "C", "C", "Cl"]:
        m2.AddAtom(Chem.Atom(sym))
    m2.AddBond(0, 1, Chem.BondType.SINGLE)
    m2.AddBond(1, 2, Chem.BondType.DOUBLE)
    m2.AddBond(2, 3, Chem.BondType.SINGLE)
    m2 = m2.GetMol()
    conf = Conformer(4)
    pts = [(-1.5, 0.5), (-0.5, 0), (0.5, 0), (1.5, -0.5)]
    for i, (x, y) in enumerate(pts):
        conf.SetAtomPosition(i, (x, y, 0))
    m2.RemoveAllConformers()
    m2.AddConformer(conf)
    Chem.SanitizeMol(m2)
    return m2


def _mol_fccl_cis():
    m = _mol_fccl_trans()
    conf = m.GetConformer(0)
    conf.SetAtomPosition(3, (1.5, 0.5, 0))
    return m


def test_infer_ez_trans_fccl():
    m = _mol_fccl_trans()
    assert infer_alkene_ez_for_sketch_mol(m) == {(1, 2): "E"}


def test_infer_ez_cis_fccl():
    m = _mol_fccl_cis()
    assert infer_alkene_ez_for_sketch_mol(m) == {(1, 2): "Z"}
