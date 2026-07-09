"""Generate ligand and receptor PDBQT files for docking (Meeko)."""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem
from rdkit.Chem import AllChem


@dataclass(frozen=True)
class PdbqtGenRequest:
    receptor_pdb_path: str | None
    receptor_pdbqt_out: str | None
    ligand_mode: str  # "sdf" | "smiles" | "rows"
    ligand_sdf_path: str | None
    ligand_smiles: list[str] | None
    ligand_rows: list[tuple[int, Chem.Mol]] | None
    ligand_pdbqt_out: str | None
    working_dir: str | None = None


class PdbqtGenSignals(QObject):
    finished = pyqtSignal(str, str)  # receptor_pdbqt_path, ligand_pdbqt_path (empty if skipped)
    failed = pyqtSignal(str)


def _which_or_empty(name: str) -> str:
    return shutil.which(name) or ""

def _meeko_cli_or_module_argv(module_name: str, script_base: str) -> list[str] | None:
    """
    Return argv prefix to run Meeko CLI either via PATH script or python -m module.

    On Windows, entrypoints are often installed as ``mk_prepare_ligand.exe`` (no .py).
    """
    for cand in (script_base, f"{script_base}.exe", f"{script_base}.py"):
        p = _which_or_empty(cand)
        if p:
            return [p]
    try:
        import sys

        return [sys.executable, "-m", module_name]
    except Exception:
        return None


def _read_sdf_molecules(path: Path) -> list[Chem.Mol]:
    suppl = Chem.SDMolSupplier(str(path), removeHs=False)
    return [m for m in suppl if m is not None]


def _embed_ligand_3d(mol: Chem.Mol) -> bool:
    params = None
    for name in ("ETKDGv3", "ETKDGv2", "ETKDG"):
        factory = getattr(AllChem, name, None)
        if factory is None:
            continue
        try:
            params = factory()
            break
        except Exception:
            continue
    if params is None:
        return False
    try:
        cid = AllChem.EmbedMolecule(mol, params)
    except Exception:
        cid = -1
    if cid != 0:
        try:
            cid = AllChem.EmbedMolecule(mol, randomSeed=0xC0FFEE)
        except Exception:
            cid = -1
    if cid != 0:
        return False
    try:
        AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
    except Exception:
        try:
            AllChem.UFFOptimizeMolecule(mol, maxIters=200)
        except Exception:
            pass
    return True


def prepare_ligand_with_hydrogens(mol: Chem.Mol) -> Chem.Mol | None:
    """
    Add explicit hydrogens to *mol* before Meeko PDBQT conversion.

    Uses existing 3D coordinates when present; otherwise embeds with ETKDG after AddHs.
    """
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    try:
        m = Chem.Mol(mol)
        Chem.SanitizeMol(m)
    except Exception:
        return None
    try:
        if m.GetNumConformers() > 0:
            m = Chem.AddHs(m, addCoords=True)
        else:
            m = Chem.AddHs(m)
            if not _embed_ligand_3d(m):
                return None
    except Exception:
        return None
    return m


def _prepare_ligand_mols(mols: list[Chem.Mol]) -> tuple[list[Chem.Mol], int]:
    """Return ligands with explicit H atoms; second value is the failure count."""
    prepared: list[Chem.Mol] = []
    failed = 0
    for mol in mols:
        out = prepare_ligand_with_hydrogens(mol)
        if out is None:
            failed += 1
        else:
            prepared.append(out)
    return prepared, failed


def _ligand_mols_from_request(req: PdbqtGenRequest) -> tuple[list[Chem.Mol] | None, str]:
    """Load ligand molecules from the request before hydrogen placement."""
    if req.ligand_mode == "sdf":
        if not req.ligand_sdf_path:
            return None, "Select an SDF file for ligand input."
        path = Path(req.ligand_sdf_path).expanduser()
        mols = _read_sdf_molecules(path)
        if not mols:
            return None, "Could not read any molecules from the ligand SDF file."
        return mols, ""
    if req.ligand_mode == "smiles":
        smis = [s.strip() for s in (req.ligand_smiles or []) if s.strip()]
        if not smis:
            return None, "Enter at least one SMILES for ligand input."
        mols = []
        for smi in smis:
            m = Chem.MolFromSmiles(smi)
            if m is not None:
                mols.append(m)
        if not mols:
            return None, "Could not parse any provided SMILES."
        return mols, ""
    rows = list(req.ligand_rows or [])
    if not rows:
        return None, "No ligand rows were provided."
    mols = [m for _oid, m in rows if m is not None]
    if not mols:
        return None, "No valid ligands in selected rows."
    return mols, ""


def _apply_meeko_rdkit_compat() -> None:
    """
    Meeko 0.7.x still calls ``mol.HasQuery()``; RDKit 2023.09+ exposes ``HasQuery`` on atoms/bonds only.
    """
    if getattr(Chem.Mol, "_molmanager_hasquery_patched", False):
        return
    if hasattr(Chem.Mol, "HasQuery"):
        Chem.Mol._molmanager_hasquery_patched = True  # type: ignore[attr-defined]
        return

    def _mol_has_query(self: Chem.Mol) -> bool:
        return any(atom.HasQuery() for atom in self.GetAtoms()) or any(
            bond.HasQuery() for bond in self.GetBonds()
        )

    Chem.Mol.HasQuery = _mol_has_query  # type: ignore[method-assign, attr-defined]
    Chem.Mol._molmanager_hasquery_patched = True  # type: ignore[attr-defined]


