import sys

from PyQt5.QtCore import QThreadPool, QTimer, Qt
from PyQt5.QtGui import QCloseEvent, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QUndoStack,
    QVBoxLayout,
    QWidget,
)

from ...config import load_config
from ...workers import RenderWorker, SubstructureFilterSignals, WorkerSignals
from ..background_activity import BackgroundActivityHub
from ..compound_table_model import (
    CompoundTableModel,
    CompoundTableView,
    STRUCTURE_COLUMN_HORIZONTAL_PADDING,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
    STRUCTURE_ROW_DEFAULT_HEIGHT,
)
from ..process_queue import ProcessQueueManager
from .cluster_mixin import ClusterMixin
from .session_mixin import SessionMixin
from .table_ui_mixin import TableUIMixin
from .ingest_export_mixin import IngestExportMixin
from .chemistry_mixin import ChemistryMixin


class ChemicalTableApp(QMainWindow, SessionMixin, TableUIMixin, IngestExportMixin, ChemistryMixin, ClusterMixin):
    """Main PyQt window: compound table, structure column, tools, and RDKit-backed chemistry.

    Each row stores **one explicit structure** (connectivity + stereo as encoded). Isomerism scope
    (E/Z, R/S, tautomers, atropisomers, diastereomers) is summarized for developers in
    ``docs/STEREO_AND_ISOMERISM.md``. Valence, bond order, aromaticity handling, and atom typing in
    the sketcher are summarized in ``docs/VALENCE_BONDS_AND_AROMATICITY.md``.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RDKit Chemistry Manager")
        self.resize(1500, 900)
        self.threadpool = QThreadPool()
        cfg = load_config()
        # Cap via CHEMMANAGER_MAX_THREADPOOL (1–64); otherwise scale gently with CPU count.
        if cfg.max_threadpool is not None:
            cap = cfg.max_threadpool
        else:
            ideal = max(1, QThreadPool.globalInstance().maxThreadCount() or 4)
            cap = max(4, min(ideal * 2, 16))
        self.threadpool.setMaxThreadCount(cap)
        # Dedicated pool so 2D rendering does not compete with descriptor/calc/IO workers.
        self._render_threadpool = QThreadPool()
        if cfg.render_threadpool is not None:
            ren_cap = cfg.render_threadpool
        else:
            ren_cap = max(2, min(cap, 8))
        self._render_threadpool.setMaxThreadCount(ren_cap)
        self.mols, self.headers, self.filters, self.global_bounds = {}, [], [], {}
        self.zoomed_ids = set()
        self.signals = WorkerSignals()
        _qc = Qt.QueuedConnection
        self.signals.mols_loaded.connect(self.on_file_loaded, _qc)
        self.signals.rendered.connect(self.on_row_ready, _qc)
        self.signals.washed.connect(self.on_wash_finished, _qc)
        self.signals.calculated.connect(self.on_calc_finished, _qc)
        self.signals.conformers_finished.connect(self.on_conformers_finished, _qc)
        self.signals.superpose_finished.connect(self.on_superpose_finished, _qc)
        self.signals.custom_calc.connect(self.on_custom_calc_finished, _qc)
        self.signals.rgroup_decomp_finished.connect(self.on_rgroup_decomp_finished, _qc)
        self.signals.rgroup_decomp_failed.connect(self.on_rgroup_decomp_failed, _qc)
        self.signals.cluster_failed.connect(self.on_cluster_failed, _qc)
        self.signals.cluster_explore_finished.connect(self.on_cluster_explore_finished, _qc)
        self.signals.export_finished.connect(self._on_export_finished_message, _qc)
        self.signals.tool_progress.connect(self._on_tool_progress, _qc)
        self._substructure_filter_signals = SubstructureFilterSignals()
        self._substructure_filter_signals.finished.connect(self._on_substructure_filter_finished)
        self._substructure_filter_signals.failed.connect(self._on_substructure_filter_failed)
        self._substructure_job_gen = 0
        self._substructure_job_smarts = None
        self._ingest_loading = False
        self._structures_queued = 0
        self._import_progress_active = False
        self._import_render_done = 0
        self._import_render_goal = 0
        self._import_building_progress_shown = False
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(200)
        self.init_ui()
        self._apply_filters_timer = QTimer(self)
        self._apply_filters_timer.setSingleShot(True)
        self._apply_filters_timer.timeout.connect(self._apply_filters_impl)
        self.init_menubar()
        self.next_oid = 0
        self._structure_field_override = None
        # incremental batch processing state to avoid UI freezes
        self._pending_batches = []  # list of (mols_list, is_last)
        self._processing_batches = False
        self._last_batch_received = False
        self._plot_dialog = None
        self._selection_browser_dialog = None
        self._sketcher_dialog = None
        self._calculator_dialog = None
        self._data_analysis_dialog = None
        self._cluster_dialog = None
        self._external_db_dialog = None
        self._pubchem_dialog = None
        self._chembl_dialog = None
        self._export_busy = False
        self._export_prep = None
        self._render2d_queue = None
        self._render2d_batch_active = False
        self._render2d_saved_sort_enabled = None
        self._render2d_row_by_oid = None
        self._render2d_cancel_event = None
        self._render2d_pixmap_target = None
        self._render2d_session_id = 0
        self._render2d_accept_session = None
        self._render2d_batch_session_tag = 0
        self._render2d_pending = {}
        self._render2d_batch_oids_ordered = []
        self._render2d_snapshot = None
        self._ingest_append_mode = False
        self._structure_column_autosize_after_render_oid = None
        self.process_queue = ProcessQueueManager(self)
        self.background_activity = BackgroundActivityHub(self)
        self.background_activity.attach()
        self._processes_dialog = None

    def render2d_batch_active(self) -> bool:
        """True while Tools → Render 2D batch is running (sorting frozen, etc.)."""
        return self._render2d_batch_active

    def start_render_worker(
        self,
        oid,
        mol,
        w=None,
        h=None,
        cancel_event=None,
        skip_mol_props=False,
        render_batch_session=0,
    ):
        """Queue 2D structure rendering on the render-only thread pool."""
        if w is None:
            w = STRUCTURE_DEPICT_WIDTH
        if h is None:
            h = STRUCTURE_DEPICT_HEIGHT
        self._render_threadpool.start(
            RenderWorker(
                oid,
                mol,
                self.signals,
                w,
                h,
                cancel_event=cancel_event,
                skip_mol_props=skip_mol_props,
                render_batch_session=render_batch_session,
            )
        )

    def init_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        main_v = QVBoxLayout(cw)
        content_h = QHBoxLayout()
        self._table_model = CompoundTableModel([])
        self.table = CompoundTableView()
        self.table.set_compound_model(self._table_model)
        self.table.setAlternatingRowColors(True)
        vh = self.table.verticalHeader()
        vh.setDefaultSectionSize(STRUCTURE_ROW_DEFAULT_HEIGHT)
        vh.setContextMenuPolicy(Qt.CustomContextMenu)
        vh.customContextMenuRequested.connect(self.show_row_header_menu)
        # Reorder rows by dragging row numbers (same idea as movable column headers).
        vh.setSectionsMovable(True)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.show_header_menu)
        # Allow rearranging columns by dragging the header (keep ID/Structure pinned).
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setFirstSectionMovable(False)
        self.table.horizontalHeader().sectionMoved.connect(self._on_column_moved)
        self.table.horizontalHeader().sectionClicked.connect(self._on_horizontal_header_section_clicked)
        self.table.setColumnWidth(
            CompoundTableModel.STRUCTURE_COL,
            STRUCTURE_DEPICT_WIDTH + STRUCTURE_COLUMN_HORIZONTAL_PADDING,
        )
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_menu)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self._table_stack = QStackedWidget()
        self._loading_page = QWidget()
        load_lyt = QVBoxLayout(self._loading_page)
        load_lyt.addStretch()
        self._loading_detail = QLabel("")
        self._loading_detail.setAlignment(Qt.AlignCenter)
        self._loading_detail.setWordWrap(True)
        self._loading_detail.setStyleSheet("font-size: 14px; color: palette(mid); padding: 24px;")
        load_lyt.addWidget(self._loading_detail)
        load_lyt.addStretch()
        self._table_stack.addWidget(self._loading_page)
        self._table_stack.addWidget(self.table)
        self._table_stack.setCurrentIndex(1)
        self._search_panel = QFrame(cw)
        self._search_panel.setVisible(False)
        sp_lyt = QHBoxLayout(self._search_panel)
        sp_lyt.setContentsMargins(6, 4, 6, 4)
        self._search_col_combo = QComboBox()
        self._search_col_combo.setMinimumWidth(180)
        self._search_query_edit = QLineEdit()
        self._search_query_edit.setPlaceholderText(
            "Query; use commas to separate terms (see AND/OR). Press Enter or Find."
        )
        self._search_query_edit.returnPressed.connect(self._run_table_search)
        self._search_match_combo = QComboBox()
        self._search_match_combo.addItem("All (AND)", "and")
        self._search_match_combo.addItem("Any (OR)", "or")
        self._search_match_combo.setToolTip(
            "With comma-separated terms: AND requires every term to match the cell; "
            "OR requires at least one term to match."
        )
        self._search_partial_cb = QCheckBox("Partial match")
        self._search_partial_cb.setChecked(True)  # default: substring-style text search
        self._search_partial_cb.setToolTip(
            "When on, each term matches if it appears anywhere in the cell. "
            "When off, the whole cell text (after trimming ends) must match a term."
        )
        self._search_substructure_cb = QCheckBox("Substructure (SMILES/SMARTS)")
        self._search_substructure_cb.setToolTip(
            "Each comma-separated term is parsed as SMARTS, then SMILES if needed; "
            "rows are matched when the row's structure contains that substructure."
        )
        self._search_case_sensitive_cb = QCheckBox("Case Sensitive")
        self._search_case_sensitive_cb.setToolTip(
            "When on, letter case must match the cell text. When off, matching ignores case."
        )
        self._search_find_btn = QPushButton("Find")
        self._search_find_btn.clicked.connect(self._run_table_search)
        sp_lyt.addWidget(QLabel("Column:"))
        sp_lyt.addWidget(self._search_col_combo)
        sp_lyt.addWidget(self._search_query_edit, 1)
        sp_lyt.addWidget(self._search_match_combo)
        sp_lyt.addWidget(self._search_partial_cb)
        sp_lyt.addWidget(self._search_substructure_cb)
        sp_lyt.addWidget(self._search_case_sensitive_cb)
        sp_lyt.addWidget(self._search_find_btn)
        self._search_substructure_cb.toggled.connect(self._on_search_substructure_mode_toggled)
        self._table_area = QWidget(cw)
        table_area_lyt = QVBoxLayout(self._table_area)
        table_area_lyt.setContentsMargins(0, 0, 0, 0)
        table_area_lyt.setSpacing(4)
        table_area_lyt.addWidget(self._search_panel)
        table_area_lyt.addWidget(self._table_stack, 1)
        content_h.addWidget(self._table_area, 1)
        self.f_panel = QFrame()
        self.f_panel.setFixedWidth(280)
        self.f_panel.setVisible(False)
        sb_lyt = QVBoxLayout(self.f_panel)
        sb_lyt.setSpacing(2)

        self._filter_cards_host = QWidget()
        self._filter_cards_host.setMinimumWidth(258)
        self.f_container = QVBoxLayout(self._filter_cards_host)
        self.f_container.setContentsMargins(0, 0, 0, 0)
        self.f_container.setSpacing(10)
        self.f_container.setAlignment(Qt.AlignTop)
        self.f_container.setSizeConstraint(QLayout.SetMinimumSize)
        self._filter_scroll = QScrollArea()
        self._filter_scroll.setWidgetResizable(True)
        self._filter_scroll.setFrameShape(QFrame.NoFrame)
        self._filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._filter_scroll.setWidget(self._filter_cards_host)
        self._filter_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sb_lyt.addWidget(self._filter_scroll, 1)

        add_grid = QGridLayout()
        add_grid.setSpacing(2)
        btn_slider = QPushButton("Add Slider")
        btn_slider.setToolTip("Add a numeric range (slider) filter for the current table.")
        btn_slider.clicked.connect(lambda: self.add_filter_card())
        btn_text = QPushButton("Add Text")
        btn_text.setToolTip("Add a text filter card.")
        btn_text.clicked.connect(lambda: self.add_text_filter_card())
        btn_cat = QPushButton("Add Category")
        btn_cat.setToolTip("Add a category filter card.")
        btn_cat.clicked.connect(lambda: self.add_category_filter_card())
        btn_ss = QPushButton("Add Substructure")
        btn_ss.setToolTip("Add a SMARTS substructure filter card.")
        btn_ss.clicked.connect(lambda: self.add_substructure_filter_card())
        add_grid.addWidget(btn_slider, 0, 0)
        add_grid.addWidget(btn_text, 0, 1)
        add_grid.addWidget(btn_cat, 1, 0)
        add_grid.addWidget(btn_ss, 1, 1)
        sb_lyt.addLayout(add_grid)

        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        btn_row_style = "QPushButton { padding: 4px 6px; min-height: 26px; max-height: 26px; }"
        btn_close_panel = QPushButton("Close Filters")
        btn_close_panel.setStyleSheet(btn_row_style)
        btn_close_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_close_panel.setToolTip(
            "Close the filter panel and turn off every filter. Filter cards stay; use On to enable again."
        )
        btn_close_panel.clicked.connect(self.close_filter_panel_and_disable_filters)
        btn_close_keep = QPushButton("Close and Keep")
        btn_close_keep.setStyleSheet(btn_row_style)
        btn_close_keep.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_close_keep.setToolTip(
            "Close the filter panel only. Filters stay on and keep affecting the table."
        )
        btn_close_keep.clicked.connect(self.close_filter_panel_keep_filters)
        btn_disable_all = QPushButton("Disable Filters")
        btn_disable_all.setStyleSheet(btn_row_style)
        btn_disable_all.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_disable_all.setToolTip(
            "Turn off every filter while keeping this panel open. Use On on each card to enable again."
        )
        btn_disable_all.clicked.connect(self.disable_all_filters_keep_panel)
        btn_delete_filters = QPushButton("Delete Filters")
        btn_delete_filters.setStyleSheet(btn_row_style)
        btn_delete_filters.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_delete_filters.setToolTip("Remove every filter card from this panel.")
        btn_delete_filters.clicked.connect(lambda: self.delete_all_filters_from_panel())
        bottom.addWidget(btn_close_panel, 1)
        bottom.addWidget(btn_close_keep, 1)
        bottom.addWidget(btn_disable_all, 1)
        bottom.addWidget(btn_delete_filters, 1)
        sb_lyt.addLayout(bottom)
        content_h.addWidget(self.f_panel)
        main_v.addLayout(content_h)
        self.status_label = QLabel("Ready")
        main_v.addWidget(self.status_label)

    def init_menubar(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        file_menu.addAction(
            QAction("&Open File...", self, shortcut=QKeySequence.Open, triggered=self.open_file_dialog)
        )
        file_menu.addAction(QAction("Import &Data...", self, triggered=self.open_import_file_dialog))
        file_menu.addSeparator()
        file_menu.addAction(QAction("Open Session…", self, triggered=self.open_session_file))
        file_menu.addAction(QAction("Save Session…", self, triggered=self.save_session_as))
        file_menu.addAction(QAction("New Session", self, triggered=self.new_session))
        file_menu.addAction(QAction("Duplicate Session", self, triggered=self.duplicate_session))
        file_menu.addSeparator()
        file_menu.addAction(QAction("&Export All...", self, shortcut="Ctrl+S", triggered=lambda: self.run_export(False)))
        file_menu.addAction(QAction("Export Selected...", self, triggered=lambda: self.run_export(True)))
        edit = mb.addMenu("&Edit")
        edit.addAction(self._undo_stack.createUndoAction(self))
        edit.addAction(self._undo_stack.createRedoAction(self))
        edit.addSeparator()
        edit.addAction(QAction("&Copy", self, shortcut=QKeySequence.Copy, triggered=self.edit_copy))
        edit.addAction(QAction("&Paste", self, shortcut=QKeySequence.Paste, triggered=self.edit_paste))
        edit.addAction(
            QAction("Delete &Selection", self, shortcut=QKeySequence.Delete, triggered=self.edit_delete_selection)
        )
        edit.addSeparator()
        act_clear_sel = QAction(
            "Clear Selection",
            self,
            shortcut=QKeySequence("Ctrl+Shift+D"),
            triggered=self.table.clearSelection,
        )
        act_clear_sel.setToolTip("Clear the current cell/row selection (Ctrl+Shift+D).")
        edit.addAction(act_clear_sel)
        edit.addAction(
            QAction(
                "Clear Table…",
                self,
                shortcut=QKeySequence("Ctrl+Shift+Backspace"),
                triggered=self.clear_table_after_confirm,
            )
        )
        tools = mb.addMenu("&Tools")
        self._act_custom_calc = QAction("Calculator…", self, triggered=self.open_calculator)
        if load_config().disable_custom_calc:
            self._act_custom_calc.setEnabled(False)
            self._act_custom_calc.setToolTip("Calculator disabled by CHEMMANAGER_DISABLE_CUSTOM_CALC.")
        tools.addActions(
            [
                QAction("Disconnect Largest Fragments…", self, triggered=self.run_disconnect_fragments),
                QAction("Render 2D…", self, triggered=self.run_render_2d_structures),
                QAction("Calculate Descriptors…", self, triggered=self.open_calc),
                QAction("Generate Conformations…", self, triggered=self.open_generate_conformations),
                QAction("Superpose Conformers…", self, triggered=self.open_superpose_conformers),
                QAction("R Group Decomposition…", self, triggered=self.open_rgroup_decomposition),
                QAction("Fingerprint Similarity...", self, triggered=self.open_fp_similarity),
                QAction("pKa Predictor…", self, triggered=self.open_pka_predictor),
                QAction("Generate Protomers…", self, triggered=self.open_protomer_generator),
            ]
        )
        tools.addSeparator()
        filter_menu = tools.addMenu("&Filter")
        self._act_toggle_filter_panel = QAction("Toggle Panel", self)
        self._act_toggle_filter_panel.setToolTip("Show or hide the filter panel (Ctrl+Shift+L).")
        self._act_toggle_filter_panel.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self._act_toggle_filter_panel.setShortcutContext(Qt.ApplicationShortcut)
        self._act_toggle_filter_panel.triggered.connect(
            lambda: self.f_panel.setVisible(not self.f_panel.isVisible())
        )
        self.addAction(self._act_toggle_filter_panel)
        filter_menu.addAction(self._act_toggle_filter_panel)
        filter_menu.addSeparator()
        filter_menu.addAction(QAction("Substructure Filter", self, triggered=lambda: self.add_substructure_filter_card()))
        filter_menu.addAction(QAction("Add Slider", self, triggered=lambda: self.add_filter_card()))
        filter_menu.addAction(QAction("Add Text", self, triggered=lambda: self.add_text_filter_card()))
        filter_menu.addAction(QAction("Add Category", self, triggered=lambda: self.add_category_filter_card()))
        tools.addAction(
            QAction("&Search…", self, shortcut=QKeySequence("Ctrl+F"), triggered=self.toggle_table_search_panel)
        )
        tools.addAction(QAction("&Browser…", self, triggered=self.open_selection_browser))
        tools.addSeparator()
        tools.addAction(QAction("&Plotter…", self, triggered=self.open_plot))
        tools.addAction(self._act_custom_calc)
        tools.addAction(QAction("&Sketcher…", self, triggered=self.open_sketcher))

        data_menu = mb.addMenu("&Data")
        data_menu.addAction(
            QAction("Analyze and summarize table…", self, triggered=self.open_data_analysis)
        )
        data_menu.addAction(QAction("&Cluster…", self, triggered=self.open_cluster_dialog))

        ext_menu = mb.addMenu("E&xternal")
        ext_menu.addAction(QAction("Connect to SQL database…", self, triggered=self.open_external_db))
        ext_menu.addSeparator()
        ext_menu.addAction(QAction("Query PubChem…", self, triggered=self.open_pubchem))
        ext_menu.addAction(QAction("Query ChEMBL…", self, triggered=self.open_chembl))

        # Native Windows menu bars can swallow clicks meant for the corner widget; use in-window bar.
        if sys.platform == "win32":
            mb.setNativeMenuBar(False)

        btn_proc = QToolButton(mb)
        btn_proc.setText("Processes")
        btn_proc.setToolTip("View queued background jobs (conformers, descriptors, import, export, …).")
        btn_proc.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_proc.setAutoRaise(True)
        btn_proc.setFocusPolicy(Qt.NoFocus)
        btn_proc.setFont(mb.font())
        btn_proc.clicked.connect(self.open_processes_dialog)
        mb.setCornerWidget(btn_proc, Qt.TopRightCorner)

    def _on_processes_dialog_destroyed(self) -> None:
        self._processes_dialog = None

    def open_processes_dialog(self) -> None:
        from ..processes_dialog import ProcessesDialog

        dlg = getattr(self, "_processes_dialog", None)
        if dlg is not None:
            try:
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                dlg._reload()
                return
            except RuntimeError:
                self._processes_dialog = None
        w = ProcessesDialog(self)
        self._processes_dialog = w
        w.destroyed.connect(self._on_processes_dialog_destroyed)
        w.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._prepare_application_shutdown()
        super().closeEvent(event)

    def _prepare_application_shutdown(self) -> None:
        """Stop timers, cancel background work, close modeless dialogs, drain thread pools."""
        t = getattr(self, "_apply_filters_timer", None)
        if t is not None:
            t.stop()

        self.background_activity.prepare_for_quit()

        pq = getattr(self, "process_queue", None)

        for attr in (
            "_processes_dialog",
            "_plot_dialog",
            "_selection_browser_dialog",
            "_sketcher_dialog",
            "_calculator_dialog",
            "_data_analysis_dialog",
            "_cluster_dialog",
            "_external_db_dialog",
            "_pubchem_dialog",
            "_chembl_dialog",
        ):
            dlg = getattr(self, attr, None)
            if dlg is None:
                continue
            try:
                dlg.close()
            except RuntimeError:
                setattr(self, attr, None)

        QApplication.processEvents()

        shutdown_wait_ms = 30_000
        tp = getattr(self, "threadpool", None)
        if tp is not None:
            tp.waitForDone(shutdown_wait_ms)
            clear = getattr(tp, "clear", None)
            if callable(clear):
                clear()

        rtp = getattr(self, "_render_threadpool", None)
        if rtp is not None:
            rtp.waitForDone(shutdown_wait_ms)

        if pq is not None:
            pq.wait_for_all_jobs(shutdown_wait_ms)

    def _on_export_finished_message(self, message: str) -> None:
        self._export_busy = False
        self.status_label.setText(message)
        self._clear_tool_progress()

    def _on_tool_progress(self, message: str, done: int, total: int) -> None:
        if total < 0:
            if message:
                self.status_label.setText(message)
        else:
            dv = min(max(done, 0), total)
            pct = int(100 * dv / total) if total > 0 else 100
            if message:
                self.status_label.setText(f"{message} ({pct}%)")
            else:
                self.status_label.setText(f"{pct}%")

    def _clear_tool_progress(self) -> None:
        # Text-only progress; nothing to clear besides the caller's status text.
        return

    def _on_table_double_clicked(self, index) -> None:
        if index.isValid():
            self.on_cell_double_click(index.row(), index.column())
