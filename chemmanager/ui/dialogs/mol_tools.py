from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from ...workers import ConformerGenParams, SuperposeParams
from ..strings import DISCONNECT_FRAGMENTS_HELP, TOOL_RGROUP_DECOMP
from .scope import selection_scope_checked


class DisconnectFragmentsDialog(QDialog):
    """Pick the structure field and whether Structure is cleared or a SMILES column is added."""

    def __init__(self, source_labels: list[str], existing_headers: list[str], selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Disconnect Largest Fragments")
        self.resize(500, 320)
        self._existing = list(existing_headers)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)
        root.addWidget(QLabel(DISCONNECT_FRAGMENTS_HELP))

        f = QFormLayout()
        self.src_combo = QComboBox()
        self.src_combo.addItems(source_labels)
        f.addRow("Structure field:", self.src_combo)
        root.addLayout(f)

        out = QGroupBox("Output")
        og = QVBoxLayout(out)
        self.radio_structure = QRadioButton(
            "Replace Structure column (refreshes the 2D image from the largest fragment; updates working molecule)"
        )
        self.radio_newcol = QRadioButton(
            "New column only (SMILES of largest fragment; Structure column unchanged)"
        )
        self.radio_structure.setChecked(True)
        og.addWidget(self.radio_structure)
        og.addWidget(self.radio_newcol)
        row = QHBoxLayout()
        row.addWidget(QLabel("New column name:"))
        self.name_edit = QLineEdit("Largest fragment SMILES")
        self.name_edit.setEnabled(False)
        row.addWidget(self.name_edit)
        og.addLayout(row)
        self.radio_newcol.toggled.connect(self._sync_name_enabled)
        root.addWidget(out)

        scope_box = QGroupBox("Scope")
        scope_lyt = QVBoxLayout(scope_box)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        scope_lyt.addWidget(self.only_selected_cb)
        root.addWidget(scope_box)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        self._sync_name_enabled()

    def _sync_name_enabled(self) -> None:
        self.name_edit.setEnabled(self.radio_newcol.isChecked())

    def _try_accept(self) -> None:
        if self.radio_newcol.isChecked():
            name = (self.name_edit.text() or "").strip()
            if not name:
                QMessageBox.warning(self, self.windowTitle(), "Enter a column name for the new SMILES column.")
                return
            if name in ("ID_HIDDEN", "Structure", "Fragments"):
                QMessageBox.warning(self, self.windowTitle(), f"The column name â€œ{name}â€ is reserved.")
                return
            if name in self._existing:
                QMessageBox.warning(
                    self,
                    self.windowTitle(),
                    f"A column named â€œ{name}â€ already exists. Choose another name, or use â€œReplace Structure columnâ€.",
                )
                return
        self.accept()

    def config(self) -> tuple[str, bool, str | None, bool]:
        """Returns ``(source_field, replace_structure_column, new_column_name_or_None, only_selected_rows)``."""
        src = self.src_combo.currentText()
        replace_structure = self.radio_structure.isChecked()
        new_col = None if replace_structure else (self.name_edit.text() or "").strip()
        only_sel = selection_scope_checked(self)
        return src, replace_structure, new_col, only_sel


