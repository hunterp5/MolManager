"""Tests for BOILED-Egg / golden-triangle medicinal chemistry plots."""

from __future__ import annotations

from rdkit import Chem

from molmanager.medchem_space import (
    MedChemRowSnapshot,
    build_medchem_space_dataset,
    build_medchem_space_from_snapshots,
    classify_boiled_egg,
    in_golden_triangle,
    oids_in_egg_gia,
    oids_in_egg_yolk,
    oids_in_golden_triangle_region,
    required_descriptor_columns_ok,
    resolve_descriptor_column,
    snapshot_scope_row_indices,
    snapshot_table_values_complete,
    subsample_dataset,
)
from molmanager.ui.medchem_space_plot import build_boiled_egg_figure, build_golden_triangle_figure


def test_snapshot_scope_row_indices():
    assert snapshot_scope_row_indices(10) is None
    assert snapshot_scope_row_indices(10, visible_row_indices=[2, 4]) == [2, 4]
    assert snapshot_scope_row_indices(10, only_selected_rows=[1, 2, 8]) == [1, 2, 8]
    assert snapshot_scope_row_indices(
        10, only_selected_rows=[1, 2, 8], visible_row_indices=[2, 4, 8]
    ) == [2, 8]


def test_required_descriptor_columns_ok():
    assert required_descriptor_columns_ok(
        "boiled_egg", tpsa_col="TPSA", logp_col="LogP", mw_col="MW", wlogp_col="LogP"
    )
    assert not required_descriptor_columns_ok(
        "boiled_egg", tpsa_col="TPSA", logp_col="LogP", mw_col=None, wlogp_col="LogP"
    )
    assert required_descriptor_columns_ok("golden_triangle", tpsa_col=None, logp_col="LogP", mw_col="MW")


def test_golden_triangle_uses_table_without_rdkit_when_populated():
    snap = MedChemRowSnapshot(
        oid=1,
        label="a",
        structure_text="CCO",
        tpsa_text="",
        wlogp_text="",
        mw_text="300.12",
        logp_text="2.50",
    )
    assert snapshot_table_values_complete("golden_triangle", tpsa=None, wlogp=None, mw=300.12, logp=2.5)
    ds, updates = build_medchem_space_from_snapshots(
        [snap],
        plot_kind="golden_triangle",
        tpsa_col="TPSA",
        logp_col="LogP",
        mw_col="MolWt",
        wlogp_col=None,
        use_table_columns_only=True,
    )
    assert len(ds.points) == 1
    assert ds.golden_triangle_count == 1
    assert updates == []


def test_build_from_snapshots_fills_from_structure_when_cells_empty():
    snap = MedChemRowSnapshot(
        oid=1,
        label="ethanol",
        structure_text="CCO",
        tpsa_text="",
        wlogp_text="",
        mw_text="",
        logp_text="",
    )
    ds, updates = build_medchem_space_from_snapshots(
        [snap],
        plot_kind="boiled_egg",
        tpsa_col="TPSA",
        logp_col="LogP",
        mw_col="MW",
        wlogp_col="LogP",
        use_table_columns_only=True,
        oid_smiles={1: "CCO"},
    )
    assert len(ds.points) == 1
    assert len(updates) == 1
    assert "TPSA" in updates[0][1]


def test_resolve_descriptor_column_aliases():
    headers = ["Compound", "MolWt", "cLogP", "TPSA"]
    assert resolve_descriptor_column(headers, ("mw",)) == "MolWt"
    assert resolve_descriptor_column(headers, ("logp",)) == "cLogP"
    assert resolve_descriptor_column(headers, ("tpsa",)) == "TPSA"


def test_build_dataset_and_figures_from_ethanol():
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    ds = build_medchem_space_dataset(mol_rows=[(1, mol)], id_labels={1: "ethanol"})
    assert len(ds.points) == 1
    p = ds.points[0]
    assert p.tpsa > 0
    assert isinstance(classify_boiled_egg(p.tpsa, p.wlogp), tuple)
    assert isinstance(in_golden_triangle(p.logp, p.mw), bool)
    egg = build_boiled_egg_figure(ds)
    assert len(egg.data) == 1
    assert len(egg.layout.shapes) == 3
    assert egg.layout.yaxis.title.text == "LogP"
    assert "LogP" in ds.points[0].hover
    assert "WLOGP" not in ds.points[0].hover
    tri = build_golden_triangle_figure(ds)
    assert len(tri.data) == 1
    assert len(tri.layout.shapes) == 1
    assert tri.layout.xaxis.title.text == "LogP"
    assert tri.layout.yaxis.title.text == "Molecular weight (Da)"
    assert isinstance(oids_in_egg_gia(ds), list)
    assert isinstance(oids_in_egg_yolk(ds), list)
    assert isinstance(oids_in_golden_triangle_region(ds), list)
    assert "GIA" in ds.summary_text(plot_kind="boiled_egg")
    assert "golden triangle" in ds.summary_text(plot_kind="golden_triangle").lower()
    big = build_medchem_space_dataset(mol_rows=[(i, mol) for i in range(1, 21)])
    sub = subsample_dataset(big, 5, random_state=0)
    assert len(sub.points) == 5
    assert "subsample" in sub.subsample_note.lower()
    snap = MedChemRowSnapshot(
        oid=1,
        label="a",
        structure_text="",
        tpsa_text="46",
        wlogp_text="-0.0",
        mw_text="46",
        logp_text="-0.0",
    )
    ds2, _updates2 = build_medchem_space_from_snapshots(
        [snap],
        plot_kind="boiled_egg",
        tpsa_col="TPSA",
        logp_col="LogP",
        mw_col="MW",
        wlogp_col="LogP",
        use_table_columns_only=True,
    )
    assert len(ds2.points) == 1
    fig_c = build_boiled_egg_figure(ds, color_values=[p.mw, p.mw + 10], color_label="MW")
    assert fig_c.data[0].marker.showscale is True
    assert len(fig_c.data[0].marker.color) == 2
