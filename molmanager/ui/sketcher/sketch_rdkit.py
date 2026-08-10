"""RDKit integration for the sketch widget: mol build, 2D load, SMILES/SMARTS export, and AddHs helpers."""

from __future__ import annotations

import math
from typing import Any

from PyQt5.QtCore import QPoint, QTimer
from PyQt5.QtWidgets import QMessageBox

from rdkit import Chem
from rdkit.Chem import AllChem, rdCIPLabeler, rdDepictor
from rdkit.Chem.rdchem import BondDir, Conformer
from rdkit.Geometry import Point2D, Point3D

from ...utils import mol_to_canonical_smiles

from .bonds import (
    BOND_STEREO_DATIVE,
    BOND_STEREO_HASH,
    BOND_STEREO_WAVY,
    BOND_STEREO_WEDGE,
    _bond_make,
    _bond_unpack,
    sanitize_sketch_stereo_bonds,
)
from .chem import (
    _rdkit_atom_from_sketch_node,
    _sanitize_mol_for_smiles,
    _sketch_element_from_rdkit_atom,
)
from .constants import (
    BOND_DIR_HASH as _BOND_DIR_HASH,
    SKETCH_COORD_SCALE as _SKETCH_COORD_SCALE,
    SKETCH_MEDIAN_BOND_PX,
)
from .contracted_labels import expand_contracted_label_on_atom
from .iupac_orient import (
    apply_iupac_orientation,
    apply_iupac_orientation_to_conformer,
    resolve_layout_overlaps_on_conformer,
)
from .iupac_rings import large_ring_offsets_y_up, rotate_offsets_to_inward_heteroatoms
from .wildcards import _is_wildcard_node


