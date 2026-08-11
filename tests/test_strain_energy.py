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

"""Strain energy of conformers relative to a reference."""

from __future__ import annotations

from rdkit import Chem

from molmanager.workers import (
    ConformerGenParams,
    StrainEnergyParams,
    run_conformer_generation,
    run_strain_energy,
)


def test_run_strain_energy_relative_to_reference():
    m = Chem.MolFromSmiles("CCO")
    out, meta = run_conformer_generation(
        m,
        ConformerGenParams(
            num_confs=5,
            energy_window_kcal=100.0,
            force_field="MMFF",
            random_seed=21,
            prune_rms_threshold=-1.0,
            max_iterations=80,
        ),
    )
    assert out is not None and meta.get("ok") is True
    assert out.GetNumConformers() >= 2

    row, smeta = run_strain_energy(out, StrainEnergyParams(reference_conformer_index=0))
    assert row is not None and smeta.get("ok") is True
    assert smeta.get("ff") in ("MMFF", "UFF")
    strains = [float(x) for x in row["Strain_energies"].split(";")]
    assert len(strains) == out.GetNumConformers()
    assert abs(strains[0]) < 1e-6
    assert float(row["Strain_max"]) == max(strains)
    assert float(row["E_ref"]) == float(smeta["e_ref_kcal"])
    assert len(smeta.get("energies") or []) == len(strains)
    assert len(smeta.get("strains") or []) == len(strains)
    assert abs(float(smeta["energies"][0]) - float(smeta["e_ref_kcal"])) < 1e-6

    row2, smeta2 = run_strain_energy(out, StrainEnergyParams(reference_conformer_index=1))
    assert row2 is not None and smeta2.get("ok") is True
    strains2 = [float(x) for x in row2["Strain_energies"].split(";")]
    assert abs(strains2[1]) < 1e-6


def test_run_strain_energy_clamps_reference_index():
    m = Chem.MolFromSmiles("CCO")
    out, meta = run_conformer_generation(
        m,
        ConformerGenParams(
            num_confs=3,
            energy_window_kcal=100.0,
            force_field="UFF",
            random_seed=7,
            prune_rms_threshold=-1.0,
            max_iterations=60,
        ),
    )
    assert out is not None and meta.get("ok") is True
    row, smeta = run_strain_energy(
        out, StrainEnergyParams(reference_conformer_index=999, force_field="UFF")
    )
    assert row is not None and smeta.get("ok") is True
    assert smeta.get("ref_clamped") is True
    strains = [float(x) for x in row["Strain_energies"].split(";")]
    assert abs(strains[-1]) < 1e-6
