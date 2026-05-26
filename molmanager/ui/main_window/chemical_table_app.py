import sys
import threading
import time

from PyQt5.QtCore import QThreadPool, QTimer, Qt, pyqtSlot
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
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
from ...performance import PerformanceTracker
from ...tool_progress import ToolProgressState
from ...storage import SqliteTableStore
from ...workers import RenderWorker, SqliteRebuildSignals, SubstructureFilterSignals, WorkerSignals
from ..background_activity import BackgroundActivityHub
from ..compound_table_model import (
    CompoundTableModel,
    CompoundTableView,
    StructureDelegate,
    STRUCTURE_COLUMN_HORIZONTAL_PADDING,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
    STRUCTURE_ROW_DEFAULT_HEIGHT,
)
from ..filter_proxy_model import FilterProxyModel
from ..table_selection_delegate import RowHighlightDelegate
from ..process_queue import ProcessQueueManager
from ..user_guides import open_user_guide_dialog
from .cluster_mixin import ClusterMixin
from .dimension_reduction_mixin import DimensionReductionMixin
from .medchem_space_mixin import MedChemSpaceMixin
from .qsar_mixin import QsarMixin
from .session_mixin import SessionMixin
from .table_ui_mixin import TableUIMixin
from .ingest_export_mixin import IngestExportMixin
from .chemistry_mixin import ChemistryMixin
from ..gui_settings_mixin import GuiSettingsMixin


_FILTER_PANEL_BTN_H = 28
_FILTER_PANEL_BTN_SPACING = 6


def _configure_filter_panel_button(btn: QPushButton) -> None:
    """Uniform size and stretch for all filter-panel action buttons."""
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    btn.setFixedHeight(_FILTER_PANEL_BTN_H)
    btn.setMinimumWidth(0)


