"""Export table + molecules to disk."""

import csv
import logging
import threading

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from ..utils import mol_to_canonical_smiles
from .signals import WorkerSignals

logger = logging.getLogger(__name__)

class ExportWorker(QRunnable):
    def __init__(
        self,
        path,
        ext,
        mols_dict,
        headers_to_export,
        table_data,
        signals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.path, self.ext, self.mols, self.headers, self.table_data, self.signals = (
            path,
            ext,
            mols_dict,
            headers_to_export,
            table_data,
            signals,
        )
        self.cancel_event = cancel_event

    def run(self):
        user_cancelled = False
        try:
            skip = ["ID_HIDDEN", "Structure"]
            clean_headers = [h for h in self.headers if h not in skip]
            mols_items = list(self.mols.items())
            tot = max(len(mols_items), 1)
            if self.ext == ".csv":
                csv_heads = clean_headers.copy()
                if "SMILES" not in csv_heads:
                    csv_heads.insert(0, "SMILES")
                with open(self.path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=csv_heads)
                    writer.writeheader()
                    for done, (oid, mol) in enumerate(mols_items, start=1):
                        if self.cancel_event is not None and self.cancel_event.is_set():
                            user_cancelled = True
                            break
                        row = self.table_data.get(oid, {}).copy()
                        if "SMILES" not in row or not row["SMILES"]:
                            row["SMILES"] = mol_to_canonical_smiles(mol)
                        writer.writerow({k: v for k, v in row.items() if k in csv_heads})
                        try:
                            self.signals.tool_progress.emit("Exporting…", done, tot)
                        except Exception:
                            pass
            else:
                if self.ext in [".sdf", ".mol"]:
                    writer = Chem.SDWriter(self.path)
                elif self.ext == ".smi":
                    writer = Chem.SmilesWriter(self.path)
                elif self.ext == ".tdt":
                    writer = Chem.TDTWriter(self.path)
                elif self.ext == ".pdb":
                    writer = Chem.PDBWriter(self.path)
                for done, (oid, mol) in enumerate(mols_items, start=1):
                    if self.cancel_event is not None and self.cancel_event.is_set():
                        user_cancelled = True
                        break
                    row = self.table_data.get(oid, {})
                    for h in clean_headers:
                        mol.SetProp(h, str(row.get(h, "")))
                    writer.write(mol)
                    try:
                        self.signals.tool_progress.emit("Exporting…", done, tot)
                    except Exception:
                        pass
                writer.close()
            if user_cancelled:
                self.signals.export_finished.emit("Export cancelled (partial file may exist).")
            else:
                self.signals.export_finished.emit(f"Exported successfully to {self.path}")
        except Exception as e:
            logger.exception("ExportWorker failed for %s", self.path)
            self.signals.export_finished.emit(f"Export Error: {str(e)}")