class GenerateConformationsDialog(QDialog):
    """Configure ETKDG embedding, minimizer, energy window, and table scope."""

    def __init__(self, selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate Conformations")
        self.resize(480, 420)
        self._have_selection = selected_row_count > 0
        root = QVBoxLayout(self)
        intro = QLabel(
            "Each row's working molecule is copied, embedded with ETKDG, minimized (MMFF94 or UFF), "
            "then conformers outside the energy window are discarded.\n\n"
            "The \"confs\" column shows compact generation metadata; full 3D coordinates are kept in memory for "
            "responsiveness (and saved with sessions). When there are at least two conformers, rightâ€‘click that cell "
            "and choose \"View Conformersâ€¦\" to open the 3D viewer "
            "(switch between one-at-a-time and superpose-all inside the viewer)."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.num_confs_sb = QSpinBox()
        self.num_confs_sb.setRange(1, 500)
        self.num_confs_sb.setValue(25)
        self.num_confs_sb.setToolTip("Number of conformers to embed before minimization and pruning.")
        form.addRow("Conformers to generate:", self.num_confs_sb)

        self.energy_win_sb = QDoubleSpinBox()
        self.energy_win_sb.setRange(0.0, 200.0)
        self.energy_win_sb.setDecimals(2)
        self.energy_win_sb.setSingleStep(1.0)
        self.energy_win_sb.setValue(10.0)
        self.energy_win_sb.setSuffix(" kcal/mol")
        self.energy_win_sb.setSpecialValueText("0 = keep all (no window)")
        self.energy_win_sb.setToolTip(
            "Keep only conformers within this energy above the lowest-energy conformer. "
            "Set to 0 to skip energy pruning."
        )
        form.addRow("Energy window (Î”E):", self.energy_win_sb)

        self.ff_combo = QComboBox()
        self.ff_combo.addItems(["MMFF", "UFF"])
        self.ff_combo.setToolTip("MMFF94 when parameters exist; otherwise falls back to UFF automatically.")
        form.addRow("Minimize with:", self.ff_combo)

        self.seed_sb = QSpinBox()
        self.seed_sb.setRange(0, 2_147_483_647)
        self.seed_sb.setValue(0xC0FFEE)
        self.seed_sb.setToolTip("Random seed passed to the ETKDG embedder.")
        form.addRow("Random seed:", self.seed_sb)

        self.prune_rms_sb = QDoubleSpinBox()
        self.prune_rms_sb.setRange(-1.0, 3.0)
        self.prune_rms_sb.setDecimals(3)
        self.prune_rms_sb.setSingleStep(0.05)
        self.prune_rms_sb.setValue(-1.0)
        self.prune_rms_sb.setSpecialValueText("default (ETKDG)")
        self.prune_rms_sb.setToolTip("ETKDG pruneRmsThresh during embed; âˆ’1 uses the parameter object default.")
        form.addRow("Embed RMS prune:", self.prune_rms_sb)

        self.max_iters_sb = QSpinBox()
        self.max_iters_sb.setRange(20, 2000)
        self.max_iters_sb.setValue(200)
        self.max_iters_sb.setToolTip("Maximum minimizer iterations per conformer.")
        form.addRow("Max minimizer iterations:", self.max_iters_sb)

        root.addLayout(form)

        scope_box = QGroupBox("Scope")
        scope_lyt = QVBoxLayout(scope_box)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        scope_lyt.addWidget(self.only_selected_cb)
        root.addWidget(scope_box)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> ConformerGenParams:
        return ConformerGenParams(
            num_confs=int(self.num_confs_sb.value()),
            energy_window_kcal=float(self.energy_win_sb.value()),
            force_field=str(self.ff_combo.currentText()),
            random_seed=int(self.seed_sb.value()),
            prune_rms_threshold=float(self.prune_rms_sb.value()),
            max_iterations=int(self.max_iters_sb.value()),
        )


class SuperposeConformersDialog(QDialog):
    """Configure rigid superposition of conformers read from packed ``confs`` cells."""

    def __init__(self, selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Superpose Conformers")
        self.resize(520, 500)
        self._have_selection = selected_row_count > 0
        root = QVBoxLayout(self)
        intro = QLabel(
            'Reads each row\'s multi-conformer data from the "confs" column (from Generate Conformations), '
            "rigidly aligns every conformer to one reference conformer, then writes the aligned ensemble to a new "
            '"superpose" column (same format as "confs": compact cell text plus in-memory coordinates). '
            "Optionally restrict alignment to atoms matching a SMILES or SMARTS substructure."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.ref_sb = QSpinBox()
        self.ref_sb.setRange(0, 499)
        self.ref_sb.setValue(0)
        self.ref_sb.setToolTip(
            "0-based index into the conformer list for each row (sorted by RDKit conformer id). "
            "If a row has fewer conformers than this index, the last conformer is used as reference."
        )
        form.addRow("Reference conformer index:", self.ref_sb)

        self.heavy_cb = QCheckBox("Use heavy atoms only (exclude hydrogen)")
        self.heavy_cb.setChecked(True)
        self.heavy_cb.setToolTip(
            "Alignment minimizes RMS over non-hydrogen atoms only (recommended for noisy H positions)."
        )
        form.addRow(self.heavy_cb)

        self.reflect_cb = QCheckBox("Allow reflection (mirror image)")
        self.reflect_cb.setChecked(False)
        self.reflect_cb.setToolTip(
            "If checked, the alignment may invert chirality-related rigid transforms; leave off for typical conformers."
        )
        form.addRow(self.reflect_cb)

        self.max_align_sb = QSpinBox()
        self.max_align_sb.setRange(10, 500)
        self.max_align_sb.setValue(50)
        self.max_align_sb.setToolTip("Maximum iterations passed to the RDKit alignment optimizer per conformer pair.")
        form.addRow("Max alignment iterations:", self.max_align_sb)

        self.align_pat_edit = QLineEdit()
        self.align_pat_edit.setPlaceholderText("optional, e.g. CC or c1ccccc1 or [#6]-[#6]")
        self.align_pat_edit.setToolTip(
            "If set, rigid alignment uses only atoms that match this query on each conformer "
            "(same graph as the row molecule). Leave empty to align on all heavy atoms or all atoms."
        )
        form.addRow("Align on substructure (SMILES/SMARTS):", self.align_pat_edit)

        self.align_smarts_cb = QCheckBox("Pattern is SMARTS (unchecked = SMILES)")
        self.align_smarts_cb.setChecked(False)
        self.align_smarts_cb.setToolTip("Parse the field above with MolFromSmarts instead of MolFromSmiles.")
        form.addRow(self.align_smarts_cb)

        root.addLayout(form)

        scope_box = QGroupBox("Scope")
        scope_lyt = QVBoxLayout(scope_box)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        scope_lyt.addWidget(self.only_selected_cb)
        root.addWidget(scope_box)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> SuperposeParams:
        return SuperposeParams(
            reference_conformer_index=int(self.ref_sb.value()),
            heavy_atoms_only=bool(self.heavy_cb.isChecked()),
            reflect=bool(self.reflect_cb.isChecked()),
            max_align_iters=int(self.max_align_sb.value()),
            align_pattern=(self.align_pat_edit.text() or "").strip(),
            align_pattern_is_smarts=bool(self.align_smarts_cb.isChecked()),
        )


@dataclass(frozen=True)
class RGroupDecompDialogParams:
    """Arguments from :class:`RGroupDecompositionDialog` for the worker."""

    core_query: str
    structure_source: str
    column_prefix: str
    only_match_at_r_groups: bool
    remove_hydrogens_post_match: bool
    matching: str  # "greedy" or "exhaustive"


class RGroupDecompositionDialog(QDialog):
    """Core SMARTS/SMILES, structure column, RDKit R-group decomposition options."""

    def __init__(self, structure_sources: list[str], selected_row_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TOOL_RGROUP_DECOMP)
        self.setMinimumWidth(420)
        self.resize(480, 0)
        self._have_selection = selected_row_count > 0
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        intro = QLabel(
            "Provide a labeled core (dummy atoms [*], [1*], â€¦ for R-group attachment). "
            "Each table row is decomposed against that core; results are written as new columns "
            "(core scaffold and R-group SMILES per attachment)."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.core_edit = QLineEdit()
        self.core_edit.setPlaceholderText("e.g. c1ccc([*:1])cc1 or SMARTS with dummy labels")
        self.core_edit.setToolTip(
            "Parsed as SMARTS first, then as SMILES. Use RDKit-style dummy atoms on the core "
            "that map to substituents in the row molecules."
        )
        form.addRow("Core (SMARTS or SMILES):", self.core_edit)

        self.src_combo = QComboBox()
        self.src_combo.addItems(structure_sources)
        form.addRow("Molecules from:", self.src_combo)

        self.prefix_edit = QLineEdit()
        self.prefix_edit.setText("RGD")
        self.prefix_edit.setToolTip("New columns are named PREFIX_Core, PREFIX_R1, â€¦")
        form.addRow("Column name prefix:", self.prefix_edit)

        self.only_rg_cb = QCheckBox("Only match at R-groups (onlyMatchAtRGroups)")
        self.only_rg_cb.setChecked(True)
        form.addRow(self.only_rg_cb)

        self.remove_h_cb = QCheckBox("Remove hydrogens after match (removeHydrogensPostMatch)")
        self.remove_h_cb.setChecked(True)
        form.addRow(self.remove_h_cb)

        self.match_combo = QComboBox()
        self.match_combo.addItems(["Greedy", "Exhaustive"])
        self.match_combo.setToolTip("Greedy is faster; Exhaustive explores more matchings.")
        form.addRow("Matching strategy:", self.match_combo)

        root.addLayout(form)

        scope_box = QGroupBox("Scope")
        scope_lyt = QVBoxLayout(scope_box)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        scope_lyt.addWidget(self.only_selected_cb)
        root.addWidget(scope_box)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _on_accept(self) -> None:
        if not (self.core_edit.text() or "").strip():
            QMessageBox.warning(self, TOOL_RGROUP_DECOMP, "Enter a core SMARTS or SMILES.")
            return
        self.accept()

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> RGroupDecompDialogParams:
        strat = self.match_combo.currentText().strip().lower()
        return RGroupDecompDialogParams(
            core_query=(self.core_edit.text() or "").strip(),
            structure_source=self.src_combo.currentText(),
            column_prefix=(self.prefix_edit.text() or "").strip() or "RGD",
            only_match_at_r_groups=bool(self.only_rg_cb.isChecked()),
            remove_hydrogens_post_match=bool(self.remove_h_cb.isChecked()),
            matching="exhaustive" if strat.startswith("exhaustive") else "greedy",
        )
