"""RDKit integration for the sketch widget: mol build, 2D load, SMILES/SMARTS export, and AddHs helpers."""

from __future__ import annotations

import math
from typing import Any

from PyQt5.QtCore import QPoint, QTimer
from PyQt5.QtWidgets import QMessageBox

from rdkit import Chem
from rdkit.Chem import AllChem, rdCIPLabeler, rdDepictor
from rdkit.Chem.rdchem import BondDir, Conformer
from rdkit.Geometry import Point2D

from ...utils import mol_to_canonical_smiles

from .bonds import _bond_make, _bond_unpack
from .chem import _sanitize_mol_for_smiles
from .constants import (
    BOND_DIR_HASH as _BOND_DIR_HASH,
    DEFAULT_WILDCARD_ELEMENTS,
    SKETCH_COORD_SCALE as _SKETCH_COORD_SCALE,
    SKETCH_MEDIAN_BOND_PX,
)
from .wildcards import (
    _is_wildcard_node,
    _normalize_wildcard_elements,
    _wildcard_query_smarts,
)


class SketchWidgetRdkitMixin:
    """Mixed into ``SketchWidget`` after paint/events mixins, before ``QWidget``."""

    @staticmethod
    def _mol_net_formal_charge(mol: Chem.Mol) -> int:
        return sum(mol.GetAtomWithIdx(i).GetFormalCharge() for i in range(mol.GetNumAtoms()))

    def _apply_sketch_coords_and_stereo(self, mol: Chem.Mol, idmap: dict[int, int]) -> None:
        """Embed 2D sketch coordinates and derive tetrahedral stereo from wedge/hash bond dirs; assign CIP R/S."""
        na = mol.GetNumAtoms()
        if na == 0 or not idmap:
            return
        try:
            mol.RemoveAllConformers()
        except Exception:
            pass
        conf = Conformer(na)
        inv = {rd_idx: sk_id for sk_id, rd_idx in idmap.items()}
        sc = _SKETCH_COORD_SCALE
        for idx in range(na):
            sk_id = inv.get(idx)
            if sk_id is None:
                continue
            node = next((x for x in self.nodes if x["id"] == sk_id), None)
            if not node:
                continue
            pos = node["pos"]
            conf.SetAtomPosition(idx, (float(pos.x()) / sc, float(-pos.y()) / sc, 0.0))
        mol.AddConformer(conf, assignId=True)
        try:
            Chem.AssignChiralTypesFromBondDirs(mol, confId=0, replaceExistingTags=True)
        except Exception:
            pass
        try:
            Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
        except Exception:
            pass
        try:
            rdCIPLabeler.AssignCIPLabels(mol)
        except Exception:
            pass

    def _mol_from_node_ids(self, ids: set[int], return_idmap: bool = False) -> Chem.Mol | tuple[Chem.Mol, dict[int, int]] | None:
        if not ids:
            return None
        rw = Chem.RWMol()
        idmap: dict[int, int] = {}
        for n in self.nodes:
            if n["id"] not in ids:
                continue
            if _is_wildcard_node(n):
                sm = _wildcard_query_smarts(_normalize_wildcard_elements(n))
                try:
                    a = Chem.AtomFromSmarts(sm)
                except Exception:
                    a = Chem.AtomFromSmarts(_wildcard_query_smarts(list(DEFAULT_WILDCARD_ELEMENTS)))
            else:
                a = Chem.Atom(n["element"])
            fc = self._formal_charge(n)
            if fc != 0:
                a.SetFormalCharge(fc)
            idx = rw.AddAtom(a)
            idmap[n["id"]] = idx
        for bond in self.bonds:
            a, b, order, stereo = _bond_unpack(bond)
            if a not in ids or b not in ids:
                continue
            ai, bi = idmap.get(a), idmap.get(b)
            if ai is None or bi is None:
                continue
            bt = Chem.BondType.SINGLE
            if order == 2:
                bt = Chem.BondType.DOUBLE
            elif order == 3:
                bt = Chem.BondType.TRIPLE
            try:
                rw.AddBond(ai, bi, bt)
            except Exception:
                pass
            if order == 1 and stereo in (1, 2):
                bobj = rw.GetBondBetweenAtoms(ai, bi)
                if bobj is not None:
                    if stereo == 1:
                        bobj.SetBondDir(BondDir.BEGINWEDGE)
                    else:
                        bobj.SetBondDir(_BOND_DIR_HASH)
        mol = rw.GetMol()
        self._apply_sketch_coords_and_stereo(mol, idmap)
        if return_idmap:
            return mol, idmap
        return mol

    def load_from_rdkit_mol(
        self, mol: Chem.Mol, center: QPoint | None = None, preserve_existing_2d: bool = False
    ) -> bool:
        """
        Replace the sketch with a 2D layout from an RDKit molecule (e.g. from the main table).
        If ``preserve_existing_2d`` is True and the molecule already has a conformer, that geometry
        is scaled into the widget instead of calling the 2D depictor (used after ``AddHs``).
        Returns False if the molecule could not be laid out.
        """
        if mol is None or mol.GetNumAtoms() == 0:
            return False
        try:
            m = Chem.Mol(mol)
        except Exception:
            return False
        try:
            Chem.SanitizeMol(m)
        except Exception:
            try:
                m.UpdatePropertyCache(strict=False)
                Chem.SanitizeMol(m, sanitizeOps=Chem.SanitizeFlags.SANITIZE_PROPERTIES)
            except Exception:
                try:
                    m.UpdatePropertyCache(strict=False)
                except Exception:
                    return False
        try:
            Chem.AssignStereochemistry(m, cleanIt=True, force=False)
        except Exception:
            pass
        try:
            Chem.Kekulize(m)
        except Exception:
            pass
        use_existing = bool(preserve_existing_2d and m.GetNumConformers() > 0)
        if not use_existing:
            try:
                rdDepictor.Compute2DCoords(m)
            except Exception:
                return False
        conf = m.GetConformer(0)
        try:
            AllChem.WedgeMolBonds(m, conf)
        except Exception:
            pass
        na = m.GetNumAtoms()
        lens: list[float] = []
        for bond in m.GetBonds():
            i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            pa, pb = conf.GetAtomPosition(i), conf.GetAtomPosition(j)
            lens.append(math.hypot(pa.x - pb.x, pa.y - pb.y))
        med = sorted(lens)[len(lens) // 2] if lens else 1.5
        scale = float(SKETCH_MEDIAN_BOND_PX) / max(med, 0.01)
        xs = [conf.GetAtomPosition(i).x for i in range(na)]
        ys = [conf.GetAtomPosition(i).y for i in range(na)]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        r = self.rect()
        wc = center if center is not None else (r.center() if r.width() > 8 and r.height() > 8 else QPoint(250, 200))

        self.clear()
        self._undo.clear()
        self._redo.clear()

        rd2sk: dict[int, int] = {}
        for idx in range(na):
            atom = m.GetAtomWithIdx(idx)
            sym = atom.GetSymbol()
            pos = conf.GetAtomPosition(idx)
            nx = int(round(wc.x() + (pos.x - mx) * scale))
            ny = int(round(wc.y() - (pos.y - my) * scale))
            nid = self.next_id
            self.next_id += 1
            rd2sk[idx] = nid
            node: dict[str, Any] = {"id": nid, "pos": QPoint(nx, ny), "element": sym}
            fc = atom.GetFormalCharge()
            if fc:
                node["charge"] = int(fc)
            self.nodes.append(node)

        for bond in m.GetBonds():
            ib, ie = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            bt = bond.GetBondType()
            order = 1
            if bt == Chem.BondType.DOUBLE:
                order = 2
            elif bt == Chem.BondType.TRIPLE:
                order = 3
            elif bt == Chem.BondType.AROMATIC:
                order = 1
            stereo = 0
            a_rd, b_rd = ib, ie
            if order == 1:
                bd = bond.GetBondDir()
                if bd == BondDir.BEGINWEDGE:
                    stereo = 1
                elif bd == _BOND_DIR_HASH:
                    stereo = 2
                elif bd == BondDir.ENDDOWNRIGHT:
                    # Wedged single bond: narrow end at the bond's end atom (RDKit "END" convention).
                    stereo = 1
                    a_rd, b_rd = ie, ib
                elif bd == BondDir.ENDUPRIGHT:
                    # Hashed / dashed bond with narrow end at the bond's end atom.
                    stereo = 2
                    a_rd, b_rd = ie, ib
            a, b = rd2sk[a_rd], rd2sk[b_rd]
            self.bonds.append(_bond_make(a, b, order, stereo))
        self._ensure_bonds_sanitized()

        def _finish_rdkit_load() -> None:
            if not self.nodes:
                return
            self._view_scale = 1.0
            self._refresh_sketch_draw_metrics()
            self._after_sketch_edit(notify=True, notify_if_valence_failed=True)

        QTimer.singleShot(0, _finish_rdkit_load)
        return True

    def _depict_add_hs_mol_fixed_heavy(self, mh: Chem.Mol, na_heavy: int, idmap: dict[int, int]) -> bool:
        """
        Re-run 2D depiction with heavy-atom coordinates pinned to the sketch so added hydrogens
        get standard bond angles and avoid overlapping the heavy-atom labels.
        """
        if na_heavy <= 0 or mh.GetNumAtoms() <= na_heavy:
            return False
        inv = {rd: sk for sk, rd in idmap.items()}
        sc = _SKETCH_COORD_SCALE
        try:
            conf0 = mh.GetConformer(0)
        except Exception:
            return False
        coord_map: dict[int, Point2D] = {}
        for rd in range(na_heavy):
            sk_id = inv.get(rd)
            node = next((n for n in self.nodes if n["id"] == sk_id), None) if sk_id is not None else None
            if node is None:
                p = conf0.GetAtomPosition(rd)
                coord_map[rd] = Point2D(float(p.x), float(p.y))
                continue
            pos = node["pos"]
            coord_map[rd] = Point2D(float(pos.x()) / sc, float(-pos.y()) / sc)
        try:
            rdDepictor.Compute2DCoords(mh, coordMap=coord_map)
            return True
        except Exception:
            return False

    def add_explicit_hydrogens_from_implicit(self) -> tuple[bool, str]:
        """
        Replace the sketch with the same connectivity plus explicit H atoms (RDKit ``AddHs``),
        keeping heavy-atom 2D positions from the current drawing when possible.
        Returns ``(True, "")`` on success, or ``(False, reason)``.
        """
        if not self.nodes:
            return False, "The sketch is empty."
        if self.sketch_has_wildcards():
            return False, "Remove wildcard atoms first; implicit hydrogens are only added for normal elements."
        ids = {n["id"] for n in self.nodes}
        out = self._mol_from_node_ids(ids, return_idmap=True)
        if out is None:
            return False, "Could not build a molecule from the sketch."
        m0, idmap = out
        if m0 is None or m0.GetNumAtoms() == 0:
            return False, "Could not build a molecule from the sketch."
        if not _sanitize_mol_for_smiles(m0):
            try:
                m0.UpdatePropertyCache(strict=False)
            except Exception:
                pass
        na_before = m0.GetNumAtoms()
        try:
            mh = Chem.AddHs(Chem.Mol(m0), addCoords=True)
        except Exception as e:
            return False, f"RDKit could not add hydrogens ({e})."
        if mh.GetNumAtoms() <= na_before:
            return (
                False,
                "There are no implicit hydrogens to add; valences may already be fully explicit.",
            )
        if not self._depict_add_hs_mol_fixed_heavy(mh, na_before, idmap):
            pass
        r = self.rect()
        center = r.center() if r.width() > 8 and r.height() > 8 else QPoint(250, 200)
        if not self.load_from_rdkit_mol(mh, center=center, preserve_existing_2d=True):
            return False, "Could not place the expanded structure in the sketcher."
        return True, ""

    def add_explicit_hydrogens_on_atom(self, nid: int) -> tuple[bool, str]:
        """
        Add RDKit ``AddHs`` hydrogens that are bonded only to the given heavy atom, using the
        expanded molecule's conformer to place new H nodes relative to that atom's sketch position.
        """
        if self.sketch_has_wildcards():
            return False, "Remove wildcard atoms first."
        node = next((n for n in self.nodes if n["id"] == nid), None)
        if node is None:
            return False, "Atom not found."
        if _is_wildcard_node(node):
            return False, "Not supported for wildcard atoms."
        ids = {n["id"] for n in self.nodes}
        out = self._mol_from_node_ids(ids, return_idmap=True)
        if out is None:
            return False, "Could not build a molecule from the sketch."
        m0, idmap = out
        hi = idmap.get(nid)
        if hi is None:
            return False, "Internal layout error for this atom."
        if not _sanitize_mol_for_smiles(m0):
            try:
                m0.UpdatePropertyCache(strict=False)
            except Exception:
                pass
        na0 = m0.GetNumAtoms()
        try:
            mh = Chem.AddHs(Chem.Mol(m0), addCoords=True)
        except Exception as e:
            return False, f"RDKit could not add hydrogens ({e})."
        self._depict_add_hs_mol_fixed_heavy(mh, na0, idmap)
        conf = mh.GetConformer(0)
        hm = conf.GetAtomPosition(hi)
        hpos = node["pos"]
        sc = _SKETCH_COORD_SCALE
        new_h_rd: list[int] = []
        for idx in range(na0, mh.GetNumAtoms()):
            a = mh.GetAtomWithIdx(idx)
            if a.GetAtomicNum() != 1:
                continue
            nbrs = [n.GetIdx() for n in a.GetNeighbors()]
            if nbrs == [hi]:
                new_h_rd.append(idx)
        if not new_h_rd:
            return False, "There are no implicit hydrogens to add on this atom."
        new_nodes: list[dict[str, Any]] = []
        new_bonds: list[tuple[int, int, int, int]] = []
        for hidx in new_h_rd:
            ap = conf.GetAtomPosition(hidx)
            dx = (ap.x - hm.x) * sc
            dy = -(ap.y - hm.y) * sc
            hid = self.next_id
            self.next_id += 1
            nh = {"id": hid, "pos": QPoint(int(round(hpos.x() + dx)), int(round(hpos.y() + dy))), "element": "H"}
            new_nodes.append(nh)
            new_bonds.append(_bond_make(nid, hid, 1, 0))
        self.nodes.extend(new_nodes)
        self.bonds.extend(new_bonds)
        self._push_undo("add_hs_local", {"nodes": new_nodes, "bonds": new_bonds})
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)
        return True, ""

    def _format_cip_chiral_summary(self) -> str:
        """Short Cahn–Ingold–Prelog R/S summary for status line (from wedge/hash + sketch geometry)."""
        if not self._chiral_center_ids:
            return ""
        try:
            ids = {n["id"] for n in self.nodes}
            out = self._mol_from_node_ids(ids, return_idmap=True)
            if out is None:
                return ""
            mol, sk2rd = out
            inv = {v: k for k, v in sk2rd.items()}
            labels: list[str] = []
            for cen in Chem.FindMolChiralCenters(
                mol,
                includeUnassigned=True,
                includeCIP=True,
                useLegacyImplementation=False,
            ):
                if len(cen) < 2:
                    continue
                idx, cip = cen[0], cen[1]
                if cip not in ("R", "S"):
                    continue
                sk = inv.get(idx)
                if sk is None:
                    continue
                el = next((n["element"] for n in self.nodes if n["id"] == sk), "?")
                labels.append(f"{el}{sk}={cip}")
            if not labels:
                return ""
            txt = ", ".join(labels[:5])
            if len(labels) > 5:
                txt += ", …"
            return f" · CIP: {txt}"
        except Exception:
            return ""

    def _format_alkene_ez_summary(self) -> str:
        """Short E/Z summary for status (2D layout + canonical rank ligands; cis/trans aligns when substituents match textbook cases)."""
        d = getattr(self, "_alkene_ez_by_bond_index", {}) or {}
        if not d:
            return ""
        parts: list[str] = []
        for bi in sorted(d.keys()):
            lab = d.get(bi)
            if lab not in ("E", "Z"):
                continue
            if bi < 0 or bi >= len(self.bonds):
                continue
            a, b, o, _s = _bond_unpack(self.bonds[bi])
            if o != 2:
                continue
            ela = next((n["element"] for n in self.nodes if n["id"] == a), "?")
            elb = next((n["element"] for n in self.nodes if n["id"] == b), "?")
            parts.append(f"{ela}={elb}:{lab}")
        if not parts:
            return ""
        txt = ", ".join(parts[:4])
        if len(parts) > 4:
            txt += ", …"
        return f" · Alkene E/Z: {txt}"

    def to_smiles(self) -> str:
        """
        Export SMILES for all **connected components** (fragments), joined with '.'.
        Does not hard-fail on local valence warnings: RDKit sanitize + fallbacks handle charges.
        Fragments that contain wildcard atoms export as SMARTS (RDKit ``MolToSmarts``).
        """
        if not self.nodes:
            return ""
        parts = self.fragment_smiles_parts()
        return ".".join(parts) if parts else ""

    def to_smarts(self) -> str:
        """RDKit SMARTS for the full sketch (fragments joined with '.')."""
        if not self.nodes:
            return ""
        parts = self.fragment_smarts_parts()
        return ".".join(parts) if parts else ""

    def _component_has_wildcard(self, comp: set[int]) -> bool:
        return any(_is_wildcard_node(n) for n in self.nodes if n["id"] in comp)

    def _component_to_smiles(self, comp: set[int]) -> str | None:
        m = self._mol_from_node_ids(comp)
        if m is None or m.GetNumAtoms() == 0:
            return None
        if not _sanitize_mol_for_smiles(m):
            try:
                m.UpdatePropertyCache(strict=False)
            except Exception:
                pass
        if self._component_has_wildcard(comp):
            try:
                return Chem.MolToSmarts(m)
            except Exception:
                try:
                    m.UpdatePropertyCache(strict=False)
                    return Chem.MolToSmarts(m)
                except Exception:
                    return None
        try:
            return mol_to_canonical_smiles(m, isomeric=True)
        except Exception:
            try:
                m.UpdatePropertyCache(strict=False)
                return mol_to_canonical_smiles(m, isomeric=True)
            except Exception:
                return None

    def _component_to_smarts(self, comp: set[int]) -> str | None:
        m = self._mol_from_node_ids(comp)
        if m is None or m.GetNumAtoms() == 0:
            return None
        if not _sanitize_mol_for_smiles(m):
            try:
                m.UpdatePropertyCache(strict=False)
            except Exception:
                pass
        try:
            return Chem.MolToSmarts(m)
        except Exception:
            try:
                m.UpdatePropertyCache(strict=False)
                return Chem.MolToSmarts(m)
            except Exception:
                return None

    def fragment_smiles_parts(self) -> list[str]:
        """SMILES per table row: each ungrouped fragment is one entry; a user group is one dot-separated SMILES."""
        self._salt_invalidate_if_stale()
        all_frags = self.connected_components()
        smi_b = self._salt_bundle_smiles
        U = self._salt_bundle_nodes
        if smi_b and U is not None:
            out: list[str] = [smi_b]
            for c in all_frags:
                if not (c <= U):
                    s = self._component_to_smiles(c)
                    if s:
                        out.append(s)
            return out
        return [s for c in all_frags if (s := self._component_to_smiles(c))]

    def fragment_smarts_parts(self) -> list[str]:
        """SMARTS string per fragment (same grouping rules as ``fragment_smiles_parts``)."""
        self._salt_invalidate_if_stale()
        all_frags = self.connected_components()
        smi_b = self._salt_bundle_smiles
        U = self._salt_bundle_nodes
        if smi_b and U is not None:
            out: list[str] = [smi_b]
            for c in all_frags:
                if not (c <= U):
                    s = self._component_to_smarts(c)
                    if s:
                        out.append(s)
            return out
        return [s for c in all_frags if (s := self._component_to_smarts(c))]

    def _build_grouped_export_smiles_from_components(
        self, comps: list[set[int]]
    ) -> tuple[str | None, bool]:
        """
        One dot-separated SMILES for the grouped fragments.

        Returns (smiles, is_salt). ``is_salt`` is True only when at least one fragment has net
        positive formal charge and at least one has net negative formal charge; then cations are
        listed before anions. Otherwise fragments are co-grouped only: stable order, still one
        entry (multiple disconnected structures in one SMILES string).
        """
        rows: list[tuple[set[int], str, int]] = []
        for comp in comps:
            smi = self._component_to_smiles(comp)
            if not smi:
                continue
            m = self._mol_from_node_ids(comp)
            if m is None or m.GetNumAtoms() == 0:
                continue
            q = self._mol_net_formal_charge(m)
            rows.append((comp, smi, q))
        if len(rows) < 2:
            return None, False
        pos = [x for x in rows if x[2] > 0]
        neg = [x for x in rows if x[2] < 0]
        neu = [x for x in rows if x[2] == 0]
        is_salt = bool(pos and neg)
        if is_salt:
            pos.sort(key=lambda x: -x[2])
            neg.sort(key=lambda x: x[2])
            ordered = pos + neg + neu
        else:
            ordered = sorted(rows, key=lambda x: (min(x[0]) if x[0] else 0))
        return ".".join(x[1] for x in ordered), is_salt

    def apply_group_from_selection(self) -> bool:
        """Group selected fragments into one export/table SMILES entry. Does not add to the table."""
        self._salt_invalidate_if_stale()
        sel = self._selected_node_set()
        if len(sel) < 2:
            return False
        comps = [c for c in self.connected_components() if c & sel]
        if len(comps) < 2:
            return False
        combined, is_salt = self._build_grouped_export_smiles_from_components(comps)
        if not combined:
            return False
        if Chem.MolFromSmiles(combined) is None and Chem.MolFromSmarts(combined) is None:
            return False
        union = frozenset().union(*comps)
        self._salt_bundle_smiles = combined
        self._salt_bundle_nodes = union
        self._salt_bundle_fragment_count = len(comps)
        self._group_bundle_is_salt = is_salt
        self._notify_sketch_changed()
        self.update()
        return True

    def ungroup_for_export(self) -> bool:
        """Clear the active export group so each fragment is its own entry again."""
        if self._salt_bundle_nodes is None:
            return False
        self._clear_salt_bundle()
        self._notify_sketch_changed()
        self.update()
        return True

    def _run_group_selection_menu(self) -> None:
        ok = self.apply_group_from_selection()
        p = self._sketcher_dialog_if()
        if p is not None:
            if ok:
                p._update_sketch_status()
            else:
                QMessageBox.information(
                    p,
                    "Group",
                    "Turn on Select, pick atoms from at least two disconnected structures, then group.\n\n"
                    "If some fragments are net cations and others net anions, they are treated as a salt "
                    "(cation SMILES before anion). Otherwise the group is not a salt: fragments stay "
                    "separate structures in one SMILES entry (dot-separated) and one table row when added.",
                )

    def cleanup_layout_2d(self) -> bool:
        """Reposition all atoms using RDKit 2D coordinates (idealized bond angles/geometry)."""
        ids = {n["id"] for n in self.nodes}
        if not ids:
            return False
        try:
            out = self._mol_from_node_ids(ids, return_idmap=True)
            if out is None:
                return False
            mol, sk2rd = out
            if mol.GetNumAtoms() == 0:
                return False
            mol = Chem.Mol(mol)
            try:
                Chem.SanitizeMol(mol)
            except Exception:
                try:
                    mol.UpdatePropertyCache(strict=False)
                    Chem.SanitizeMol(mol, sanitizeOps=Chem.SanitizeFlags.SANITIZE_PROPERTIES)
                except Exception:
                    mol.UpdatePropertyCache(strict=False)
            rdDepictor.Compute2DCoords(mol)
            conf = mol.GetConformer(0)
            lens: list[float] = []
            for b in self.bonds:
                a0, b0, _, __ = _bond_unpack(b)
                ia, ib = sk2rd.get(a0), sk2rd.get(b0)
                if ia is None or ib is None:
                    continue
                pa, pb = conf.GetAtomPosition(ia), conf.GetAtomPosition(ib)
                lens.append(math.hypot(pa.x - pb.x, pa.y - pb.y))
            med = sorted(lens)[len(lens) // 2] if lens else 1.5
            scale = float(SKETCH_MEDIAN_BOND_PX) / max(med, 0.01)
            xs = [conf.GetAtomPosition(i).x for i in range(mol.GetNumAtoms())]
            ys = [conf.GetAtomPosition(i).y for i in range(mol.GetNumAtoms())]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            wc = self.rect().center()
            moves: list[tuple[int, QPoint, QPoint]] = []
            for n in self.nodes:
                nid = n["id"]
                if nid not in sk2rd:
                    continue
                i = sk2rd[nid]
                po = conf.GetAtomPosition(i)
                nx = int(round(wc.x() + (po.x - mx) * scale))
                ny = int(round(wc.y() - (po.y - my) * scale))
                oldp = n["pos"]
                newp = QPoint(nx, ny)
                if oldp.x() != newp.x() or oldp.y() != newp.y():
                    moves.append((nid, QPoint(oldp.x(), oldp.y()), newp))
            if not moves:
                return True
            for nid, _oldp, newp in moves:
                node = next((x for x in self.nodes if x["id"] == nid), None)
                if node:
                    node["pos"] = newp
            self._push_undo("move_nodes", moves)
            self._after_sketch_edit()
            return True
        except Exception:
            return False