class SketchWidgetRdkitMixin:
    """Mixed into ``SketchWidget`` after paint/events mixins, before ``QWidget``."""

    @staticmethod
    def _mol_net_formal_charge(mol: Chem.Mol) -> int:
        return sum(mol.GetAtomWithIdx(i).GetFormalCharge() for i in range(mol.GetNumAtoms()))

    @staticmethod
    def _conformer_is_3d(conf, *, z_eps: float = 1e-3) -> bool:
        try:
            n = conf.GetNumAtoms()
        except Exception:
            return False
        for i in range(n):
            if abs(float(conf.GetAtomPosition(i).z)) > z_eps:
                return True
        return False

    def _structure_has_usable_2d(self, mol: Chem.Mol) -> bool:
        """True when *mol* has a 2D conformer suitable for table-style depiction."""
        if mol is None or mol.GetNumConformers() == 0:
            return False
        try:
            return not self._conformer_is_3d(mol.GetConformer(0))
        except Exception:
            return False

    @staticmethod
    def _capture_tetrahedral_chiral_tags(mol: Chem.Mol) -> dict[int, Any]:
        tags: dict[int, Any] = {}
        for atom in mol.GetAtoms():
            ct = atom.GetChiralTag()
            if ct != Chem.ChiralType.CHI_UNSPECIFIED:
                tags[int(atom.GetIdx())] = ct
        return tags

    @staticmethod
    def _restore_tetrahedral_chiral_tags(mol: Chem.Mol, tags: dict[int, Any]) -> None:
        for idx, ct in tags.items():
            try:
                mol.GetAtomWithIdx(int(idx)).SetChiralTag(ct)
            except Exception:
                pass

    @staticmethod
    def _clear_bond_dirs(mol: Chem.Mol) -> None:
        for bond in mol.GetBonds():
            try:
                bond.SetBondDir(BondDir.NONE)
            except Exception:
                pass

    def _assign_tetrahedral_cip(self, mol: Chem.Mol) -> dict[int, str]:
        """
        Assign tetrahedral CIP labels with macrocycle-friendly fallbacks.

        Returns ``{rdkit_atom_idx: 'R'|'S'|'?'|''}`` for stereogenic (or potentially
        stereogenic) tetrahedral atoms.
        """
        out: dict[int, str] = {}
        try:
            mol.UpdatePropertyCache(strict=False)
        except Exception:
            pass
        try:
            Chem.AssignChiralTypesFromBondDirs(mol, confId=0, replaceExistingTags=False)
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

        def _ingest(centers) -> None:
            for cen in centers:
                idx = int(cen[0])
                tag = str(cen[1]) if len(cen) >= 2 else ""
                prev = out.get(idx, "")
                if prev in ("R", "S") and tag not in ("R", "S"):
                    continue
                out[idx] = tag

        for legacy in (False, True):
            try:
                _ingest(
                    Chem.FindMolChiralCenters(
                        mol,
                        includeUnassigned=True,
                        includeCIP=True,
                        useLegacyImplementation=legacy,
                    )
                )
            except Exception:
                pass

        # FindPotentialStereo catches additional ring stereocenters RDKit may miss
        # until CIP/property cache is fully warmed (common for macrocycles).
        try:
            for si in Chem.FindPotentialStereo(mol):
                try:
                    centered = int(si.centeredOn)
                except Exception:
                    continue
                type_name = str(getattr(si, "type", "") or "")
                if "Tetrahedral" not in type_name and "Atom_Tetrahedral" not in type_name:
                    continue
                if centered in out and out[centered] in ("R", "S"):
                    continue
                atom = mol.GetAtomWithIdx(centered)
                cip = ""
                try:
                    if atom.HasProp("_CIPCode"):
                        cip = str(atom.GetProp("_CIPCode") or "")
                except Exception:
                    cip = ""
                if cip in ("R", "S"):
                    out[centered] = cip
                    continue
                # Only promote potential centers that already carry a chiral tag.
                try:
                    if atom.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED:
                        out.setdefault(centered, cip or "?")
                except Exception:
                    pass
        except Exception:
            pass
        return out

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
        self._assign_tetrahedral_cip(mol)

    def _depict_mol_2d_table_match(
        self, mol: Chem.Mol, *, preserve_existing_2d: bool = True
    ) -> bool:
        """
        Match the table structure window: RDKit default 2D (no CoordGen preference,
        no IUPAC orientation / macrocycle hex). Used for table→sketcher load only.
        """
        try:
            if hasattr(rdDepictor, "SetPreferCoordGen"):
                rdDepictor.SetPreferCoordGen(False)
        except Exception:
            pass
        keep = preserve_existing_2d and self._structure_has_usable_2d(mol)
        if not keep:
            try:
                mol.RemoveAllConformers()
            except Exception:
                pass
            try:
                rdDepictor.Compute2DCoords(mol)
            except Exception:
                return False
        return mol.GetNumConformers() > 0

    def _place_new_atoms_with_pinned_heavies(self, mol: Chem.Mol, n_pinned: int) -> bool:
        """After AddHs, recompute 2D with atoms ``0..n_pinned-1`` fixed (table pose)."""
        if mol.GetNumAtoms() <= n_pinned or mol.GetNumConformers() == 0:
            return True
        conf = mol.GetConformer(0)
        coord_map = {
            i: Point2D(float(conf.GetAtomPosition(i).x), float(conf.GetAtomPosition(i).y))
            for i in range(n_pinned)
        }
        try:
            if hasattr(rdDepictor, "SetPreferCoordGen"):
                rdDepictor.SetPreferCoordGen(False)
            rdDepictor.Compute2DCoords(mol, coordMap=coord_map)
        except Exception:
            return False
        return mol.GetNumConformers() > 0

    def _depict_mol_2d_iupac(self, mol: Chem.Mol, *, clear_conformers: bool = True) -> bool:
        """
        Generate IUPAC-oriented 2D coords (CoordGen preferred + GR-3 orient + macrocycle hex).

        Used by Clean Up and interactive drawing tidy — not table→sketcher load.
        """
        try:
            if clear_conformers:
                mol.RemoveAllConformers()
        except Exception:
            pass
        try:
            if hasattr(rdDepictor, "SetPreferCoordGen"):
                rdDepictor.SetPreferCoordGen(True)
        except Exception:
            pass
        try:
            rdDepictor.Compute2DCoords(mol)
        except Exception:
            return False
        if mol.GetNumConformers() == 0:
            return False
        conf = mol.GetConformer(0)
        elements = [mol.GetAtomWithIdx(i).GetSymbol() for i in range(mol.GetNumAtoms())]
        bonds_ij: list[tuple[int, int]] = []
        bond_ords: list[int] = []
        for bond in mol.GetBonds():
            bonds_ij.append((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()))
            bt = bond.GetBondType()
            if bt == Chem.BondType.TRIPLE:
                bond_ords.append(3)
            elif bt == Chem.BondType.DOUBLE:
                bond_ords.append(2)
            else:
                bond_ords.append(1)
        apply_iupac_orientation_to_conformer(
            conf, elements=elements, bonds=bonds_ij, bond_orders=bond_ords
        )
        self._refine_isolated_macrocycles_on_conformer(mol, conf)
        # Macrocycle reshape can undo preferred pose / introduce stacked bonds — re-score.
        resolve_layout_overlaps_on_conformer(
            conf, elements=elements, bonds=bonds_ij, bond_orders=bond_ords
        )
        return True

    def _reorient_sketch_iupac(self) -> None:
        """Apply GR-3 orientation to current sketch nodes (screen Y-down → chemistry Y-up)."""
        if len(self.nodes) < 2:
            return
        ids = [int(n["id"]) for n in self.nodes]
        idx = {nid: i for i, nid in enumerate(ids)}
        xs = [float(n["pos"].x()) for n in self.nodes]
        ys = [-float(n["pos"].y()) for n in self.nodes]  # screen → Y-up
        elements = [str(n.get("element") or "C") for n in self.nodes]
        bonds_ij: list[tuple[int, int]] = []
        bond_ords: list[int] = []
        for b in self.bonds:
            a, bo, o, _s = _bond_unpack(b)
            if a not in idx or bo not in idx:
                continue
            bonds_ij.append((idx[a], idx[bo]))
            bond_ords.append(int(o))
        if not bonds_ij:
            return
        ox, oy = apply_iupac_orientation(
            xs, ys, elements=elements, bonds=bonds_ij, bond_orders=bond_ords
        )
        # Keep centroid fixed in screen space.
        old_cx = sum(float(n["pos"].x()) for n in self.nodes) / len(self.nodes)
        old_cy = sum(float(n["pos"].y()) for n in self.nodes) / len(self.nodes)
        new_cx = sum(ox) / len(ox)
        new_cy = sum(-y for y in oy) / len(oy)  # Y-up → screen
        for n, x, y in zip(self.nodes, ox, oy):
            n["pos"] = QPoint(
                int(round(x - new_cx + old_cx)),
                int(round((-y) - new_cy + old_cy)),
            )

    def _rewedge_sketch_from_mol(
        self,
        mol: Chem.Mol,
        rd2sk: dict[int, int],
        stereo_tags: dict[int, Any] | None = None,
    ) -> None:
        """
        Push current sketch coords into *mol*, restore tetrahedral tags, and rewrite
        wedge/hash bonds so CIP matches absolute stereo after orientation flips.
        """
        if mol is None or mol.GetNumAtoms() == 0 or not rd2sk:
            return
        try:
            mol.RemoveAllConformers()
        except Exception:
            pass
        na = mol.GetNumAtoms()
        conf = Conformer(na)
        sc = _SKETCH_COORD_SCALE
        sk2node = {int(n["id"]): n for n in self.nodes}
        for rd_idx, sk_id in rd2sk.items():
            node = sk2node.get(int(sk_id))
            if node is None:
                continue
            pos = node["pos"]
            conf.SetAtomPosition(
                int(rd_idx),
                Point3D(float(pos.x()) / sc, float(-pos.y()) / sc, 0.0),
            )
        mol.AddConformer(conf, assignId=True)
        if stereo_tags:
            self._restore_tetrahedral_chiral_tags(mol, stereo_tags)
        self._clear_bond_dirs(mol)
        try:
            AllChem.WedgeMolBonds(mol, mol.GetConformer(0))
        except Exception:
            return
        inv = {int(sk): int(rd) for rd, sk in rd2sk.items()}
        rd_bonds = {}
        for bond in mol.GetBonds():
            a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            rd_bonds[(min(a, b), max(a, b))] = bond
        new_bonds = []
        for bond in self.bonds:
            a, b, order, stereo = _bond_unpack(bond)
            ia, ib = inv.get(a), inv.get(b)
            if ia is None or ib is None or order != 1 or stereo == BOND_STEREO_DATIVE:
                new_bonds.append(bond)
                continue
            rb = rd_bonds.get((min(ia, ib), max(ia, ib)))
            if rb is None:
                new_bonds.append(_bond_make(a, b, order, 0))
                continue
            bd = rb.GetBondDir()
            if bd == BondDir.BEGINWEDGE:
                tip_rd, far_rd = rb.GetBeginAtomIdx(), rb.GetEndAtomIdx()
                tip, far = rd2sk.get(tip_rd), rd2sk.get(far_rd)
                if tip is not None and far is not None:
                    new_bonds.append(_bond_make(int(tip), int(far), order, BOND_STEREO_WEDGE))
                else:
                    new_bonds.append(_bond_make(a, b, order, BOND_STEREO_WEDGE))
            elif bd == _BOND_DIR_HASH:
                tip_rd, far_rd = rb.GetBeginAtomIdx(), rb.GetEndAtomIdx()
                tip, far = rd2sk.get(tip_rd), rd2sk.get(far_rd)
                if tip is not None and far is not None:
                    new_bonds.append(_bond_make(int(tip), int(far), order, BOND_STEREO_HASH))
                else:
                    new_bonds.append(_bond_make(a, b, order, BOND_STEREO_HASH))
            elif bd == BondDir.UNKNOWN:
                new_bonds.append(_bond_make(a, b, order, BOND_STEREO_WAVY))
            else:
                new_bonds.append(_bond_make(a, b, order, 0))
        self.bonds = new_bonds
        self._ensure_bonds_sanitized()

    def _apply_hex_macrocycles_to_sketch(self) -> None:
        """
        Force isolated ≥9 rings in sketch coordinates into hexagonal GR-3.3.2 form.

        Runs after table load so the canvas matches Clean Up even if the RDKit
        conformer pass left a near-circular monocycle.
        """
        from collections import defaultdict, deque

        if len(self.nodes) < 9 or not self.bonds:
            return
        by_id = {int(n["id"]): n for n in self.nodes}
        adj: dict[int, set[int]] = defaultdict(set)
        for b in self.bonds:
            a, bo, _o, _s = _bond_unpack(b)
            adj[a].add(bo)
            adj[bo].add(a)

        def _shortest_cycle(start: int, nb: int) -> list[int] | None:
            parent: dict[int, int | None] = {start: None}
            q: deque[int] = deque([start])
            found = None
            while q:
                u = q.popleft()
                for v in adj.get(u, ()):
                    if u == start and v == nb:
                        continue
                    if v == nb:
                        found = u
                        q.clear()
                        break
                    if v in parent:
                        continue
                    parent[v] = u
                    q.append(v)
                if found is not None:
                    break
            if found is None:
                return None
            path = [nb, found]
            cur: int | None = found
            while cur is not None and cur != start:
                cur = parent.get(cur)
                if cur is None:
                    break
                path.append(cur)
            path.append(start)
            return path if len(path) >= 9 else None

        seen_rings: set[frozenset[int]] = set()
        rings: list[list[int]] = []
        for start, nbrs in adj.items():
            for nb in nbrs:
                if nb < start:
                    continue
                cyc = _shortest_cycle(start, nb)
                if cyc is None:
                    continue
                key = frozenset(cyc)
                if key in seen_rings:
                    continue
                seen_rings.add(key)
                rings.append(cyc)

        membership: dict[int, int] = defaultdict(int)
        for ring in rings:
            for a in ring:
                membership[a] += 1

        bl = float(getattr(self, "_median_bond_length_px", None) or SKETCH_MEDIAN_BOND_PX)
        for ring in rings:
            n = len(ring)
            if n < 9:
                continue
            if any(membership[a] != 1 for a in ring):
                continue
            # Skip rings with endocyclic triples (must stay linear).
            ring_set = set(ring)
            has_tpl = False
            for i in range(n):
                a, b = ring[i], ring[(i + 1) % n]
                for bond in self.bonds:
                    ba, bb, o, _s = _bond_unpack(bond)
                    if o == 3 and {ba, bb} == {a, b}:
                        has_tpl = True
                        break
                if has_tpl:
                    break
            if has_tpl:
                continue

            old = [(float(by_id[a]["pos"].x()), float(by_id[a]["pos"].y())) for a in ring]
            cx = sum(p[0] for p in old) / n
            cy = sum(p[1] for p in old) / n

            els = [str(by_id[a].get("element") or "C") for a in ring]
            # Offsets are Y-up; sketch positions are Y-down.
            offsets = large_ring_offsets_y_up(n, bond_length=bl)
            offsets = rotate_offsets_to_inward_heteroatoms(offsets, els)
            # Align first edge to current first edge, then place.
            p0x, p0y = old[0]
            p1x, p1y = old[1]
            cur_ang = math.atan2(-(p1y - p0y), p1x - p0x)  # screen → Y-up angle
            t_ang = math.atan2(offsets[1][1] - offsets[0][1], offsets[1][0] - offsets[0][0])
            rot = cur_ang - t_ang
            cos_r, sin_r = math.cos(rot), math.sin(rot)
            ox0, oy0 = offsets[0]
            new_yu: list[tuple[float, float]] = []
            for i in range(n):
                ox, oy = offsets[i][0] - ox0, offsets[i][1] - oy0
                rx = ox * cos_r - oy * sin_r
                ry = ox * sin_r + oy * cos_r
                # Anchor at old[0] in Y-up of that point: (p0x, -p0y)
                new_yu.append((p0x + rx, (-p0y) + ry))
            ncx = sum(p[0] for p in new_yu) / n
            ncy = sum(p[1] for p in new_yu) / n
            # Target centroid in Y-up equals (cx, -cy)
            dx, dy = cx - ncx, (-cy) - ncy
            new_screen = [
                (x + dx, - (y + dy)) for x, y in new_yu
            ]

            # Exocyclic substituents: rotate with local ring-edge frame (screen space).
            exo_pos: dict[int, QPoint] = {}
            for i, aid in enumerate(ring):
                o0x, o0y = old[i]
                n0x, n0y = new_screen[i]
                o1x, o1y = old[(i + 1) % n]
                n1x, n1y = new_screen[(i + 1) % n]
                a_old = math.atan2(o1y - o0y, o1x - o0x)
                a_new = math.atan2(n1y - n0y, n1x - n0x)
                drot = a_new - a_old
                ca, sa = math.cos(drot), math.sin(drot)
                stack = [v for v in adj.get(aid, ()) if v not in ring_set]
                seen = set(stack)
                while stack:
                    u = stack.pop()
                    if u not in by_id:
                        continue
                    pu = by_id[u]["pos"]
                    vx, vy = float(pu.x()) - o0x, float(pu.y()) - o0y
                    rx = vx * ca - vy * sa
                    ry = vx * sa + vy * ca
                    exo_pos[u] = QPoint(int(round(n0x + rx)), int(round(n0y + ry)))
                    for v in adj.get(u, ()):
                        if v in ring_set or v in seen:
                            continue
                        seen.add(v)
                        stack.append(v)

            for i, aid in enumerate(ring):
                by_id[aid]["pos"] = QPoint(int(round(new_screen[i][0])), int(round(new_screen[i][1])))
            for uid, pt in exo_pos.items():
                by_id[uid]["pos"] = pt

    def _mol_from_node_ids(self, ids: set[int], return_idmap: bool = False) -> Chem.Mol | tuple[Chem.Mol, dict[int, int]] | None:
        if not ids:
            return None
        rw = Chem.RWMol()
        idmap: dict[int, int] = {}
        for n in self.nodes:
            if n["id"] not in ids:
                continue
            fc = self._formal_charge(n)
            a = _rdkit_atom_from_sketch_node(n, formal_charge=fc)
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
            elif order == 1 and stereo == BOND_STEREO_DATIVE:
                bt = Chem.BondType.DATIVE
            try:
                rw.AddBond(ai, bi, bt)
            except Exception:
                pass
            if order == 1 and stereo in (BOND_STEREO_WEDGE, BOND_STEREO_HASH, BOND_STEREO_WAVY):
                bobj = rw.GetBondBetweenAtoms(ai, bi)
                if bobj is not None:
                    if stereo == BOND_STEREO_WEDGE:
                        bobj.SetBondDir(BondDir.BEGINWEDGE)
                    elif stereo == BOND_STEREO_HASH:
                        bobj.SetBondDir(_BOND_DIR_HASH)
                    else:
                        bobj.SetBondDir(BondDir.UNKNOWN)
        # Expand contracted labels (CF3, Ph, …) after skeleton bonds exist.
        for n in self.nodes:
            if n["id"] not in ids:
                continue
            ab = n.get("abbrev")
            if not ab:
                continue
            ai = idmap.get(n["id"])
            if ai is None:
                continue
            expand_contracted_label_on_atom(rw, ai, str(ab))
        mol = rw.GetMol()
        self._apply_sketch_coords_and_stereo(mol, idmap)
        if return_idmap:
            return mol, idmap
        return mol

    @staticmethod
    def _stereocenter_indices_needing_explicit_h(mol: Chem.Mol) -> list[int]:
        """Tetrahedral stereocenters that still lack an explicit H neighbor."""
        need: set[int] = set()
        try:
            mol.UpdatePropertyCache(strict=False)
        except Exception:
            pass

        def _needs_stereo_h(atom: Chem.Atom) -> bool:
            try:
                if any(nb.GetAtomicNum() == 1 for nb in atom.GetNeighbors()):
                    return False
                # RDKit may report GetNumImplicitHs()==0 while GetTotalNumHs()>0 for @H centers.
                return int(atom.GetTotalNumHs()) > 0
            except Exception:
                return False

        for atom in mol.GetAtoms():
            try:
                if atom.GetChiralTag() == Chem.ChiralType.CHI_UNSPECIFIED:
                    continue
                if _needs_stereo_h(atom):
                    need.add(int(atom.GetIdx()))
            except Exception:
                continue
        for legacy in (False, True):
            try:
                for cen in Chem.FindMolChiralCenters(
                    mol,
                    includeUnassigned=True,
                    includeCIP=True,
                    useLegacyImplementation=legacy,
                ):
                    idx = int(cen[0])
                    if _needs_stereo_h(mol.GetAtomWithIdx(idx)):
                        need.add(idx)
            except Exception:
                pass
        try:
            for si in Chem.FindPotentialStereo(mol):
                type_name = str(getattr(si, "type", "") or "")
                if "Tetrahedral" not in type_name and "Atom_Tetrahedral" not in type_name:
                    continue
                idx = int(si.centeredOn)
                if _needs_stereo_h(mol.GetAtomWithIdx(idx)):
                    need.add(idx)
        except Exception:
            pass
        return sorted(need)

    def _add_stereochemical_hydrogens(self, mol: Chem.Mol) -> Chem.Mol:
        """
        Add explicit H only at tetrahedral stereocenters that still have implicit H.

        Drawn stereo-H (typically wedged/hashed) prevents unspecified-stereocenter
        flags when a structure is loaded into the sketcher from the table.
        """
        if mol is None or mol.GetNumAtoms() == 0:
            return mol
        try:
            centers = self._stereocenter_indices_needing_explicit_h(mol)
        except Exception:
            centers = []
        if not centers:
            return mol
        try:
            mh = Chem.AddHs(Chem.Mol(mol), onlyOnAtoms=centers, addCoords=False)
        except TypeError:
            # Older RDKit: no onlyOnAtoms — add all Hs then strip non-stereo H.
            try:
                mh = Chem.AddHs(Chem.Mol(mol), addCoords=False)
            except Exception:
                return mol
            keep_h: set[int] = set()
            center_set = set(centers)
            for atom in mh.GetAtoms():
                if atom.GetAtomicNum() != 1:
                    continue
                for nb in atom.GetNeighbors():
                    if nb.GetIdx() in center_set:
                        keep_h.add(atom.GetIdx())
                        break
            if keep_h:
                remove = [
                    i
                    for i in range(mh.GetNumAtoms())
                    if mh.GetAtomWithIdx(i).GetAtomicNum() == 1 and i not in keep_h
                ]
                if remove:
                    mh = Chem.RWMol(mh)
                    for i in sorted(remove, reverse=True):
                        mh.RemoveAtom(i)
                    mh = mh.GetMol()
            else:
                return mol
        except Exception:
            return mol
        try:
            Chem.SanitizeMol(mh)
        except Exception:
            try:
                mh.UpdatePropertyCache(strict=False)
            except Exception:
                pass
        try:
            Chem.AssignStereochemistry(mh, cleanIt=True, force=True)
        except Exception:
            pass
        return mh

    def load_from_rdkit_mol(
        self, mol: Chem.Mol, center: QPoint | None = None, preserve_existing_2d: bool = False
    ) -> bool:
        """
        Replace the sketch with a 2D layout matching the table structure picture.

        Uses RDKit default depiction (or an existing 2D SDF conformer), not IUPAC
        Clean Up orientation/hex. Stereochemical H are added with heavy atoms pinned.
        If ``preserve_existing_2d`` is True, the molecule's current conformer is used
        as-is (e.g. after AddHs with pinned heavies). Returns False if layout fails.
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

        # Prefer absolute stereo from 3D when the table mol carries a real 3D conformer.
        try:
            if m.GetNumConformers() > 0 and self._conformer_is_3d(m.GetConformer(0)):
                Chem.AssignStereochemistryFrom3D(m)
            else:
                Chem.AssignStereochemistry(m, cleanIt=True, force=True)
        except Exception:
            try:
                Chem.AssignStereochemistry(m, cleanIt=True, force=False)
            except Exception:
                pass

        try:
            Chem.Kekulize(m)
        except Exception:
            pass

        # Table load: match structure_draw (RDKit default 2D / existing SDF coords).
        # IUPAC CoordGen + orient + macrocycle hex run only on Clean Up / draw tidy.
        # Stereo-H after layout with heavies pinned so the skeleton matches the table PNG.
        use_existing = bool(preserve_existing_2d and m.GetNumConformers() > 0)
        if not use_existing:
            n_heavy = m.GetNumAtoms()
            if not self._depict_mol_2d_table_match(m, preserve_existing_2d=True):
                return False
            m = self._add_stereochemical_hydrogens(m)
            if m.GetNumAtoms() > n_heavy:
                if not self._place_new_atoms_with_pinned_heavies(m, n_heavy):
                    return False

        stereo_tags = self._capture_tetrahedral_chiral_tags(m)
        conf = m.GetConformer(0)
        # Re-apply tetrahedral tags after layout, then place wedges.
        self._restore_tetrahedral_chiral_tags(m, stereo_tags)
        self._clear_bond_dirs(m)
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

        self.clear(push_undo=False)
        self._undo.clear()
        self._redo.clear()

        rd2sk: dict[int, int] = {}
        for idx in range(na):
            atom = m.GetAtomWithIdx(idx)
            sym = _sketch_element_from_rdkit_atom(atom)
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
            stereo = 0
            if bt == Chem.BondType.DOUBLE:
                order = 2
            elif bt == Chem.BondType.TRIPLE:
                order = 3
            elif bt == Chem.BondType.AROMATIC:
                order = 1
            elif bt == Chem.BondType.DATIVE or bt in (
                getattr(Chem.BondType, "DATIVEONE", None),
                getattr(Chem.BondType, "DATIVEL", None),
                getattr(Chem.BondType, "DATIVER", None),
            ):
                order = 1
                stereo = BOND_STEREO_DATIVE
            a_rd, b_rd = ib, ie
            if order == 1 and stereo != BOND_STEREO_DATIVE:
                bd = bond.GetBondDir()
                if bd == BondDir.BEGINWEDGE:
                    stereo = BOND_STEREO_WEDGE
                elif bd == _BOND_DIR_HASH:
                    stereo = BOND_STEREO_HASH
                elif bd == BondDir.UNKNOWN:
                    stereo = BOND_STEREO_WAVY
                elif bd == BondDir.ENDDOWNRIGHT:
                    # Wedged single bond: narrow end at the bond's end atom (RDKit "END" convention).
                    stereo = BOND_STEREO_WEDGE
                    a_rd, b_rd = ie, ib
                elif bd == BondDir.ENDUPRIGHT:
                    # Hashed / dashed bond with narrow end at the bond's end atom.
                    stereo = BOND_STEREO_HASH
                    a_rd, b_rd = ie, ib
            a, b = rd2sk[a_rd], rd2sk[b_rd]
            self.bonds.append(_bond_make(a, b, order, stereo))
        self._ensure_bonds_sanitized()
        # Keep RDKit/table orientation; do not apply IUPAC hex or GR-3 flips on load.
        try:
            self._rewedge_sketch_from_mol(m, rd2sk, stereo_tags)
        except Exception:
            pass

        def _finish_rdkit_load() -> None:
            if not self.nodes:
                return
            self._view_scale = 1.0
            self._refresh_sketch_draw_metrics()
            self.ensure_sketch_fits_viewport(refresh=False)
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

    def sketch_has_explicit_hydrogens(self) -> bool:
        """True when the sketch contains at least one H/D/T atom node."""
        return any(n.get("element") in ("H", "D", "T") for n in self.nodes)

    def atom_has_explicit_hydrogen_neighbors(self, nid: int) -> bool:
        """True when *nid* is bonded to one or more explicit H/D/T atoms."""
        for bond in self.bonds:
            a, b, _o, _s = _bond_unpack(bond)
            other = b if a == nid else a if b == nid else None
            if other is None:
                continue
            node = next((n for n in self.nodes if n["id"] == other), None)
            if node is not None and node.get("element") in ("H", "D", "T"):
                return True
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

    def remove_explicit_hydrogens_from_sketch(self) -> tuple[bool, str]:
        """Remove all explicit H/D/T atoms and their bonds from the sketch."""
        h_nodes = [n for n in self.nodes if n.get("element") in ("H", "D", "T")]
        if not h_nodes:
            return False, "There are no explicit hydrogens to remove."
        h_ids = {n["id"] for n in h_nodes}
        removed_bonds = [b for b in self.bonds if _bond_unpack(b)[0] in h_ids or _bond_unpack(b)[1] in h_ids]
        self.bonds = [b for b in self.bonds if _bond_unpack(b)[0] not in h_ids and _bond_unpack(b)[1] not in h_ids]
        self.nodes = [n for n in self.nodes if n["id"] not in h_ids]
        self._push_undo("del_hs_local", {"nodes": h_nodes, "bonds": removed_bonds})
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)
        return True, ""

    def toggle_explicit_hydrogens(self) -> tuple[bool, str]:
        """Add implicit→explicit Hs when none are shown; otherwise remove explicit Hs."""
        if self.sketch_has_explicit_hydrogens():
            return self.remove_explicit_hydrogens_from_sketch()
        return self.add_explicit_hydrogens_from_implicit()

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

    def remove_explicit_hydrogens_on_atom(self, nid: int) -> tuple[bool, str]:
        """Remove explicit H/D/T atoms bonded only to the given atom."""
        node = next((n for n in self.nodes if n["id"] == nid), None)
        if node is None:
            return False, "Atom not found."
        h_ids: set[int] = set()
        for bond in self.bonds:
            a, b, _o, _s = _bond_unpack(bond)
            other = b if a == nid else a if b == nid else None
            if other is None:
                continue
            hn = next((n for n in self.nodes if n["id"] == other), None)
            if hn is not None and hn.get("element") in ("H", "D", "T"):
                h_ids.add(other)
        if not h_ids:
            return False, "There are no explicit hydrogens on this atom to remove."
        h_nodes = [n for n in self.nodes if n["id"] in h_ids]
        removed_bonds = [
            b for b in self.bonds if _bond_unpack(b)[0] in h_ids or _bond_unpack(b)[1] in h_ids
        ]
        self.bonds = [
            b for b in self.bonds if _bond_unpack(b)[0] not in h_ids and _bond_unpack(b)[1] not in h_ids
        ]
        self.nodes = [n for n in self.nodes if n["id"] not in h_ids]
        self._push_undo("del_hs_local", {"nodes": h_nodes, "bonds": removed_bonds})
        self._after_sketch_edit(notify=True, notify_if_valence_failed=True)
        return True, ""

    def toggle_explicit_hydrogens_on_atom(self, nid: int) -> tuple[bool, str]:
        """Add or remove explicit hydrogens on a single atom."""
        if self.atom_has_explicit_hydrogen_neighbors(nid):
            return self.remove_explicit_hydrogens_on_atom(nid)
        return self.add_explicit_hydrogens_on_atom(nid)

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
        Fragments with wildcard atoms export as SMARTS (element lists cannot be expressed in SMILES).
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

    def _mol_string_from_component(self, comp: set[int], *, as_smarts: bool) -> str | None:
        m = self._mol_from_node_ids(comp)
        if m is None or m.GetNumAtoms() == 0:
            return None
        try:
            m.UpdatePropertyCache(strict=False)
        except Exception:
            pass
        # Sanitize (incl. aromatize) before MolToSmarts so Kekulé rings export as
        # aromatic ``:`` bonds and match table molecules. Query atoms are preserved.
        if self._component_has_wildcard(comp) or as_smarts:
            _sanitize_mol_for_smiles(m)
            try:
                return Chem.MolToSmarts(m, True)
            except Exception:
                try:
                    m.UpdatePropertyCache(strict=False)
                    return Chem.MolToSmarts(m, True)
                except Exception:
                    return None
        if not _sanitize_mol_for_smiles(m):
            try:
                m.UpdatePropertyCache(strict=False)
            except Exception:
                pass
        try:
            return mol_to_canonical_smiles(m, isomeric=True)
        except Exception:
            try:
                m.UpdatePropertyCache(strict=False)
                return mol_to_canonical_smiles(m, isomeric=True)
            except Exception:
                return None

    def _component_to_smiles(self, comp: set[int]) -> str | None:
        # Wildcards → SMARTS so element choices survive in Add-to-table / Copy SMILES.
        if self._component_has_wildcard(comp):
            return self._mol_string_from_component(comp, as_smarts=True)
        return self._mol_string_from_component(comp, as_smarts=False)

    def _component_to_smarts(self, comp: set[int]) -> str | None:
        return self._mol_string_from_component(comp, as_smarts=True)

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

    def _refine_isolated_macrocycles_on_conformer(self, mol: Chem.Mol, conf) -> None:
        """
        Force isolated ≥9 rings into hexagonal GR-3.3.2 geometry (never circular).

        Skips rings that share atoms with other rings (fused/bridged). Preserves
        exocyclic substituents by rotating their offsets with each ring atom's local frame.
        Tries template reflection when endocyclic doubles are present so configuration
        is not casually inverted.
        """
        try:
            Chem.GetSymmSSSR(mol)
        except Exception:
            pass
        ri = mol.GetRingInfo()
        if ri is None or ri.NumRings() == 0:
            return
        atom_rings = list(ri.AtomRings())
        membership = [0] * mol.GetNumAtoms()
        for ring in atom_rings:
            for a in ring:
                membership[a] += 1

        def _apply_ring_offsets(
            ring: tuple[int, ...],
            offsets: list[tuple[float, float]],
            *,
            reflect: bool,
        ) -> None:
            n = len(ring)
            ring_set = set(ring)
            old = [
                (float(conf.GetAtomPosition(int(a)).x), float(conf.GetAtomPosition(int(a)).y))
                for a in ring
            ]
            offs = list(offsets)
            if reflect:
                offs = [(x, -y) for x, y in offs]
            cxs = [p[0] for p in old]
            cys = [p[1] for p in old]
            cx, cy = sum(cxs) / n, sum(cys) / n
            p0x, p0y = old[0]
            p1x, p1y = old[1]
            cur_ang = math.atan2(p1y - p0y, p1x - p0x)
            t_ang = math.atan2(offs[1][1] - offs[0][1], offs[1][0] - offs[0][0])
            rot = cur_ang - t_ang
            cos_r, sin_r = math.cos(rot), math.sin(rot)
            ox0, oy0 = offs[0]
            new: list[tuple[float, float]] = []
            for i in range(n):
                ox, oy = offs[i][0] - ox0, offs[i][1] - oy0
                rx = ox * cos_r - oy * sin_r
                ry = ox * sin_r + oy * cos_r
                new.append((p0x + rx, p0y + ry))
            ncx = sum(p[0] for p in new) / n
            ncy = sum(p[1] for p in new) / n
            dx, dy = cx - ncx, cy - ncy
            new = [(x + dx, y + dy) for x, y in new]

            # Exocyclic: rotate each substituent with the local ring-edge frame.
            exo_moves: list[tuple[int, float, float]] = []
            for i, aid in enumerate(ring):
                atom = mol.GetAtomWithIdx(int(aid))
                o0x, o0y = old[i]
                n0x, n0y = new[i]
                o1x, o1y = old[(i + 1) % n]
                n1x, n1y = new[(i + 1) % n]
                a_old = math.atan2(o1y - o0y, o1x - o0x)
                a_new = math.atan2(n1y - n0y, n1x - n0x)
                drot = a_new - a_old
                ca, sa = math.cos(drot), math.sin(drot)
                for nb in atom.GetNeighbors():
                    nid = nb.GetIdx()
                    if nid in ring_set:
                        continue
                # Collect exo component via BFS excluding ring.
                stack = [nb.GetIdx() for nb in atom.GetNeighbors() if nb.GetIdx() not in ring_set]
                seen = set(stack)
                while stack:
                    u = stack.pop()
                    pu = conf.GetAtomPosition(int(u))
                    vx, vy = float(pu.x) - o0x, float(pu.y) - o0y
                    rx = vx * ca - vy * sa
                    ry = vx * sa + vy * ca
                    exo_moves.append((u, n0x + rx, n0y + ry))
                    for nb2 in mol.GetAtomWithIdx(int(u)).GetNeighbors():
                        v = nb2.GetIdx()
                        if v in ring_set or v in seen:
                            continue
                        seen.add(v)
                        stack.append(v)

            for i, aid in enumerate(ring):
                conf.SetAtomPosition(int(aid), Point3D(new[i][0], new[i][1], 0.0))
            for uid, x, y in exo_moves:
                conf.SetAtomPosition(int(uid), Point3D(x, y, 0.0))

        def _double_side_score(ring: tuple[int, ...]) -> float:
            """Higher when endocyclic doubles look cis-like (ring path on one side)."""
            n = len(ring)
            ring_set = set(ring)
            score = 0.0
            for i in range(n):
                a, b = int(ring[i]), int(ring[(i + 1) % n])
                bond = mol.GetBondBetweenAtoms(a, b)
                if bond is None or bond.GetBondType() != Chem.BondType.DOUBLE:
                    continue
                pa, pb = conf.GetAtomPosition(a), conf.GetAtomPosition(b)
                prev = conf.GetAtomPosition(int(ring[(i - 1) % n]))
                nxt = conf.GetAtomPosition(int(ring[(i + 2) % n]))
                # Both ring flanks should lie on the same side of the double for Z-like endocyclic.
                ax, ay = pb.x - pa.x, pb.y - pa.y
                c1 = ax * (prev.y - pa.y) - ay * (prev.x - pa.x)
                c2 = ax * (nxt.y - pb.y) - ay * (nxt.x - pb.x)
                if c1 * c2 > 0:
                    score += 1.0
                else:
                    score -= 1.0
                # Exocyclic substituents on opposite sides → E preference if present.
                for end, other in ((a, b), (b, a)):
                    atom = mol.GetAtomWithIdx(end)
                    for nb in atom.GetNeighbors():
                        if nb.GetIdx() in ring_set:
                            continue
                        p = conf.GetAtomPosition(nb.GetIdx())
                        pe = conf.GetAtomPosition(end)
                        _ = (pb.x - pa.x) * (p.y - pe.y) - (pb.y - pa.y) * (p.x - pe.x)
            return score

        for ring in atom_rings:
            n = len(ring)
            if n < 9:
                continue
            if any(membership[a] != 1 for a in ring):
                continue  # fused / bridged — leave to CoordGen + GR-3.3.3/4
            lens: list[float] = []
            for i in range(n):
                a, b = ring[i], ring[(i + 1) % n]
                pa, pb = conf.GetAtomPosition(int(a)), conf.GetAtomPosition(int(b))
                lens.append(math.hypot(pa.x - pb.x, pa.y - pb.y))
            bl = sorted(lens)[len(lens) // 2] if lens else 1.5
            offsets = large_ring_offsets_y_up(n, bond_length=bl)
            els = [mol.GetAtomWithIdx(int(a)).GetSymbol() for a in ring]
            offsets = rotate_offsets_to_inward_heteroatoms(offsets, els)
            has_dbl = False
            has_tpl = False
            for i in range(n):
                bond = mol.GetBondBetweenAtoms(int(ring[i]), int(ring[(i + 1) % n]))
                if bond is None:
                    continue
                bt = bond.GetBondType()
                if bt == Chem.BondType.DOUBLE:
                    has_dbl = True
                elif bt == Chem.BondType.TRIPLE:
                    has_tpl = True
            if has_tpl:
                # GR-3.3.2: triples must stay linear — leave CoordGen geometry.
                continue
            if has_dbl:
                snap = [
                    (int(a), float(conf.GetAtomPosition(int(a)).x), float(conf.GetAtomPosition(int(a)).y))
                    for a in range(mol.GetNumAtoms())
                ]

                def _restore() -> None:
                    for aid, x, y in snap:
                        conf.SetAtomPosition(aid, Point3D(x, y, 0.0))

                _apply_ring_offsets(ring, offsets, reflect=False)
                s0 = _double_side_score(ring)
                _restore()
                _apply_ring_offsets(ring, offsets, reflect=True)
                s1 = _double_side_score(ring)
                if s0 >= s1:
                    _restore()
                    _apply_ring_offsets(ring, offsets, reflect=False)
            else:
                _apply_ring_offsets(ring, offsets, reflect=False)

    def cleanup_layout_2d(self) -> bool:
        """Reposition all atoms using RDKit 2D coords with IUPAC-oriented post-pass."""
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
            stereo_tags = self._capture_tetrahedral_chiral_tags(mol)
            if not self._depict_mol_2d_iupac(mol, clear_conformers=True):
                return False
            conf = mol.GetConformer(0)
            self._restore_tetrahedral_chiral_tags(mol, stereo_tags)
            self._clear_bond_dirs(mol)
            try:
                AllChem.WedgeMolBonds(mol, conf)
            except Exception:
                pass

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
            if moves:
                for nid, _oldp, newp in moves:
                    node = next((x for x in self.nodes if x["id"] == nid), None)
                    if node:
                        node["pos"] = newp
                self._push_undo("move_nodes", moves)
            try:
                self._apply_hex_macrocycles_to_sketch()
            except Exception:
                pass
            try:
                self._reorient_sketch_iupac()
            except Exception:
                pass
            # Sync wedge/hash from re-wedged mol after layout.
            try:
                self._rewedge_sketch_from_mol(mol, {v: k for k, v in sk2rd.items()}, stereo_tags)
            except Exception:
                pass
            # Stereo tip / between-center sanitize after layout.
            self.bonds = sanitize_sketch_stereo_bonds(
                self.bonds, chiral_center_ids=getattr(self, "_chiral_center_ids", set()) or set()
            )
            self.ensure_sketch_fits_viewport(refresh=False)
            self._after_sketch_edit()
            return True
        except Exception:
            return False