def _ligand_mol_for_meeko(mol: Chem.Mol) -> Chem.Mol:
    """Return a copy with a single 3D conformer for Meeko preparation."""
    m = Chem.Mol(mol)
    if m.GetNumConformers() > 1:
        conf = Chem.Conformer(m.GetConformer(0))
        m.RemoveAllConformers()
        m.AddConformer(conf, assignId=True)
    return m


def _write_ligand_pdbqt_file(mols: list[Chem.Mol], out_path: Path) -> str | None:
    """
    Prepare ligands with Meeko and write PDBQT to *out_path*.

    Returns an error message on failure, or ``None`` on success.
    """
    _apply_meeko_rdkit_compat()
    from meeko import MoleculePreparation, PDBQTWriterLegacy

    preparator = MoleculePreparation()
    written = 0
    errors: list[str] = []
    with out_path.open("w", encoding="utf-8") as fh:
        for index, mol in enumerate(mols, start=1):
            lig = _ligand_mol_for_meeko(mol)
            if not lig.HasProp("_Name"):
                lig.SetProp("_Name", f"ligand_{index}")
            try:
                molsetups = preparator.prepare(lig)
            except Exception as exc:
                errors.append(f"Molecule {index}: {exc}")
                continue
            for molsetup in molsetups:
                pdbqt_string, success, error_msg = PDBQTWriterLegacy.write_string(molsetup)
                if not success:
                    errors.append(f"Molecule {index}: {error_msg or 'PDBQT write failed.'}")
                    continue
                fh.write(pdbqt_string)
                if not pdbqt_string.endswith("\n"):
                    fh.write("\n")
                written += 1
    if written == 0:
        return "\n".join(errors) if errors else "No PDBQT models were generated."
    return None


def _run_cmd(argv: list[str], *, cwd: str | None, cancel_event: threading.Event | None) -> tuple[int, str]:
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Cancelled.")
    try:
        p = subprocess.run(
            argv,
            cwd=cwd or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return int(p.returncode), (p.stdout or "")
    except FileNotFoundError:
        return 127, f"Command not found: {argv[0]}"


class PdbqtGeneratorWorker(QRunnable):
    """Generate .pdbqt files using Meeko CLI scripts."""

    def __init__(
        self,
        req: PdbqtGenRequest,
        *,
        signals: PdbqtGenSignals,
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.req = req
        self.signals = signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        try:
            cancel_ev = self.cancel_event
            meeko_ok = True
            try:
                import meeko  # noqa: F401
            except Exception:
                meeko_ok = False
            if not meeko_ok:
                self.signals.failed.emit(
                    "Meeko is required to generate PDBQT. Install with: pip install meeko"
                )
                return

            rec_prefix = _meeko_cli_or_module_argv(
                "meeko.cli.mk_prepare_receptor", "mk_prepare_receptor"
            )
            if self.req.receptor_pdbqt_out and not rec_prefix:
                self.signals.failed.emit(
                    "Could not find Meeko receptor CLI entrypoints or module runner. "
                    "Try: pip install --upgrade meeko"
                )
                return

            cwd = (self.req.working_dir or "").strip() or None
            receptor_out = ""
            ligand_out = ""

            # Receptor
            if self.req.receptor_pdb_path and self.req.receptor_pdbqt_out:
                rec_in = Path(self.req.receptor_pdb_path).expanduser()
                rec_out = Path(self.req.receptor_pdbqt_out).expanduser()
                rec_out.parent.mkdir(parents=True, exist_ok=True)
                argv = [
                    *rec_prefix,
                    "--read_pdb",
                    str(rec_in),
                    "--write_pdbqt",
                    str(rec_out),
                ]
                code, out = _run_cmd(argv, cwd=cwd, cancel_event=cancel_ev)
                if cancel_ev is not None and cancel_ev.is_set():
                    self.signals.failed.emit("Cancelled.")
                    return
                if code != 0:
                    self.signals.failed.emit(f"Receptor PDBQT generation failed:\n{out.strip()}")
                    return
                receptor_out = str(rec_out)

            # Ligand
            if self.req.ligand_pdbqt_out:
                lig_out = Path(self.req.ligand_pdbqt_out).expanduser()
                lig_out.parent.mkdir(parents=True, exist_ok=True)
                source_mols, err = _ligand_mols_from_request(self.req)
                if source_mols is None:
                    self.signals.failed.emit(err)
                    return
                prepared_mols, n_failed = _prepare_ligand_mols(source_mols)
                if not prepared_mols:
                    self.signals.failed.emit(
                        "Could not add explicit hydrogens to any ligand structures."
                    )
                    return
                if n_failed:
                    self.signals.failed.emit(
                        f"Could not add explicit hydrogens to {n_failed} ligand structure(s)."
                    )
                    return
                if cancel_ev is not None and cancel_ev.is_set():
                    self.signals.failed.emit("Cancelled.")
                    return
                err = _write_ligand_pdbqt_file(prepared_mols, lig_out)
                if err:
                    self.signals.failed.emit(f"Ligand PDBQT generation failed:\n{err}")
                    return
                ligand_out = str(lig_out)

            self.signals.finished.emit(receptor_out, ligand_out)
        except Exception as e:
            self.signals.failed.emit(str(e) or "PDBQT generation failed.")

