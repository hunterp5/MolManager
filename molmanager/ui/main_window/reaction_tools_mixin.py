"""Tools → Reaction Based Enumeration."""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox

from ..strings import TOOL_REACTION_ENUMERATION
from ...workers import ReactionEnumerationWorker

logger = logging.getLogger(__name__)


class ReactionToolsMixin:
    def open_reaction_enumeration(self) -> None:
        from ..dialogs import ReactionEnumerationDialog

        d = ReactionEnumerationDialog(parent=self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(lambda *_, dlg=d: self._on_reaction_enumeration_dialog_accepted(dlg))
        d.show()

    def _on_reaction_enumeration_dialog_accepted(self, d) -> None:
        p = d.params()
        from ...memory_guards import check_product_enumeration

        guard = check_product_enumeration(p.max_products)
        if not guard.ok:
            QMessageBox.warning(self, p.tool_title, guard.message)
            return
        ps = self._tool_progress_state
        self._begin_tool_progress(p.tool_title, p.max_products)
        self.process_queue.enqueue(
            f"{p.tool_title} ({p.reaction_name})",
            lambda ev, pp=p, sigs=self.signals, prog=ps: ReactionEnumerationWorker(
                pp.rxn_smarts,
                pp.reaction_name,
                pp.reactant_1_mode,
                pp.reactant_2_mode,
                pp.reactant_file_1,
                pp.reactant_file_2,
                pp.reactant_smiles_1,
                pp.reactant_smiles_2,
                pp.max_products,
                pp.output_filters,
                pp.add_to_table,
                pp.save_to_file,
                pp.save_path,
                pp.tool_title,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_reaction_enum_finished(self, result) -> None:
        self._finish_tool_progress(TOOL_REACTION_ENUMERATION)
        parts: list[str] = []
        if result.add_to_table and result.products:
            records = [
                (
                    str(smi),
                    {
                        "Reaction": str(result.reaction_name or ""),
                        "Enumeration_Method": "Reaction",
                    },
                )
                for smi in result.products
                if (smi or "").strip()
            ]
            n = self.add_rows_from_external_records_batch(records, render_structures=True)
            parts.append(f"added {n:,} row(s) to table")
        if result.save_to_file and result.save_path:
            parts.append(f"wrote {int(result.written_count):,} structure(s) to {result.save_path}")
        suffix = ""
        if int(result.skipped) > 0:
            suffix = f" ({int(result.skipped):,} outcome(s) skipped by constraints or duplicates)"
        if getattr(self, "_partial_results_notice", None):
            return
        if parts:
            self.status_label.setText(f"{TOOL_REACTION_ENUMERATION}: {', '.join(parts)}{suffix}.")
        elif not result.products:
            self.status_label.setText(f"{TOOL_REACTION_ENUMERATION}: no products generated{suffix}.")

    def on_reaction_enum_failed(self, message: str, tool_title: str) -> None:
        if message == "Cancelled.":
            self._finish_tool_progress(tool_title)
            self.status_label.setText(self._consume_partial_results_notice() or "Cancelled.")
            return
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, tool_title, message or "Reaction enumeration failed.")
