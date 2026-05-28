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
from ..qt_widget_utils import make_window_minimizable
from ..strings import TOOL_CORE_DECOMP, TOOL_SINGLE_CONFORMATION
from ...fragment_decomposition import detect_fragment_column_prefixes
from .scope import selection_scope_checked


_RESERVED_DISCONNECT_COLUMNS = frozenset({"ID_HIDDEN"})


class DisconnectFragmentsDialog(QDialog):
    """Pick the target structure field; update it by default or write results to new columns."""

    def __init__(self, source_labels: list[str], existing_headers: list[str], selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Disconnect Largest Fragments")
        self.resize(480, 280)
        self._existing = list(existing_headers)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)

        f = QFormLayout()
        self.src_combo = QComboBox()
        self.src_combo.addItems(source_labels)
        f.addRow("Target column:", self.src_combo)
        root.addLayout(f)

        out = QGroupBox("Output")
        og = QVBoxLayout(out)
        self.radio_update_target = QRadioButton(
            "Update target column (refresh 2D image when applicable; write largest fragment there)"
        )
        self.radio_new_columns = QRadioButton(
            "New columns only (leave target column unchanged)"
        )
        self.radio_update_target.setChecked(True)
        og.addWidget(self.radio_update_target)
        og.addWidget(self.radio_new_columns)
        ncol = QFormLayout()
        self.largest_edit = QLineEdit("Largest fragment SMILES")
        ncol.addRow("Largest fragment column:", self.largest_edit)
        self.fragments_edit = QLineEdit("Fragments")
        ncol.addRow("Smaller fragments column:", self.fragments_edit)
        og.addLayout(ncol)
        self.radio_update_target.toggled.connect(self._sync_output_fields)
        self.radio_new_columns.toggled.connect(self._sync_output_fields)
        root.addWidget(out)

        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        root.addWidget(self.only_selected_cb)
        self.no_render_2d_cb = QCheckBox("No Render 2D")
        root.addWidget(self.no_render_2d_cb)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        self._sync_output_fields()
        make_window_minimizable(self)

    def _sync_output_fields(self) -> None:
        new_only = self.radio_new_columns.isChecked()
        self.largest_edit.setEnabled(new_only)
        self.fragments_edit.setEnabled(True)

    def _try_accept(self) -> None:
        fragments = (self.fragments_edit.text() or "").strip()
        if not fragments:
            QMessageBox.warning(self, self.windowTitle(), "Enter a column name for the smaller fragments.")
            return
        if self.radio_new_columns.isChecked():
            largest = (self.largest_edit.text() or "").strip()
            if not largest:
                QMessageBox.warning(self, self.windowTitle(), "Enter a column name for the largest fragment.")
                return
            if largest == fragments:
                QMessageBox.warning(
                    self,
                    self.windowTitle(),
                    "Largest and smaller fragment columns must have different names.",
                )
                return
            target = self.src_combo.currentText()
            if largest == target:
                QMessageBox.warning(
                    self,
                    self.windowTitle(),
                    "Largest fragment column must differ from the target column when using new columns only.",
                )
                return
            for label, name in (("Largest fragment", largest), ("Smaller fragments", fragments)):
                if name in _RESERVED_DISCONNECT_COLUMNS:
                    QMessageBox.warning(
                        self,
                        self.windowTitle(),
                        f"The {label} column name “{name}” is reserved.",
                    )
                    return
        elif fragments in _RESERVED_DISCONNECT_COLUMNS:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                f"The smaller fragments column name “{fragments}” is reserved.",
            )
            return
        self.accept()

    def config(self) -> tuple[str, bool, str | None, str, bool, bool]:
        """
        Returns ``(target_column, update_target, largest_column_or_None, smaller_fragments_column,
        only_selected_rows, no_render_2d)``.

        When *update_target* is true, the largest fragment is written to the target column and
        *largest_column_or_None* is ``None``. Otherwise results go to the named largest column.
        """
        src = self.src_combo.currentText()
        update_target = self.radio_update_target.isChecked()
        largest = None if update_target else (self.largest_edit.text() or "").strip()
        fragments = (self.fragments_edit.text() or "").strip()
        only_sel = selection_scope_checked(self)
        no_render = self.no_render_2d_cb.isChecked()
        return src, update_target, largest, fragments, only_sel, no_render


