"""Qt signal objects shared by background workers."""

from PyQt5.QtCore import QObject, pyqtSignal

def emit_partial_results_if_cancelled(
    signals: "WorkerSignals",
    tool_label: str,
    done: int,
    total: int,
    cancelled: bool,
) -> None:
    """Emit ``partial_results`` before the finish signal so the UI can show a cancel notice."""
    if not cancelled:
        return
    try:
        signals.partial_results.emit(tool_label, int(done), max(1, int(total)))
    except Exception:
        pass


class WorkerSignals(QObject):
    """Signals from background workers to the main window (single QObject for simple wiring)."""

    # --- Load + 2D render pipeline ---
    # mols_loaded: (list_of_mols, headers_or_empty, is_first_batch:bool, is_last_batch:bool)
    mols_loaded = pyqtSignal(list, list, bool, bool)
    # Emitted on the UI thread before bulk read when multiple structure columns exist; worker waits.
    structure_source_probe = pyqtSignal(list)
    # Last int: render_batch_session (0 = single / non-batch; non-zero must match app accept id)
    rendered = pyqtSignal(int, dict, bytes, bool, int, int, int)

    # --- Batch chemistry / tools (wash, descriptors, conformers, custom calc, export) ---
    washed = pyqtSignal(list)
    neutralized = pyqtSignal(list)
    calculated = pyqtSignal(list, list)
    # list of (oid, mol_or_None, confs_cell_json_str)
    conformers_finished = pyqtSignal(list)
    # list of (oid, mol_or_None, superpose_cell_str) — same packed format as ``confs`` when successful
    superpose_finished = pyqtSignal(list)
    custom_calc = pyqtSignal(list)
    export_finished = pyqtSignal(str)
    # Core-based decomposition: list of (oid, {header: value}), then ordered new column names
    rgroup_decomp_finished = pyqtSignal(list, list)
    rgroup_decomp_failed = pyqtSignal(str)
    # BRICS / RECAP recomposition: product SMILES list, tool_title
    fragment_recomp_finished = pyqtSignal(list, str)
    fragment_recomp_failed = pyqtSignal(str, str)
    # BRICS / RECAP: (rows, headers, tool_title)
    fragment_decomp_finished = pyqtSignal(list, list, str)
    fragment_decomp_failed = pyqtSignal(str, str)
    cluster_failed = pyqtSignal(str)
    # Exploratory clustering: list of dict rows (method, params, settings, metrics, notes)
    cluster_explore_finished = pyqtSignal(list)

    # --- Progress banner (message, done, total; total < 0 => indeterminate) ---
    tool_progress = pyqtSignal(str, int, int)
    # Partial results were emitted before cancellation (tool_label, done, total).
    partial_results = pyqtSignal(str, int, int)


class FPSimilaritySignals(QObject):
    """Completion signals for :class:`FPSimilarityWorker` (owned by the dialog, not global WorkerSignals)."""

    finished = pyqtSignal(list)
    failed = pyqtSignal(str)


class SubstructureFilterSignals(QObject):
    """Completion signals for :class:`SubstructureFilterWorker` (owned by the main window)."""

    finished = pyqtSignal(int, object)  # job_gen, frozenset[int] of matched oids (empty = no matches)
    failed = pyqtSignal(int, str)  # job_gen, message


class SqliteRebuildSignals(QObject):
    """Completion signals for :class:`SqliteRebuildWorker` (owned by the main window)."""

    finished = pyqtSignal(int, str)  # job_gen, path to rebuilt sqlite file
    failed = pyqtSignal(int, str)  # job_gen, message