class _FilterCardsScrollArea(QScrollArea):
    """Keeps filter cards within the scroll viewport width (no horizontal spill past the panel)."""

    def __init__(self, host: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWidget(host)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API name
        super().resizeEvent(event)
        self._clamp_host_width()

    def _clamp_host_width(self) -> None:
        vp = self.viewport()
        if vp is not None and self._host is not None:
            self._host.setMaximumWidth(max(1, vp.width()))


class ChemicalTableApp(
    QMainWindow,
    SessionMixin,
    TableUIMixin,
    IngestExportMixin,
    ChemistryMixin,
    ClusterMixin,
    DimensionReductionMixin,
    MedChemSpaceMixin,
    QsarMixin,
    GuiSettingsMixin,
):
    """Main PyQt window: compound table, structure column, tools, and RDKit-backed chemistry.

    Each row stores **one explicit structure** (connectivity + stereo as encoded). Isomerism scope
    (E/Z, R/S, tautomers, atropisomers, diastereomers) is summarized for developers in
    ``docs/STEREO_AND_ISOMERISM.md``. Valence, bond order, aromaticity handling, and atom typing in
    the sketcher are summarized in ``docs/VALENCE_BONDS_AND_AROMATICITY.md``.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MolManager")
        self.resize(1500, 900)
        self.threadpool = QThreadPool()
        cfg = load_config()
        # Cap via MOLMANAGER_MAX_THREADPOOL (1–64); otherwise scale gently with CPU count.
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
        self.signals.structure_source_probe.connect(self._on_structure_source_probe, _qc)
        self.signals.rendered.connect(self.on_row_ready, _qc)
        self.signals.washed.connect(self.on_wash_finished, _qc)
        self.signals.neutralized.connect(self.on_neutralize_finished, _qc)
        self.signals.calculated.connect(self.on_calc_finished, _qc)
        self.signals.conformers_finished.connect(self.on_conformers_finished, _qc)
        self.signals.superpose_finished.connect(self.on_superpose_finished, _qc)
        self.signals.custom_calc.connect(self.on_custom_calc_finished, _qc)
        self.signals.partial_results.connect(self._on_partial_results_notice, _qc)
        self.signals.rgroup_decomp_finished.connect(self.on_rgroup_decomp_finished, _qc)
        self.signals.rgroup_decomp_failed.connect(self.on_rgroup_decomp_failed, _qc)
        self.signals.fragment_decomp_finished.connect(self.on_fragment_decomp_finished, _qc)
        self.signals.fragment_decomp_failed.connect(self.on_fragment_decomp_failed, _qc)
        self.signals.fragment_recomp_finished.connect(self.on_fragment_recomp_finished, _qc)
        self.signals.fragment_recomp_failed.connect(self.on_fragment_recomp_failed, _qc)
        self.signals.cluster_failed.connect(self.on_cluster_failed, _qc)
        self.signals.cluster_explore_finished.connect(self.on_cluster_explore_finished, _qc)
        self.signals.export_finished.connect(self._on_export_finished_message, _qc)
        self.signals.tool_progress.connect(self._on_tool_progress, _qc)
        self._substructure_filter_signals = SubstructureFilterSignals()
        self._substructure_filter_signals.finished.connect(self._on_substructure_filter_finished)
        self._substructure_filter_signals.failed.connect(self._on_substructure_filter_failed)
        self._substructure_job_gen = 0
        self._substructure_job_smarts = None
        self._substructure_target_mol_cache = {}
        self._partial_results_notice = None
        self._ingest_loading = False
        self._structures_queued = 0
        self._import_progress_active = False
        self._import_render_done = 0
        self._import_render_goal = 0
        self._import_building_progress_shown = False
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(200)
        self._filter_proxy_model: FilterProxyModel | None = None
        self.init_ui()
        self._apply_filters_timer = QTimer(self)
        self._apply_filters_timer.setSingleShot(True)
        self._apply_filters_timer.timeout.connect(self._apply_filters_impl)
        self._init_gui_settings()
        self.init_menubar()
        self._refresh_structure_delegate_theme()
        self.next_oid = 0
        self._structure_field_override = None
        self._structure_choice_event = threading.Event()
        self._structure_choice_event.set()
        # incremental batch processing state to avoid UI freezes
        self._pending_batches = []  # list of (mols_list, is_last)
        self._processing_batches = False
        self._last_batch_received = False
        self._plot_dialogs: list = []
        self._docked_plot_widget = None
        self._selected_oids_override: frozenset[int] | None = None
        self._in_programmatic_table_selection = False
        self._table_selection_job_gen = 0
        self._table_selection_ctx = None
        self._plot_table_sync_timer = QTimer(self)
        self._plot_table_sync_timer.setSingleShot(True)
        self._plot_table_sync_timer.timeout.connect(self._sync_active_plots_from_table_selection)
        self._selection_browser_dialog = None
        self._sketcher_dialog = None
        self._calculator_dialog = None
        self._data_analysis_dialog = None
        self._cluster_dialog = None
        self._external_db_dialog = None
        self._pubchem_dialog = None
        self._chembl_dialog = None
        self._patent_query_dialog = None
        self._boltz2_dialog = None
        self._vina_dock_dialog = None
        self._export_busy = False
        self._export_prep = None
        self._render2d_queue = None
        self._render2d_batch_active = False
        self._render2d_saved_sort_enabled = None
        self._render2d_row_by_oid = None
        self._render2d_cancel_event = None
        self._render2d_pixmap_target = None
        self._render2d_column_pixmap_mode = True
        self._render2d_session_id = 0
        self._render2d_accept_session = None
        self._render2d_batch_session_tag = 0
        self._render2d_pending = {}
        self._render2d_batch_oids_ordered = []
        self._render2d_snapshot = None
        self._render2d_lazy_flush = False
        self._structure_lazy_scroll_hooked = False
        self._render2d_batch_done_event = threading.Event()
        self._render2d_batch_done_event.set()
        self._render2d_queue_payload = None
        self._render2d_queue_cancel_event = None
        self._ingest_append_mode = False
        self._structure_column_autosize_after_render_oid = None
        self.process_queue = ProcessQueueManager(self)
        self.background_activity = BackgroundActivityHub(self)
        self.background_activity.attach()
        self._processes_dialog = None
        self._perf = PerformanceTracker(
            enabled=cfg.perf_metrics_enabled,
            log_every=cfg.perf_log_every,
        )
        # Local SQLite cache for text/numeric filter pushdown and column search at 100k+ rows.
        self._sqlite_store = SqliteTableStore()
        self._sqlite_store_dirty = False
        self._sqlite_rebuild_in_progress = False
        self._sqlite_rebuild_gen = 0
        self._sqlite_rebuild_pending_path = None
        self._sqlite_rebuild_pending_filters = False
        self._sqlite_rebuild_stale = False
        self._sqlite_rebuild_signals = SqliteRebuildSignals()
        self._sqlite_rebuild_signals.finished.connect(self._on_sqlite_rebuild_finished, _qc)
        self._sqlite_rebuild_signals.failed.connect(self._on_sqlite_rebuild_failed, _qc)
        self._wire_sqlite_store_dirty_tracking()
        self._tool_progress_state = ToolProgressState()
        self._tool_progress_poll_timer = QTimer(self)
        self._tool_progress_poll_timer.setInterval(cfg.tool_progress_poll_ms)
        self._tool_progress_poll_timer.timeout.connect(self._poll_tool_progress_state)

    def _begin_tool_progress(self, message: str, total: int) -> None:
        """Start polled status updates for a long-running queued tool."""
        total_i = max(1, int(total))
        self._tool_progress_active_label = str(message or "")
        self._tool_progress_state.begin(message, total_i)
        self._on_tool_progress(message, 0, total_i)
        if not self._tool_progress_poll_timer.isActive():
            self._tool_progress_poll_timer.start()

    def _poll_tool_progress_state(self) -> None:
        message, done, total, active = self._tool_progress_state.snapshot()
        if not active:
            self._tool_progress_poll_timer.stop()
            return
        self._on_tool_progress(message, done, total)

    def _finish_tool_progress(self, message: str | None = None) -> None:
        """Show 100% once, then stop polling."""
        msg, _done, total, active = self._tool_progress_state.snapshot()
        if active:
            stored = getattr(self, "_tool_progress_active_label", "") or ""
            final_msg = message or msg or stored
            self._on_tool_progress(final_msg, total, total)
        self._tool_progress_state.end()
        self._tool_progress_active_label = ""
        self._tool_progress_poll_timer.stop()

    def render2d_batch_active(self) -> bool:
        """True while Tools → Render 2D batch is running (sorting frozen, etc.)."""
        return self._render2d_batch_active

    @pyqtSlot()
    def _begin_render2d_batch_from_queue(self) -> None:
        """GUI-thread entry for :class:`Render2DBatchHeldJob` (see ``_render2d_queue_payload``)."""
        payload = getattr(self, "_render2d_queue_payload", None)
        cancel_event = getattr(self, "_render2d_queue_cancel_event", None)
        if not payload:
            return
        renders, row_by_oid, src, column_pixmap_mode = payload
        self._begin_render2d_batch_impl(
            renders,
            row_by_oid,
            src,
            column_pixmap_mode=column_pixmap_mode,
            cancel_event=cancel_event,
        )

    def boltz2_predict_active(self) -> bool:
        """True while Tools → Boltz-2 has a ``boltz predict`` subprocess running."""
        dlg = getattr(self, "_boltz2_dialog", None)
        if dlg is None:
            return False
        try:
            fn = getattr(dlg, "is_predict_running", None)
            return bool(fn()) if callable(fn) else False
        except RuntimeError:
            return False

    def cancel_boltz2_predict(self) -> bool:
        """Stop the Boltz-2 ``QProcess`` if the dialog exists and a run is active."""
        dlg = getattr(self, "_boltz2_dialog", None)
        if dlg is None:
            return False
        try:
            fn = getattr(dlg, "cancel_predict", None)
            return bool(fn()) if callable(fn) else False
        except RuntimeError:
            return False

    def vina_dock_active(self) -> bool:
        """True while Tools → Dock has a ``vina`` subprocess running."""
        dlg = getattr(self, "_vina_dock_dialog", None)
        if dlg is None:
            return False
        try:
            fn = getattr(dlg, "is_vina_running", None)
            return bool(fn()) if callable(fn) else False
        except RuntimeError:
            return False

    def cancel_vina_dock(self) -> bool:
        """Stop the Vina ``QProcess`` if the dialog exists and a run is active."""
        dlg = getattr(self, "_vina_dock_dialog", None)
        if dlg is None:
            return False
        try:
            fn = getattr(dlg, "cancel_vina", None)
            return bool(fn()) if callable(fn) else False
        except RuntimeError:
            return False

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
        # Always route the view through the filter proxy: per-row setRowHidden does not scale
        # past ~20k rows, and the filter pipeline already collapses to a single OID set.
        self._filter_proxy_model = FilterProxyModel(self)
        self._filter_proxy_model.setSourceModel(self._table_model)
        self.table.setModel(self._filter_proxy_model)
        self._structure_delegate = StructureDelegate(self.table, self._table_model)
        self._row_highlight_delegate = RowHighlightDelegate(self._table_model, self.table)
        self.table.setItemDelegate(self._row_highlight_delegate)
        self.table.setItemDelegateForColumn(CompoundTableModel.STRUCTURE_COL, self._structure_delegate)
        self.table.setColumnHidden(0, True)
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
        sm = self.table.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(self._on_user_table_selection_changed)
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
        self._init_table_search_panel(self._search_panel)
        self._table_area = QWidget(cw)
        table_area_lyt = QVBoxLayout(self._table_area)
        table_area_lyt.setContentsMargins(0, 0, 0, 0)
        table_area_lyt.setSpacing(4)
        table_area_lyt.addWidget(self._search_panel)
        table_area_lyt.addWidget(self._table_stack, 1)
        self._plot_panel = QFrame()
        self._plot_panel.setVisible(False)
        self._plot_panel.setMinimumWidth(420)
        plot_panel_lyt = QVBoxLayout(self._plot_panel)
        plot_panel_lyt.setContentsMargins(0, 0, 0, 0)
        plot_panel_lyt.setSpacing(2)
        self._plot_panel_host = QWidget()
        self._plot_panel_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plot_panel_lyt.addWidget(self._plot_panel_host, 1)
        plot_bottom = QHBoxLayout()
        plot_bottom.setSpacing(4)
        btn_send_plot_window = QPushButton("Send to New Window")
        btn_send_plot_window.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_send_plot_window.setToolTip("Open the docked plot in a separate floating plotter window.")
        btn_send_plot_window.clicked.connect(self.undock_plot_to_window)
        plot_bottom.addWidget(btn_send_plot_window)
        btn_close_plot = QPushButton("Close Plot")
        btn_close_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_close_plot.setToolTip(
            "Hide the plot panel. Reopen it from Data → Plotter while the plot remains docked."
        )
        btn_close_plot.clicked.connect(self.close_plot_panel_keep_plot)
        plot_bottom.addWidget(btn_close_plot)
        plot_panel_lyt.addLayout(plot_bottom)
        content_h.addWidget(self._table_area, 1)
        # Wide enough for filter cards and two-column bottom actions (avoids clipping).
        _filter_panel_w = 320
        self.f_panel = QFrame()
        self.f_panel.setObjectName("FilterPanel")
        self.f_panel.setFixedWidth(_filter_panel_w)
        self.f_panel.setVisible(False)
        sb_lyt = QVBoxLayout(self.f_panel)
        sb_lyt.setContentsMargins(5, 5, 5, 5)
        sb_lyt.setSpacing(5)

        self._filter_cards_host = QWidget()
        # Ignored horizontal policy: scroll viewport sets width (prevents cards wider than panel).
        self._filter_cards_host.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self.f_container = QVBoxLayout(self._filter_cards_host)
        self.f_container.setContentsMargins(0, 0, 0, 0)
        self.f_container.setSpacing(6)
        self.f_container.setAlignment(Qt.AlignTop)
        self._filter_scroll = _FilterCardsScrollArea(self._filter_cards_host)
        self._filter_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sb_lyt.addWidget(self._filter_scroll, 1)
        QTimer.singleShot(0, self._sync_filter_panel_scroll_content)

        panel_btns = QGridLayout()
        panel_btns.setSpacing(_FILTER_PANEL_BTN_SPACING)
        panel_btns.setContentsMargins(0, 0, 0, 0)
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
        btn_close_panel = QPushButton("Close Filters")
        btn_close_panel.setToolTip(
            "Hide the filter panel. Active filters keep affecting the table."
        )
        btn_close_panel.clicked.connect(self.close_filter_panel_keep_filters)
        btn_disable_all = QPushButton("Disable Filters")
        btn_disable_all.setToolTip(
            "Turn off every filter while keeping this panel open. Use On on each card to enable again."
        )
        btn_disable_all.clicked.connect(self.disable_all_filters_keep_panel)
        for btn in (
            btn_slider,
            btn_text,
            btn_cat,
            btn_ss,
            btn_close_panel,
            btn_disable_all,
        ):
            _configure_filter_panel_button(btn)
        panel_btns.addWidget(btn_slider, 0, 0)
        panel_btns.addWidget(btn_text, 0, 1)
        panel_btns.addWidget(btn_cat, 1, 0)
        panel_btns.addWidget(btn_ss, 1, 1)
        panel_btns.addWidget(btn_close_panel, 2, 0)
        panel_btns.addWidget(btn_disable_all, 2, 1)
        sb_lyt.addLayout(panel_btns)
        content_h.addWidget(self._plot_panel)
        content_h.addWidget(self.f_panel)
        main_v.addLayout(content_h)
        self.status_label = QLabel("Ready")
        main_v.addWidget(self.status_label)

    def init_menubar(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        file_menu.addAction(
            self._bind_hotkey(
                "file.open",
                QAction("&Open File...", self, triggered=self.open_file_dialog),
            )
        )
        file_menu.addAction(QAction("Import &Data...", self, triggered=self.open_import_file_dialog))
        file_menu.addSeparator()
        file_menu.addAction(QAction("Open Session…", self, triggered=self.open_session_file))
        file_menu.addAction(QAction("Save Session…", self, triggered=self.save_session_as))
        file_menu.addAction(QAction("New Session", self, triggered=self.new_session))
        file_menu.addAction(QAction("Duplicate Session", self, triggered=self.duplicate_session))
        file_menu.addSeparator()
        file_menu.addAction(
            self._bind_hotkey(
                "file.export_all",
                QAction("&Export All...", self, triggered=lambda: self.run_export(False)),
            )
        )
        file_menu.addAction(QAction("Export Selected...", self, triggered=lambda: self.run_export(True)))
        file_menu.addSeparator()
        act_browser = self._bind_hotkey(
            "file.browser",
            QAction("&Browser…", self, triggered=self.open_selection_browser),
        )
        act_browser.setToolTip("Open the selection browser to review and act on selected rows.")
        file_menu.addAction(act_browser)
        edit = mb.addMenu("&Edit")
        act_undo = self._bind_hotkey("edit.undo", self._undo_stack.createUndoAction(self))
        act_redo = self._bind_hotkey("edit.redo", self._undo_stack.createRedoAction(self))
        edit.addAction(act_undo)
        edit.addAction(act_redo)
        self.addAction(act_undo)
        self.addAction(act_redo)
        edit.addSeparator()
        edit.addAction(
            self._bind_hotkey("edit.copy", QAction("&Copy", self, triggered=self.edit_copy))
        )
        edit.addAction(
            self._bind_hotkey("edit.paste", QAction("&Paste", self, triggered=self.edit_paste))
        )
        edit.addAction(
            self._bind_hotkey(
                "edit.delete_selection",
                QAction("Delete &Selection", self, triggered=self.edit_delete_selection),
            )
        )
        edit.addSeparator()
        act_invert_sel = self._bind_hotkey(
            "edit.invert_selection",
            QAction("Invert Selection", self, triggered=self.invert_table_selection),
        )
        act_invert_sel.setToolTip(
            "Select all rows that are not currently selected (entire table, including rows hidden by filters)."
        )
        edit.addAction(act_invert_sel)
        act_clear_sel = self._bind_hotkey(
            "edit.clear_selection",
            QAction("Clear Selection", self, triggered=self.clear_table_selection),
        )
        act_clear_sel.setToolTip("Clear the current cell/row selection (Ctrl+Shift+D).")
        edit.addAction(act_clear_sel)
        edit.addAction(
            self._bind_hotkey(
                "edit.clear_table",
                QAction("Clear Table…", self, triggered=self.clear_table_after_confirm),
            )
        )
        tools = mb.addMenu("&Tools")
        tools.setToolTipsVisible(True)

        act_calc_desc = self._bind_hotkey(
            "tools.calculate_descriptors",
            QAction("Calculate Descriptors…", self, triggered=self.open_calc),
        )
        act_calc_desc.setToolTip(
            "Compute RDKit molecular descriptors and append them as columns (selected or visible rows)."
        )
        tools.addAction(act_calc_desc)
        tools.addSeparator()

        prepare_menu = tools.addMenu("&Prepare Structures")
        prepare_menu.setToolTipsVisible(True)
        for title, slot, tip, hk_id in (
            (
                "Disconnect Largest Fragments…",
                self.run_disconnect_fragments,
                "Split disconnected structure fragments into separate rows, keeping the heaviest fragment.",
                None,
            ),
            (
                "Neutralize…",
                self.run_neutralize,
                "Adjust protonation so the net formal charge is zero (RDKit Uncharger); updates the target column.",
                None,
            ),
            (
                "Render 2D…",
                self.run_render_2d_structures,
                "Regenerate 2D structure drawings for selected rows as a background batch (see Processes).",
                "tools.render_2d",
            ),
        ):
            act = QAction(title, self, triggered=slot)
            if hk_id:
                self._bind_hotkey(hk_id, act)
            act.setToolTip(tip)
            prepare_menu.addAction(act)

        self._act_custom_calc = self._bind_hotkey(
            "tools.calculator",
            QAction("Calculator…", self, triggered=self.open_calculator),
        )
        self._act_custom_calc.setToolTip(
            "Add a numeric column from a math expression using existing column names (e.g. sqrt, log10, exp)."
        )
        if load_config().disable_custom_calc:
            self._act_custom_calc.setEnabled(False)
            self._act_custom_calc.setToolTip("Calculator disabled by MOLMANAGER_DISABLE_CUSTOM_CALC.")

        conformations_menu = tools.addMenu("&Conformations")
        conformations_menu.setToolTipsVisible(True)
        for title, slot, tip in (
            (
                "Generate Conformations…",
                self.open_generate_conformations,
                "Build 3D conformer ensembles for molecules (opens the conformer settings dialog).",
            ),
            (
                "Generate Single Conformation…",
                self.open_generate_single_conformation,
                "Embed and minimize one lowest-energy 3D conformer per row into the confs column.",
            ),
            (
                "Superpose Conformers…",
                self.open_superpose_conformers,
                "Align conformer sets for structural comparison.",
            ),
        ):
            act = QAction(title, self, triggered=slot)
            act.setToolTip(tip)
            conformations_menu.addAction(act)

        for title, slot, tip, hk_id in (
            (
                "Fingerprint Similarity...",
                self.open_fp_similarity,
                "Search the table by 2D fingerprint similarity to a query structure.",
                "tools.fingerprint_similarity",
            ),
            (
                "pKa Predictor…",
                self.open_pka_predictor,
                "Estimate ionization / pKa-related properties when the predictor is available.",
                None,
            ),
            (
                "Predict Permeability…",
                self.open_permeability_predictor,
                "Predict Caco-2 and MDCK permeability / efflux endpoints (optional Chemprop install).",
                None,
            ),
            (
                "QSAR…",
                self.open_qsar_dialog,
                "Train regression or classification models on activity vs descriptors or fingerprints.",
                None,
            ),
            (
                "Generate Protomers…",
                self.open_protomer_generator,
                "Enumerate protomers or tautomers from structures and add results to the table.",
                None,
            ),
            (
                "Boltz-2 prediction…",
                self.open_boltz2,
                "Run Boltz protein–ligand cofolding (boltz predict): supply YAML or use quick cofold with FASTA/paste.",
                None,
            ),
            (
                "Dock (Vina)…",
                self.open_vina_dock,
                "Run AutoDock Vina rigid docking: receptor/ligand PDBQT, search box, and log (install vina separately).",
                None,
            ),
        ):
            act = QAction(title, self, triggered=slot)
            if hk_id:
                self._bind_hotkey(hk_id, act)
            act.setToolTip(tip)
            tools.addAction(act)

        decomp_menu = tools.addMenu("&R-Group Decomposition")
        decomp_menu.setToolTipsVisible(True)
        for title, slot, tip in (
            (
                "Core-Based Decomposition…",
                self.open_core_based_decomposition,
                "Decompose structures against a labeled core scaffold (substituent columns).",
            ),
            (
                "BRICS Decomposition…",
                self.open_brics_decomposition,
                "Split structures into BRICS retrosynthetic fragments (new SMILES columns).",
            ),
            (
                "BRICS Recomposition…",
                self.open_brics_recomposition,
                "Combine BRICS fragment columns into new product structures (new rows).",
            ),
            (
                "RECAP Decomposition…",
                self.open_recap_decomposition,
                "Split structures into RECAP retrosynthetic fragments (new SMILES columns).",
            ),
            (
                "RECAP Recomposition…",
                self.open_recap_recomposition,
                "Combine RECAP fragment columns into new product structures (new rows).",
            ),
        ):
            act = QAction(title, self, triggered=slot)
            act.setToolTip(tip)
            decomp_menu.addAction(act)

        tools.addSeparator()
        tools.addAction(self._act_custom_calc)
        act_sketch = self._bind_hotkey(
            "tools.sketcher",
            QAction("&Sketcher…", self, triggered=self.open_sketcher),
        )
        act_sketch.setToolTip("Open the structure sketcher to draw or edit molecules.")
        tools.addAction(act_sketch)

        tools.addSeparator()
        filter_menu = tools.addMenu("&Filter")
        filter_menu.setToolTipsVisible(True)
        self._act_toggle_filter_panel = self._bind_hotkey(
            "tools.toggle_filter_panel",
            QAction("Toggle Panel", self, triggered=self.toggle_filter_panel),
        )
        self._act_toggle_filter_panel.setToolTip("Show or hide the filter panel (Ctrl+Shift+L).")
        self.addAction(self._act_toggle_filter_panel)
        filter_menu.addAction(self._act_toggle_filter_panel)
        filter_menu.addSeparator()
        act_sub = QAction("Add Substructure", self, triggered=lambda: self.add_substructure_filter_card())
        act_sub.setToolTip("Add a filter card that matches a SMARTS substructure in the Structure column.")
        filter_menu.addAction(act_sub)
        act_slider = QAction("Add Slider", self, triggered=lambda: self.add_filter_card())
        act_slider.setToolTip("Add a numeric range slider filter for a column.")
        filter_menu.addAction(act_slider)
        act_txt = QAction("Add Text", self, triggered=lambda: self.add_text_filter_card())
        act_txt.setToolTip("Add a text contains / equals filter for a column.")
        filter_menu.addAction(act_txt)
        act_cat = QAction("Add Category", self, triggered=lambda: self.add_category_filter_card())
        act_cat.setToolTip("Add a categorical multi-select filter for a column.")
        filter_menu.addAction(act_cat)
        filter_menu.addSeparator()
        act_disable_all_filters = QAction("Disable All Filters", self, triggered=self.disable_all_filters_keep_panel)
        act_disable_all_filters.setToolTip(
            "Turn off every filter card. Cards stay in the panel; use On on each card to enable again."
        )
        filter_menu.addAction(act_disable_all_filters)
        act_delete_all_filters = QAction("Delete All Filters", self, triggered=self.delete_all_filters_from_panel)
        act_delete_all_filters.setToolTip("Remove every filter card from the panel.")
        filter_menu.addAction(act_delete_all_filters)
        act_search = self._bind_hotkey(
            "tools.search",
            QAction("&Search…", self, triggered=self.toggle_table_search_panel),
        )
        act_search.setToolTip("Open or focus the in-table search panel (Ctrl+F).")
        tools.addAction(act_search)

        data_menu = mb.addMenu("&Data")
        data_menu.addAction(
            self._bind_hotkey(
                "data.analyze_table",
                QAction("Analyze Table…", self, triggered=self.open_data_analysis),
            )
        )
        data_menu.addAction(
            self._bind_hotkey(
                "data.cluster",
                QAction("&Cluster…", self, triggered=self.open_cluster_dialog),
            )
        )
        data_menu.addSeparator()
        data_menu.addAction(
            QAction("Principal Component Analysis…", self, triggered=self.open_pca_dialog)
        )
        data_menu.addAction(
            QAction("t-SNE Visualization…", self, triggered=self.open_tsne_dialog)
        )
        data_menu.addAction(
            QAction("UMAP Visualization…", self, triggered=self.open_umap_dialog)
        )
        data_menu.addSeparator()
        data_menu.addAction(
            QAction("BOILED-Egg plot…", self, triggered=self.open_boiled_egg_plot)
        )
        data_menu.addAction(
            QAction("Golden triangle plot…", self, triggered=self.open_golden_triangle_plot)
        )
        act_radar = QAction("Radar Plot…", self, triggered=self.open_radar_plot)
        act_radar.setToolTip(
            "Compare compounds on up to six numeric properties (spider/radar chart)."
        )
        data_menu.addAction(act_radar)

        data_menu.addSeparator()
        plot_menu = data_menu.addMenu("&Plotter")
        plot_menu.setToolTipsVisible(True)
        act_plot = self._bind_hotkey(
            "data.plotter",
            QAction("&Plotter…", self, triggered=self.open_plot),
        )
        act_plot.setToolTip("Open the plotter or show the docked plot panel.")
        plot_menu.addAction(act_plot)
        self._act_toggle_plot_panel = self._bind_hotkey(
            "data.toggle_plot_panel",
            QAction("Toggle Panel", self, triggered=self.toggle_plot_panel),
        )
        self._act_toggle_plot_panel.setToolTip(
            "Show or hide the plot panel docked beside the table (Ctrl+Shift+P)."
        )
        self.addAction(self._act_toggle_plot_panel)
        plot_menu.addAction(self._act_toggle_plot_panel)

        ext_menu = mb.addMenu("E&xternal")
        ext_menu.addAction(QAction("Connect to SQL database…", self, triggered=self.open_external_db))
        ext_menu.addSeparator()
        ext_menu.addAction(QAction("Query PubChem…", self, triggered=self.open_pubchem))
        ext_menu.addAction(QAction("Query ChEMBL…", self, triggered=self.open_chembl))
        ext_menu.addAction(QAction("Query Patents…", self, triggered=self.open_patent_query))

        self._init_settings_menu(mb)

        act_help = self._bind_hotkey(
            "help.user_guides",
            QAction("&Help", self, triggered=lambda: open_user_guide_dialog(self)),
        )
        act_help.setToolTip("Open user guides (pick a topic in the list, or press F1).")
        mb.addAction(act_help)
        self.addAction(act_help)

        # Native Windows menu bars can swallow clicks meant for the corner widget; use in-window bar.
        if sys.platform == "win32":
            mb.setNativeMenuBar(False)

        corner = QWidget(mb)
        corner_ly = QHBoxLayout(corner)
        corner_ly.setContentsMargins(0, 0, 4, 0)
        btn_proc = QToolButton(corner)
        btn_proc.setText("Processes")
        btn_proc.setToolTip("View queued background jobs (conformers, descriptors, import, export, …).")
        btn_proc.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_proc.setAutoRaise(True)
        btn_proc.setFocusPolicy(Qt.NoFocus)
        btn_proc.setFont(mb.font())
        btn_proc.clicked.connect(self.open_processes_dialog)
        corner_ly.addWidget(btn_proc)
        mb.setCornerWidget(corner, Qt.TopRightCorner)

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

    def _wire_sqlite_store_dirty_tracking(self) -> None:
        model = getattr(self, "_table_model", None)
        if model is None:
            return
        mark = self._mark_sqlite_store_dirty
        model.dataChanged.connect(lambda *_args: mark())
        model.rowsInserted.connect(lambda *_args: mark())
        model.rowsRemoved.connect(lambda *_args: mark())
        model.columnsInserted.connect(lambda *_args: mark())
        model.columnsRemoved.connect(lambda *_args: mark())
        model.modelReset.connect(lambda *_args: mark())
        model.layoutChanged.connect(lambda *_args: mark())

    def _mark_sqlite_store_dirty(self) -> None:
        if self._sqlite_store is None:
            return
        self._sqlite_store_dirty = True
        if self._sqlite_rebuild_in_progress:
            self._sqlite_rebuild_stale = True

    def _ensure_sqlite_store_current(self) -> bool:
        """Return True when the SQLite mirror is ready for filter/search pushdown."""
        if self._sqlite_store is None:
            return False
        if not self._sqlite_store_dirty:
            return True
        if self._sqlite_rebuild_in_progress:
            return False
        schedule = getattr(self, "_schedule_sqlite_rebuild", None)
        if callable(schedule):
            schedule()
        return False

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

        for dlg in list(getattr(self, "_plot_dialogs", [])):
            try:
                dlg.close()
            except RuntimeError:
                pass
        self._plot_dialogs = []

        for attr in (
            "_processes_dialog",
            "_selection_browser_dialog",
            "_sketcher_dialog",
            "_calculator_dialog",
            "_data_analysis_dialog",
            "_cluster_dialog",
            "_external_db_dialog",
            "_pubchem_dialog",
            "_chembl_dialog",
            "_patent_query_dialog",
            "_boltz2_dialog",
            "_vina_dock_dialog",
        ):
            dlg = getattr(self, attr, None)
            if dlg is None:
                continue
            try:
                dlg.close()
            except RuntimeError:
                setattr(self, attr, None)

        QApplication.processEvents()

        # Brief drain after cancel/kill; process pools are terminated in shutdown_for_exit().
        shutdown_wait_ms = 5_000
        deadline = time.monotonic() + shutdown_wait_ms / 1000.0
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if pq is None or (not pq.has_running_job() and not pq.snapshot().get("fast_running")):
                break
            time.sleep(0.05)

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
        store = getattr(self, "_sqlite_store", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass

    def _on_export_finished_message(self, message: str) -> None:
        self._export_busy = False
        self.status_label.setText(message)
        self._clear_tool_progress()

    def _on_tool_progress(self, message: str, done: int, total: int) -> None:
        if total >= 0 and message:
            self._tool_progress_state.update(message, done, total)
        if total < 0:
            if message:
                self.status_label.setText(message)
        else:
            dv = min(max(done, 0), total)
            pct = int(100 * dv / total) if total > 0 else 100
            if message:
                self.status_label.setText(f"{message} — {dv}/{total} ({pct}%)")
            else:
                self.status_label.setText(f"{dv}/{total} ({pct}%)")

    def _on_partial_results_notice(self, tool_label: str, done: int, total: int) -> None:
        d = max(0, int(done))
        t = max(1, int(total))
        self._partial_results_notice = f"Cancelled — applied partial results for {tool_label} ({d}/{t})."

    def _consume_partial_results_notice(self) -> str | None:
        note = self._partial_results_notice
        self._partial_results_notice = None
        return note

    def _clear_tool_progress(self) -> None:
        self._tool_progress_state.end()
        if self._tool_progress_poll_timer.isActive():
            self._tool_progress_poll_timer.stop()

    def _on_table_double_clicked(self, index) -> None:
        if index.isValid():
            self.on_cell_double_click(index.row(), index.column())