class FastPrepareDialog(QDialog):
    """Disconnect largest fragment, neutralize, then render 2D in one pipeline."""

    def __init__(self, source_labels: list[str], selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fast Prepare")
        self.resize(440, 200)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)
        hint = QLabel(
            "Updates the target column with the largest disconnected fragment, neutralizes it, "
            "then redraws 2D images. Smaller fragments are written to the Fragments column."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        root.addWidget(hint)

        f = QFormLayout()
        self.src_combo = QComboBox()
        self.src_combo.addItems(source_labels)
        f.addRow("Target column:", self.src_combo)
        self.fragments_edit = QLineEdit("Fragments")
        f.addRow("Smaller fragments column:", self.fragments_edit)
        root.addLayout(f)

        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        root.addWidget(self.only_selected_cb)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        make_window_minimizable(self)

    def _try_accept(self) -> None:
        fragments = (self.fragments_edit.text() or "").strip()
        if not fragments:
            QMessageBox.warning(self, self.windowTitle(), "Enter a column name for the smaller fragments.")
            return
        if fragments in _RESERVED_DISCONNECT_COLUMNS:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                f"The smaller fragments column name “{fragments}” is reserved.",
            )
            return
        self.accept()

    def config(self) -> tuple[str, str, bool]:
        """Returns ``(target_column, smaller_fragments_column, only_selected_rows)``."""
        return (
            self.src_combo.currentText(),
            (self.fragments_edit.text() or "").strip(),
            selection_scope_checked(self),
        )


class NeutralizeDialog(QDialog):
    """Neutralize structures in a chosen column (net formal charge → 0)."""

    def __init__(self, source_labels: list[str], selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Neutralize")
        self.resize(420, 140)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)
        f = QFormLayout()
        self.src_combo = QComboBox()
        self.src_combo.addItems(source_labels)
        f.addRow("Target column:", self.src_combo)
        root.addLayout(f)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        root.addWidget(self.only_selected_cb)
        self.no_render_2d_cb = QCheckBox("No Render 2D")
        self.no_render_2d_cb.setToolTip("Skip redrawing 2D images after neutralization.")
        root.addWidget(self.no_render_2d_cb)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        make_window_minimizable(self)

    def config(self) -> tuple[str, bool, bool]:
        """Returns ``(target_column, only_selected_rows, no_render_2d)``."""
        return (
            self.src_combo.currentText(),
            selection_scope_checked(self),
            self.no_render_2d_cb.isChecked(),
        )


