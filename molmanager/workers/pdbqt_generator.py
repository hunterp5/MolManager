"""Generate ligand and receptor PDBQT files for docking (Meeko)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem


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


def _write_temp_sdf(mols: list[Chem.Mol], path: Path) -> None:
    w = Chem.SDWriter(str(path))
    try:
        for m in mols:
            if m is not None:
                w.write(m)
    finally:
        w.close()


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

            lig_prefix = _meeko_cli_or_module_argv(
                "meeko.cli.mk_prepare_ligand", "mk_prepare_ligand"
            )
            rec_prefix = _meeko_cli_or_module_argv(
                "meeko.cli.mk_prepare_receptor", "mk_prepare_receptor"
            )
            if not lig_prefix or not rec_prefix:
                self.signals.failed.emit(
                    "Could not find Meeko CLI entrypoints or module runner. "
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
                ligand_sdf: Path | None = None
                tmp_dir_cm = tempfile.TemporaryDirectory(prefix="molmanager_pdbqt_")
                try:
                    tmp_dir = Path(tmp_dir_cm.name)
                    if self.req.ligand_mode == "sdf":
                        if not self.req.ligand_sdf_path:
                            self.signals.failed.emit("Select an SDF file for ligand input.")
                            return
                        ligand_sdf = Path(self.req.ligand_sdf_path).expanduser()
                    elif self.req.ligand_mode == "smiles":
                        smis = [s.strip() for s in (self.req.ligand_smiles or []) if s.strip()]
                        if not smis:
                            self.signals.failed.emit("Enter at least one SMILES for ligand input.")
                            return
                        mols: list[Chem.Mol] = []
                        for smi in smis:
                            m = Chem.MolFromSmiles(smi)
                            if m is not None:
                                mols.append(m)
                        if not mols:
                            self.signals.failed.emit("Could not parse any provided SMILES.")
                            return
                        ligand_sdf = tmp_dir / "ligands.sdf"
                        _write_temp_sdf(mols, ligand_sdf)
                    else:
                        rows = list(self.req.ligand_rows or [])
                        if not rows:
                            self.signals.failed.emit("No ligand rows were provided.")
                            return
                        mols = [m for _oid, m in rows if m is not None]
                        if not mols:
                            self.signals.failed.emit("No valid ligands in selected rows.")
                            return
                        ligand_sdf = tmp_dir / "ligands.sdf"
                        _write_temp_sdf(mols, ligand_sdf)

                    argv = [
                        *lig_prefix,
                        "-i",
                        str(ligand_sdf),
                        "-o",
                        str(lig_out),
                    ]
                    code, out = _run_cmd(argv, cwd=cwd, cancel_event=cancel_ev)
                    if cancel_ev is not None and cancel_ev.is_set():
                        self.signals.failed.emit("Cancelled.")
                        return
                    if code != 0:
                        self.signals.failed.emit(f"Ligand PDBQT generation failed:\n{out.strip()}")
                        return
                    ligand_out = str(lig_out)
                finally:
                    tmp_dir_cm.cleanup()

            self.signals.finished.emit(receptor_out, ligand_out)
        except Exception as e:
            self.signals.failed.emit(str(e) or "PDBQT generation failed.")

