"""BRICS/RECAP/R-group fragment tools."""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMessageBox,
)

from rdkit import Chem

from ..strings import (
    TOOL_BRICS_DECOMP,
    TOOL_BRICS_RECOMP,
    TOOL_CORE_DECOMP,
    TOOL_RECAP_DECOMP,
    TOOL_RECAP_RECOMP,
)
from ...workers import (
    FragmentDecompositionWorker,
    FragmentRecompositionWorker,
    RGroupDecompositionWorker,
)
from ..compound_table_model import STRUCTURE_DEPICT_HEIGHT, STRUCTURE_DEPICT_WIDTH

logger = logging.getLogger(__name__)

class FragmentToolsMixin:
    def open_core_based_decomposition(self) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                TOOL_CORE_DECOMP,
                "Load a table with at least one row first.",
            )
            return
        candidates = self.chemistry_tool_structure_sources()
        from ..dialogs import CoreBasedDecompositionDialog

        d = CoreBasedDecompositionDialog(candidates, len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(lambda *_, dlg=d: self._on_core_based_decomposition_dialog_accepted(dlg))
        d.show()

    def _on_core_based_decomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, TOOL_CORE_DECOMP):
            return
        src = p.structure_source
        col = None if src == "Structure" else self.headers.index(src)
        data: list[tuple[int, Chem.Mol]] = []
        for r in range(self._table_model.rowCount()):
            t0 = self._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if allowed is not None and oid not in allowed:
                continue
            if src == "Structure":
                mol = self.mols.get(oid)
                if mol is None:
                    mol = self._mol_for_structure_row(r)
            else:
                if self._table_model.is_pixmap_data_column(src):
                    mol = self.mols.get(oid)
                    if mol is None:
                        raw = self._table_model.backing_value_for_row_header(r, src)
                        mol = self._mol_from_structure_text(raw) if raw else None
                    if mol is None:
                        mol = self._mol_for_structure_row(r)
                else:
                    raw = self._table_cell_text(r, col)
                    mol = self._mol_from_structure_text(raw)
                if mol is not None:
                    self.mols[oid] = mol
            if mol is not None:
                data.append((oid, mol))
        if not data:
            QMessageBox.information(
                self,
                TOOL_CORE_DECOMP,
                "No valid structures were found for the selected source and scope.",
            )
            self.status_label.setText("Ready.")
            return
        ps = self._tool_progress_state
        self._begin_tool_progress("Core-based decomposition", len(data))
        self.process_queue.enqueue(
            f"Core-based decomposition ({len(data)} rows)",
            lambda ev, dt=data, pp=p, sigs=self.signals, prog=ps: RGroupDecompositionWorker(
                dt,
                pp.core_query,
                pp.column_prefix,
                pp.only_match_at_r_groups,
                pp.remove_hydrogens_post_match,
                pp.matching,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_rgroup_decomp_finished(self, res, col_headers: list) -> None:
        self.on_calc_finished(res, col_headers, progress_label=TOOL_CORE_DECOMP)

    def on_rgroup_decomp_failed(self, message: str) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, TOOL_CORE_DECOMP, message or "Core-based decomposition failed.")

    def open_brics_decomposition(self) -> None:
        self._open_fragment_decomposition_dialog(
            method="brics",
            window_title=TOOL_BRICS_DECOMP,
            default_prefix="BRICS",
            intro=(
                "Break each structure into BRICS fragments (retrosynthetic building blocks). "
                "Fragments are written as SMILES in new columns (BRICS_1, BRICS_2, …). "
                "Dummy atoms in SMILES mark connection points."
            ),
        )

    def open_recap_decomposition(self) -> None:
        self._open_fragment_decomposition_dialog(
            method="recap",
            window_title=TOOL_RECAP_DECOMP,
            default_prefix="RECAP",
            intro=(
                "Break each structure into RECAP fragments (retrosynthetic hierarchies). "
                "Leaf fragments are written as SMILES in new columns (RECAP_1, RECAP_2, …)."
            ),
        )

    def _open_fragment_decomposition_dialog(
        self,
        *,
        method: str,
        window_title: str,
        default_prefix: str,
        intro: str,
    ) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                window_title,
                "Load a table with at least one row first.",
            )
            return
        from ..dialogs import FragmentDecompositionDialog

        d = FragmentDecompositionDialog(
            window_title=window_title,
            intro=intro,
            default_prefix=default_prefix,
            method=method,
            structure_sources=self.chemistry_tool_structure_sources(),
            selected_row_count=len(self._selected_logical_rows()),
            parent=self,
        )
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(
            lambda *_, dlg=d: self._on_fragment_decomposition_dialog_accepted(dlg)
        )
        d.show()

    def _on_fragment_decomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        if self._abort_if_only_selected_but_empty(only_selected, self._selected_oids_set(), p.tool_title):
            return
        data = self.collect_scoped_table_mols(p.structure_source, only_selected=only_selected)
        if not data:
            QMessageBox.information(
                self,
                p.tool_title,
                "No valid structures were found for the selected source and scope.",
            )
            self.status_label.setText("Ready.")
            return
        prefix = p.column_prefix or ("BRICS" if p.method == "brics" else "RECAP")
        method = "brics" if p.method == "brics" else "recap"
        self._fragment_decomp_render_2d_after = bool(getattr(p, "render_2d", False))
        ps = self._tool_progress_state
        self._begin_tool_progress(p.tool_title, len(data))
        self.process_queue.enqueue(
            f"{p.tool_title} ({len(data)} rows)",
            lambda ev, dt=data, m=method, pref=prefix, title=p.tool_title, sigs=self.signals, prog=ps: FragmentDecompositionWorker(
                dt,
                m,
                pref,
                title,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_fragment_decomp_finished(self, res, col_headers: list, tool_title: str) -> None:
        self._finish_tool_progress(tool_title)
        self.on_calc_finished(res, col_headers, finish_progress=False)
        do_render = bool(getattr(self, "_fragment_decomp_render_2d_after", False))
        self._fragment_decomp_render_2d_after = False
        # Optionally render new fragment SMILES columns so the user can see the pieces immediately.
        # Uses pixmap-only columns (hide SMILES text) and queues the renders so the GUI stays responsive.
        if do_render and tool_title in (TOOL_BRICS_DECOMP, TOOL_RECAP_DECOMP) and col_headers:
            try:
                base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
                for h in col_headers:
                    if h not in self.headers:
                        continue
                    renders, row_by_oid = self._build_render2d_tasks_in_table_order(
                        h, base_w, base_h, None
                    )
                    if renders:
                        self._start_render_2d_batch(
                            renders,
                            row_by_oid,
                            h,
                            column_pixmap_mode=True,
                            queue_title_prefix=f"{tool_title}: ",
                        )
            except Exception:
                logger.exception("Fragment decomposition: auto 2D render failed")

    def on_fragment_decomp_failed(self, message: str, tool_title: str) -> None:
        self._clear_tool_progress()
        self._fragment_decomp_render_2d_after = False
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, tool_title, message or "Fragment decomposition failed.")

    def open_brics_recomposition(self) -> None:
        self._open_fragment_recomposition_dialog(
            method="brics",
            window_title=TOOL_BRICS_RECOMP,
            default_prefix="BRICS",
            intro=(
                "Combine unique BRICS fragments from decomposition columns (e.g. BRICS_1, BRICS_2) "
                "into new product structures using RDKit BRICSBuild. Products are appended as new "
                "table rows with SMILES and 2D structures."
            ),
        )

    def open_recap_recomposition(self) -> None:
        self._open_fragment_recomposition_dialog(
            method="recap",
            window_title=TOOL_RECAP_RECOMP,
            default_prefix="RECAP",
            intro=(
                "Combine unique RECAP fragments from decomposition columns (e.g. RECAP_1, RECAP_2) "
                "into new product structures. RECAP attachment points (*) are coupled via the "
                "BRICS builder; products are appended as new rows with SMILES and 2D structures."
            ),
        )

    def _open_fragment_recomposition_dialog(
        self,
        *,
        method: str,
        window_title: str,
        default_prefix: str,
        intro: str,
    ) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                window_title,
                "Load a table with fragment columns from decomposition first.",
            )
            return
        from ..dialogs import FragmentRecompositionDialog

        d = FragmentRecompositionDialog(
            window_title=window_title,
            intro=intro,
            default_prefix=default_prefix,
            method=method,
            table_headers=list(self.headers),
            selected_row_count=len(self._selected_logical_rows()),
            parent=self,
        )
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(
            lambda *_, dlg=d: self._on_fragment_recomposition_dialog_accepted(dlg)
        )
        d.show()

    def _collect_fragment_smiles_for_prefix(
        self,
        prefix: str,
        *,
        only_selected: bool,
    ) -> list[str]:
        from ...fragment_decomposition import fragment_columns_for_prefix

        cols = fragment_columns_for_prefix(self.headers, prefix)
        if not cols:
            return []
        allowed = self._selected_oids_set() if only_selected else None
        col_idx = {h: self.headers.index(h) for h in cols}
        out: list[str] = []
        for r in range(self._table_model.rowCount()):
            t0 = self._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if allowed is not None and oid not in allowed:
                continue
            for h in cols:
                raw = self._table_cell_text(r, col_idx[h])
                if not raw:
                    raw = self._table_model.backing_value_for_row_header(r, h) or ""
                if raw:
                    out.append(str(raw).strip())
        return out

    def _on_fragment_recomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        if self._abort_if_only_selected_but_empty(
            only_selected, self._selected_oids_set(), p.tool_title
        ):
            return
        fragments = self._collect_fragment_smiles_for_prefix(
            p.column_prefix, only_selected=only_selected
        )
        if not fragments:
            QMessageBox.information(
                self,
                p.tool_title,
                f"No fragment SMILES found in columns matching “{p.column_prefix}_1”, “{p.column_prefix}_2”, …",
            )
            self.status_label.setText("Ready.")
            return
        method = "brics" if p.method == "brics" else "recap"
        ps = self._tool_progress_state
        self._begin_tool_progress(p.tool_title, 1)
        self.process_queue.enqueue(
            f"{p.tool_title} ({len(fragments)} fragments)",
            lambda ev, fr=fragments, pp=p, m=method, sigs=self.signals, prog=ps: FragmentRecompositionWorker(
                fr,
                m,
                pp.max_depth,
                pp.max_products,
                pp.tool_title,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_fragment_recomp_finished(self, products: list, tool_title: str) -> None:
        self._finish_tool_progress(tool_title)
        method_label = "BRICS" if "BRICS" in tool_title.upper() else "RECAP"
        records = [
            (str(smi), {"Recompose_Method": method_label}) for smi in products if (smi or "").strip()
        ]
        n = self.add_rows_from_external_records_batch(records, render_structures=True)
        self.status_label.setText(
            self._consume_partial_results_notice()
            or f"{tool_title}: added {n:,} product row(s)."
        )

    def on_fragment_recomp_failed(self, message: str, tool_title: str) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, tool_title, message or "Fragment recomposition failed.")