class GenerateSingleConformationDialog(QDialog):
    """Embed one conformer per row, minimize, and store in the ``confs`` column."""

    def __init__(self, selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TOOL_SINGLE_CONFORMATION)
        self.resize(460, 320)
        self._have_selection = selected_row_count > 0
        root = QVBoxLayout(self)
        intro = QLabel(
            "For each row, embed a single 3D conformer with ETKDG, minimize with MMFF94 or UFF, "
            "and write the result to the \"confs\" column (one lowest-energy geometry per structure). "
            "Right-click the cell and choose \"View Conformers…\" to open the 3D viewer. "
            "The working Structure column is unchanged."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.ff_combo = QComboBox()
        self.ff_combo.addItems(["MMFF", "UFF"])
        self.ff_combo.setToolTip("MMFF94 when parameters exist; otherwise falls back to UFF automatically.")
        form.addRow("Minimize with:", self.ff_combo)

        self.seed_sb = QSpinBox()
        self.seed_sb.setRange(0, 2_147_483_647)
        self.seed_sb.setValue(0xC0FFEE)
        self.seed_sb.setToolTip("Random seed passed to the ETKDG embedder.")
        form.addRow("Random seed:", self.seed_sb)

        self.max_iters_sb = QSpinBox()
        self.max_iters_sb.setRange(20, 2000)
        self.max_iters_sb.setValue(200)
        self.max_iters_sb.setToolTip("Maximum minimizer iterations for the conformer.")
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
        make_window_minimizable(self)

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> ConformerGenParams:
        return ConformerGenParams.single_lowest_energy(
            force_field=str(self.ff_combo.currentText()),
            random_seed=int(self.seed_sb.value()),
            max_iterations=int(self.max_iters_sb.value()),
        )


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
        make_window_minimizable(self)

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
        make_window_minimizable(self)

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
class FragmentDecompDialogParams:
    """Arguments from :class:`FragmentDecompositionDialog` for the worker."""

    structure_source: str
    column_prefix: str
    method: str  # "brics" | "recap"
    tool_title: str
    render_2d: bool


class FragmentDecompositionDialog(QDialog):
    """Structure source, column prefix, and scope for BRICS or RECAP decomposition."""

    def __init__(
        self,
        *,
        window_title: str,
        intro: str,
        default_prefix: str,
        method: str,
        structure_sources: list[str],
        selected_row_count: int,
        parent=None,
    ):
        super().__init__(parent)
        self._method = method
        self._tool_title = window_title
        self.setWindowTitle(window_title)
        self.setMinimumWidth(420)
        self.resize(480, 0)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        intro_lbl = QLabel(intro)
        intro_lbl.setWordWrap(True)
        root.addWidget(intro_lbl)

        form = QFormLayout()
        self.src_combo = QComboBox()
        self.src_combo.addItems(structure_sources)
        form.addRow("Molecules from:", self.src_combo)

        self.prefix_edit = QLineEdit()
        self.prefix_edit.setText(default_prefix)
        self.prefix_edit.setToolTip("New columns are named PREFIX_1, PREFIX_2, … (one per fragment).")
        form.addRow("Column name prefix:", self.prefix_edit)
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

        self.render_2d_cb = QCheckBox("Render 2D after decomposition")
        self.render_2d_cb.setChecked(False)
        self.render_2d_cb.setToolTip(
            "Render the new fragment columns as 2D depictions (pixmap-only) after decomposition finishes."
        )
        root.addWidget(self.render_2d_cb)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        make_window_minimizable(self)

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> FragmentDecompDialogParams:
        return FragmentDecompDialogParams(
            structure_source=self.src_combo.currentText(),
            column_prefix=(self.prefix_edit.text() or "").strip(),
            method=self._method,
            tool_title=self._tool_title,
            render_2d=bool(self.render_2d_cb.isChecked()),
        )


@dataclass(frozen=True)
class FragmentRecompDialogParams:
    """Arguments from :class:`FragmentRecompositionDialog` for the worker."""

    column_prefix: str
    method: str  # "brics" | "recap"
    max_depth: int
    max_products: int
    tool_title: str


class FragmentRecompositionDialog(QDialog):
    """Pool fragment SMILES columns and run BRICS or RECAP recomposition."""

    def __init__(
        self,
        *,
        window_title: str,
        intro: str,
        default_prefix: str,
        method: str,
        table_headers: list[str],
        selected_row_count: int,
        parent=None,
    ):
        super().__init__(parent)
        self._method = method
        self._tool_title = window_title
        self._table_headers = list(table_headers)
        self.setWindowTitle(window_title)
        self.setMinimumWidth(420)
        self.resize(480, 0)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        intro_lbl = QLabel(intro)
        intro_lbl.setWordWrap(True)
        root.addWidget(intro_lbl)

        form = QFormLayout()
        self.prefix_combo = QComboBox()
        self.prefix_combo.setEditable(True)
        prefixes = detect_fragment_column_prefixes(self._table_headers)
        if default_prefix not in prefixes:
            prefixes = [default_prefix] + prefixes
        self.prefix_combo.addItems(prefixes)
        self.prefix_combo.setCurrentText(default_prefix)
        self.prefix_combo.setToolTip(
            "Use fragment columns from decomposition (e.g. BRICS_1, BRICS_2 or RECAP_1, …)."
        )
        form.addRow("Fragment column prefix:", self.prefix_combo)

        self.max_depth_sb = QSpinBox()
        self.max_depth_sb.setRange(1, 8)
        self.max_depth_sb.setValue(3)
        self.max_depth_sb.setToolTip("Maximum BRICS coupling depth when assembling products.")
        form.addRow("Max coupling depth:", self.max_depth_sb)

        self.max_products_sb = QSpinBox()
        self.max_products_sb.setRange(10, 50_000)
        self.max_products_sb.setValue(2000)
        self.max_products_sb.setToolTip("Stop after this many unique product SMILES are generated.")
        form.addRow("Max products:", self.max_products_sb)
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
        make_window_minimizable(self)

    def _on_accept(self) -> None:
        if not (self.prefix_combo.currentText() or "").strip():
            QMessageBox.warning(self, self.windowTitle(), "Enter a fragment column prefix.")
            return
        self.accept()

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> FragmentRecompDialogParams:
        return FragmentRecompDialogParams(
            column_prefix=(self.prefix_combo.currentText() or "").strip(),
            method=self._method,
            max_depth=int(self.max_depth_sb.value()),
            max_products=int(self.max_products_sb.value()),
            tool_title=self._tool_title,
        )


@dataclass(frozen=True)
class CoreBasedDecompDialogParams:
    """Arguments from :class:`CoreBasedDecompositionDialog` for the worker."""

    core_query: str
    structure_source: str
    column_prefix: str
    only_match_at_r_groups: bool
    remove_hydrogens_post_match: bool
    matching: str  # "greedy" or "exhaustive"


class CoreBasedDecompositionDialog(QDialog):
    """Core SMARTS/SMILES, structure column, RDKit core-based decomposition options."""

    def __init__(self, structure_sources: list[str], selected_row_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TOOL_CORE_DECOMP)
        self.setMinimumWidth(420)
        self.resize(480, 0)
        self._have_selection = selected_row_count > 0
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        intro = QLabel(
            "Provide a labeled core (dummy atoms [*], [1*], … for substituent attachment). "
            "Each table row is decomposed against that core; results are written as new columns "
            "(core scaffold and substituent SMILES per attachment)."
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
        make_window_minimizable(self)

    def _on_accept(self) -> None:
        if not (self.core_edit.text() or "").strip():
            QMessageBox.warning(self, TOOL_CORE_DECOMP, "Enter a core SMARTS or SMILES.")
            return
        self.accept()

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> CoreBasedDecompDialogParams:
        strat = self.match_combo.currentText().strip().lower()
        return CoreBasedDecompDialogParams(
            core_query=(self.core_edit.text() or "").strip(),
            structure_source=self.src_combo.currentText(),
            column_prefix=(self.prefix_edit.text() or "").strip() or "RGD",
            only_match_at_r_groups=bool(self.only_rg_cb.isChecked()),
            remove_hydrogens_post_match=bool(self.remove_h_cb.isChecked()),
            matching="exhaustive" if strat.startswith("exhaustive") else "greedy",
        )
