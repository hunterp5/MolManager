from __future__ import annotations

from PyQt5.QtCore import QPoint
from rdkit import Chem
from rdkit.Chem.rdchem import Conformer
from rdkit.Chem.Draw import rdMolDraw2D

from molmanager.ui.sketcher.acs_style import acs_sketch_style
from molmanager.ui.sketcher.sketch_rdkit_paint import (
    effective_model_per_drawer_scale,
    fit_rdkit_draw_to_sketch_map,
    render_sketch_mol_to_pixmap,
    sketch_node_bounds,
)


def _mol_with_sketch_coords(nodes: list[tuple[int, int]]) -> tuple[Chem.Mol, dict[int, int], list[dict]]:
    scale = 40.0
    m = Chem.MolFromSmiles("CCC")
    conf = Conformer(3)
    sketch_nodes = []
    idmap: dict[int, int] = {}
    for i, (x, y) in enumerate(nodes):
        conf.SetAtomPosition(i, (x / scale, -y / scale, 0.0))
        sk_id = 100 + i
        idmap[sk_id] = i
        sketch_nodes.append({"id": sk_id, "pos": QPoint(x, y), "element": "C"})
    m.AddConformer(conf, assignId=True)
    return m, idmap, sketch_nodes


def test_sketch_node_bounds_empty() -> None:
    assert sketch_node_bounds([], pad_px=20.0) is None


def test_fit_rdkit_draw_to_sketch_map_linear(qapp) -> None:  # noqa: ARG001
    m, idmap, nodes = _mol_with_sketch_coords([(100, 100), (160, 200), (220, 100)])
    bounds = sketch_node_bounds(nodes, pad_px=40.0)
    assert bounds is not None
    min_x, min_y, max_x, max_y = bounds
    w = max(80, max_x - min_x)
    h = max(80, max_y - min_y)
    drawer = rdMolDraw2D.MolDraw2DCairo(w, h)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, m)
    fit = fit_rdkit_draw_to_sketch_map(drawer, idmap, nodes)
    assert fit is not None
    sx, ox, sy, oy = fit
    for node in nodes:
        rd_idx = idmap[node["id"]]
        dc = drawer.GetDrawCoords(rd_idx)
        pred_x = sx * dc.x + ox
        pred_y = sy * dc.y + oy
        assert abs(pred_x - node["pos"].x()) < 1.5
        assert abs(pred_y - node["pos"].y()) < 1.5


def test_render_sketch_mol_to_pixmap(qapp) -> None:  # noqa: ARG001
    m, idmap, nodes = _mol_with_sketch_coords([(120, 140), (180, 140), (240, 140)])
    rendered = render_sketch_mol_to_pixmap(
        m, idmap, nodes, pad_px=40.0, bond_scale_px=60.0, render_scale=3
    )
    assert rendered is not None
    pm, transform = rendered
    assert not pm.isNull()
    assert pm.width() >= 240
    assert not transform.isIdentity()


def _chain_mol_and_nodes(count: int) -> tuple[Chem.Mol, dict[int, int], list[dict]]:
    scale = 40.0
    m = Chem.MolFromSmiles("C" * count)
    conf = Conformer(count)
    sketch_nodes = []
    idmap: dict[int, int] = {}
    for i in range(count):
        x = 100 + i * 60
        y = 200
        conf.SetAtomPosition(i, (x / scale, -y / scale, 0.0))
        sk_id = 100 + i
        idmap[sk_id] = i
        sketch_nodes.append({"id": sk_id, "pos": QPoint(x, y), "element": "C"})
    m.AddConformer(conf, assignId=True)
    return m, idmap, sketch_nodes


def test_effective_model_per_drawer_scale_prefers_span_axis() -> None:
    nodes = [
        {"id": 1, "pos": QPoint(0, 0)},
        {"id": 2, "pos": QPoint(120, 0)},
    ]
    assert effective_model_per_drawer_scale(0.25, 1.0, nodes) == 0.25


def test_render_sketch_bond_width_stable_as_atoms_removed(qapp) -> None:  # noqa: ARG001
    """Deleting peripheral atoms must not shrink bond stroke as the render bbox tightens."""
    target = acs_sketch_style(60.0).bond_width_px
    strokes: list[float] = []
    for count in range(6, 2, -1):
        mol, idmap, nodes = _chain_mol_and_nodes(count)
        bounds = sketch_node_bounds(nodes, pad_px=40.0)
        assert bounds is not None
        width = max(80, bounds[2] - bounds[0])
        height = max(80, bounds[3] - bounds[1])
        rw, rh = width * 3, height * 3
        probe = rdMolDraw2D.MolDraw2DCairo(rw, rh)
        probe.drawOptions().padding = 0.02
        probe.drawOptions().bondLineWidth = 1.0
        rdMolDraw2D.PrepareAndDrawMolecule(probe, mol)
        fit = fit_rdkit_draw_to_sketch_map(probe, idmap, nodes)
        assert fit is not None
        sx, _, sy, _ = fit
        model_scale = effective_model_per_drawer_scale(sx, sy, nodes)
        line_w = max(1.0, target / model_scale)
        drawer = rdMolDraw2D.MolDraw2DCairo(rw, rh)
        drawer.drawOptions().padding = 0.02
        drawer.drawOptions().bondLineWidth = line_w
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
        fit2 = fit_rdkit_draw_to_sketch_map(drawer, idmap, nodes)
        assert fit2 is not None
        sx2, _, sy2, _ = fit2
        model_scale2 = effective_model_per_drawer_scale(sx2, sy2, nodes)
        strokes.append(line_w * model_scale2)
        rendered = render_sketch_mol_to_pixmap(
            mol, idmap, nodes, pad_px=40.0, bond_scale_px=60.0, render_scale=3
        )
        assert rendered is not None
    for stroke in strokes:
        assert abs(stroke - target) < 0.75
