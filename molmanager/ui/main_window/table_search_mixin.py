"""In-window table search (column text and substructure across rows)."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass

from PyQt5.QtCore import QItemSelectionModel, QTimer, Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from rdkit import Chem

from ...config import load_config
from ..search_panel import SearchCriterionRow
from ..search_query import (
    evaluate_search_expression,
    parse_search_expression,
    parse_search_term_groups,
    parse_substructure_term,
    sqlite_where_for_expression,
    validate_search_text_query,
)


@dataclass(frozen=True)
class _SearchCriterionSpec:
    col: int
    query: str
    term_groups: list[list[str]]
    glue: str | None  # None for first row; ``and`` / ``or`` vs previous
    partial: bool
    case_sensitive: bool
    substructure: bool


class TableSearchMixin:
    """Uses ``_search_*`` widgets, ``table``, ``headers``, ``_table_model``, ``status_label``,
    and ``_mol_for_structure_row`` from the concrete app / ``TableUIMixin``.
    """

    _search_criterion_rows: list[SearchCriterionRow]

    @property
    def _search_col_combo(self) -> QComboBox | None:
        """First search row column combo (tests and legacy callers)."""
        if not getattr(self, "_search_criterion_rows", None):
            return None
        return self._search_criterion_rows[0].col_combo

    @property
    def _search_query_edit(self):
        """First search row query field (tests and legacy callers)."""
        if not getattr(self, "_search_criterion_rows", None):
            return None
        return self._search_criterion_rows[0].query_edit

    @property
    def _search_partial_cb(self) -> QCheckBox | None:
        if not getattr(self, "_search_criterion_rows", None):
            return None
        return self._search_criterion_rows[0].partial_cb

    @property
    def _search_case_sensitive_cb(self) -> QCheckBox | None:
        if not getattr(self, "_search_criterion_rows", None):
            return None
        return self._search_criterion_rows[0].case_cb

    @property
    def _search_substructure_cb(self) -> QCheckBox | None:
        if not getattr(self, "_search_criterion_rows", None):
            return None
        return self._search_criterion_rows[0].substructure_cb

    def _init_table_search_panel(self, panel: QFrame) -> None:
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        self._search_rows_host = QWidget(panel)
        self._search_rows_layout = QVBoxLayout(self._search_rows_host)
        self._search_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._search_rows_layout.setSpacing(4)
        outer.addWidget(self._search_rows_host)

        self._search_criterion_rows = []
        self._add_search_criterion_row()
        self._wire_table_search_column_refresh()

    def _wire_table_search_column_refresh(self) -> None:
        """Refresh search column combos when the table gains or loses columns."""
        if getattr(self, "_search_column_refresh_wired", False):
            return
        model = getattr(self, "_table_model", None)
        if model is None:
            return
        model.columnsInserted.connect(self._on_table_search_columns_changed)
        model.columnsRemoved.connect(self._on_table_search_columns_changed)
        model.modelReset.connect(self._on_table_search_columns_changed)
        model.headerDataChanged.connect(self._on_table_search_header_changed)
        self._search_column_refresh_wired = True

    def _on_table_search_columns_changed(self, *_args) -> None:
        self._refresh_table_search_column_combos()

    def _on_table_search_header_changed(self, orientation, *_args) -> None:
        if int(orientation) == Qt.Horizontal:
            self._refresh_table_search_column_combos()

    def _refresh_table_search_column_combos(self) -> None:
        if not getattr(self, "_search_criterion_rows", None):
            return
        self._populate_table_search_columns_combo()

    def _remove_search_criterion_row(self, row: SearchCriterionRow) -> None:
        if not self._search_criterion_rows or row is self._search_criterion_rows[0]:
            return
        if row not in self._search_criterion_rows:
            return
        self._search_criterion_rows.remove(row)
        self._search_rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def _add_search_criterion_row(self) -> SearchCriterionRow:
        is_first = not self._search_criterion_rows
        row = SearchCriterionRow(
            self._search_rows_host,
            show_remove=not is_first,
            show_glue=not is_first,
            show_add=is_first,
            on_add=self._add_search_criterion_row,
        )
        if not is_first:
            row.remove_btn.clicked.connect(lambda _checked=False, rw=row: self._remove_search_criterion_row(rw))
            row.copy_options_from(self._search_criterion_rows[0])
        row.query_edit.returnPressed.connect(self._run_table_search)
        self._search_rows_layout.addWidget(row)
        self._search_criterion_rows.append(row)
        self._populate_search_row_columns(row)
        if not is_first:
            row.query_edit.setFocus(Qt.ShortcutFocusReason)
        return row

    @staticmethod
    def _search_combo_label_for_header(header: str) -> str:
        if header == "ID_HIDDEN":
            return "Row ID"
        return header or ""

    def _populate_search_row_columns(
        self, row: SearchCriterionRow, *, preferred_col: int | None = None
    ) -> None:
        combo = row.col_combo
        prev = combo.currentData()
        if preferred_col is not None:
            prev = preferred_col
        combo.blockSignals(True)
        combo.clear()
        if not self.headers:
            combo.addItem("(no columns)", -1)
        else:
            ncols = self._table_model.columnCount()
            for i, h in enumerate(self.headers):
                if i >= ncols:
                    break
                combo.addItem(self._search_combo_label_for_header(h), i)
        combo.blockSignals(False)
        if prev is not None and isinstance(prev, int) and prev >= 0:
            for j in range(combo.count()):
                if combo.itemData(j) == prev:
                    combo.setCurrentIndex(j)
                    return
        if combo.count():
            combo.setCurrentIndex(0)

    def _populate_table_search_columns_combo(self) -> None:
        for row in self._search_criterion_rows:
            self._populate_search_row_columns(row)

    def toggle_table_search_panel(self) -> None:
        panel: QFrame = self._search_panel
        panel.setVisible(not panel.isVisible())
        if panel.isVisible():
            self._populate_table_search_columns_combo()
            if self._search_criterion_rows:
                self._search_criterion_rows[0].query_edit.setFocus(Qt.ShortcutFocusReason)
            self.status_label.setText(
                "Search: use Add for more columns; AND/OR between rows. "
                "Within a row: & AND, | or comma OR. Press Enter to run."
            )

    def open_table_search_with_column(self, logical_col: int) -> None:
        """Show the search bar and pre-select a column (e.g. from the header context menu)."""
        if logical_col < 0 or logical_col >= len(self.headers):
            return
        panel: QFrame = self._search_panel
        panel.setVisible(True)
        if not self._search_criterion_rows:
            self._add_search_criterion_row()
        self._populate_search_row_columns(self._search_criterion_rows[0], preferred_col=logical_col)
        self._search_criterion_rows[0].query_edit.setFocus(Qt.ShortcutFocusReason)
        hname = self.headers[logical_col]
        self.status_label.setText(f'Search: column "{hname}" selected. Enter a query, then press Enter.')

    def _search_query_pattern_mol(self, text: str) -> Chem.Mol | None:
        """Parse one query string as a substructure pattern (SMARTS first, then SMILES)."""
        text = (text or "").strip()
        if not text:
            return None
        try:
            m = Chem.MolFromSmarts(text)
            if m is not None:
                return m
        except Exception:
            pass
        try:
            return Chem.MolFromSmiles(text)
        except Exception:
            return None

    def _resolve_search_column(self, combo: QComboBox) -> int | None:
        col = combo.currentData()
        if not self.headers or col is None or (isinstance(col, int) and col < 0):
            return None
        if not isinstance(col, int) or col >= self._table_model.columnCount():
            return None
        return col

    def _collect_search_criteria(self) -> list[_SearchCriterionSpec] | None:
        specs: list[_SearchCriterionSpec] = []
        for i, row in enumerate(self._search_criterion_rows):
            needle = (row.query_edit.text() or "").strip()
            if not row.substructure_cb.isChecked():
                err = validate_search_text_query(
                    needle, partial=row.partial_cb.isChecked()
                )
                if err:
                    self.status_label.setText(err)
                    return None
            term_groups = parse_search_term_groups(needle)
            if not term_groups:
                continue
            col = self._resolve_search_column(row.col_combo)
            if col is None:
                self._populate_search_row_columns(row)
                col = self._resolve_search_column(row.col_combo)
            if col is None:
                self.status_label.setText("Search: no columns loaded.")
                return None
            glue = None if i == 0 else row.glue()
            specs.append(
                _SearchCriterionSpec(
                    col=col,
                    query=needle,
                    term_groups=term_groups,
                    glue=glue,
                    partial=row.partial_cb.isChecked(),
                    case_sensitive=row.case_cb.isChecked(),
                    substructure=row.substructure_cb.isChecked(),
                )
            )
        return specs

    def _find_rows_substructure(self, term_groups: list[list[str]]) -> list[int] | None:
        or_patterns: list[list[tuple[Chem.Mol, bool]]] = []
        for and_terms in term_groups:
            and_patterns: list[tuple[Chem.Mol, bool]] = []
            for t in and_terms:
                pat_text, negated = parse_substructure_term(t)
                if not pat_text:
                    continue
                q = self._search_query_pattern_mol(pat_text)
                if q is None:
                    QMessageBox.warning(
                        self,
                        "Search",
                        f"Could not parse term as SMARTS or SMILES: {pat_text!r}",
                    )
                    return None
                and_patterns.append((q, negated))
            if and_patterns:
                or_patterns.append(and_patterns)
        if not or_patterns:
            return []

        def _substruct_ok(mol: Chem.Mol) -> bool:
            for and_patterns in or_patterns:
                group_ok = True
                for q, negated in and_patterns:
                    try:
                        hit = bool(mol.HasSubstructMatch(q))
                    except Exception:
                        hit = False
                    if negated:
                        hit = not hit
                    if not hit:
                        group_ok = False
                        break
                if group_ok:
                    return True
            return False

        rows: list[int] = []
        for r in range(self._table_model.rowCount()):
            mol = self._mol_for_structure_row(r)
            if mol is None:
                continue
            if _substruct_ok(mol):
                rows.append(r)
        return rows

    def _find_rows_text(
        self,
        col: int,
        needle: str,
        *,
        partial: bool,
        case_sensitive: bool,
    ) -> list[int]:
        expression = parse_search_expression(needle, partial=partial)
        if not expression:
            return []
        ensure_sqlite = getattr(self, "_ensure_sqlite_store_current", None)
        sqlite_ready = True
        if callable(ensure_sqlite):
            sqlite_ready = ensure_sqlite()
        store = getattr(self, "_sqlite_store", None)
        if not sqlite_ready and self._table_model.rowCount() > 5000:
            self.status_label.setText("Indexing table… (retry search in a moment)")
            return []
        perf = getattr(self, "_perf", None)
        scope = perf.track if perf is not None else (lambda *_a, **_k: nullcontext())

        if store is not None and sqlite_ready and col >= 2 and col < len(self.headers):
            header = self.headers[col]
            qh = str(header).replace('"', '""')
            sql_frag = sqlite_where_for_expression(
                qh, expression, partial=partial, case_sensitive=case_sensitive
            )
            if sql_frag is not None:
                where_sql, sql_args = sql_frag
                rows: list[int] = []
                with scope("search.sqlite_pushdown"):
                    page = max(1000, int(getattr(load_config(), "sqlite_backend_page_size", 5000)))
                    offset = 0
                    while True:
                        recs = store.fetch_page(
                            limit=page,
                            offset=offset,
                            where_sql=where_sql,
                            args=tuple(sql_args),
                            sort_by="oid",
                            ascending=True,
                        )
                        if not recs:
                            break
                        for oid, _ in recs:
                            rr = self._table_model.logical_row_for_oid(int(oid))
                            if rr >= 0:
                                rows.append(rr)
                        offset += len(recs)
                return rows

        rows = []
        with scope("search.text"):
            for r in range(self._table_model.rowCount()):
                hay = self._table_model.cell_text(r, col)
                if evaluate_search_expression(
                    hay,
                    expression,
                    partial=partial,
                    case_sensitive=case_sensitive,
                ):
                    rows.append(r)
        return rows

    def _combine_search_row_sets(self, specs: list[_SearchCriterionSpec], row_sets: list[set[int]]) -> set[int]:
        if not row_sets:
            return set()
        result = set(row_sets[0])
        for i in range(1, len(row_sets)):
            glue = specs[i].glue or "and"
            if glue == "or":
                result |= row_sets[i]
            else:
                result &= row_sets[i]
        return result

    def _select_table_rows(self, rows: list[int]) -> bool:
        """Select *rows* in the table view; return False if nothing selected."""
        if not rows:
            return False
        n = self.select_table_rows(rows)
        if n <= 0:
            return False
        view_rows = self._source_rows_to_view_rows(sorted({int(r) for r in rows}))
        if view_rows:
            view_model = self.table.model()
            if view_model is not None:
                anchor_col = 1 if self._table_model.columnCount() > 1 else 0
                idx = view_model.index(view_rows[0], anchor_col)
                sm = self.table.selectionModel()
                if sm is not None and idx.isValid():
                    sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
                    self.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        QTimer.singleShot(
            0,
            lambda: (
                self.activateWindow(),
                self.raise_(),
                self.table.setFocus(Qt.OtherFocusReason),
                self.table.viewport().update(),
            ),
        )
        return True

    def _run_table_search(self) -> None:
        specs = self._collect_search_criteria()
        if specs is None:
            return
        if not specs:
            self.table.clearSelection()
            self.status_label.setText("Search: empty query; selection cleared.")
            return

        row_sets: list[set[int]] = []
        for spec in specs:
            if spec.substructure:
                found = self._find_rows_substructure(spec.term_groups)
                if found is None:
                    return
                row_sets.append(set(found))
            else:
                row_sets.append(
                    set(
                        self._find_rows_text(
                            spec.col,
                            spec.query,
                            partial=spec.partial,
                            case_sensitive=spec.case_sensitive,
                        )
                    )
                )

        combined = sorted(self._combine_search_row_sets(specs, row_sets))
        self.table.clearSelection()
        if not combined:
            self.status_label.setText("Search: no matches.")
            return
        if not self._select_table_rows(combined):
            self.status_label.setText("Search: no visible matches.")
            return

        n_crit = len(specs)
        glue_bits = [s.glue for s in specs[1:] if s.glue]
        if n_crit > 1:
            glue_note = f", {n_crit} criteria ({'/'.join(g.upper() for g in glue_bits)})"
        else:
            glue_note = ""
        self.status_label.setText(
            f"Search: {len(combined)} matching row(s) selected{glue_note}."
        )
