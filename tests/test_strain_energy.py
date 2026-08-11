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
