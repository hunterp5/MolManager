"""In-window table search (column text and substructure across rows)."""

from __future__ import annotations

from PyQt5.QtCore import QItemSelection, QItemSelectionModel, QTimer, Qt
from PyQt5.QtWidgets import QAbstractItemView, QComboBox, QFrame, QMessageBox
from rdkit import Chem


class TableSearchMixin:
    """Uses ``_search_*`` widgets, ``table``, ``headers``, ``_table_model``, ``status_label``,
    and ``_mol_for_structure_row`` from the concrete app / ``TableUIMixin``.
    """

    @staticmethod
    def _search_combo_label_for_header(header: str) -> str:
        if header == "ID_HIDDEN":
            return "Row ID"
        return header or ""

    def _populate_table_search_columns_combo(self) -> None:
        combo: QComboBox = self._search_col_combo
        prev = combo.currentData()
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

    def toggle_table_search_panel(self) -> None:
        panel: QFrame = self._search_panel
        panel.setVisible(not panel.isVisible())
        if panel.isVisible():
            self._populate_table_search_columns_combo()
            self._search_query_edit.setFocus(Qt.ShortcutFocusReason)
            self.status_label.setText(
                "Search: column + text query, or Substructure (SMILES/SMARTS) across all rows; AND/OR; then Find."
            )

    def open_table_search_with_column(self, logical_col: int) -> None:
        """Show the search bar and pre-select a column (e.g. from the header context menu)."""
        if logical_col < 0 or logical_col >= len(self.headers):
            return
        panel: QFrame = self._search_panel
        panel.setVisible(True)
        self._populate_table_search_columns_combo()
        combo: QComboBox = self._search_col_combo
        for j in range(combo.count()):
            if combo.itemData(j) == logical_col:
                combo.setCurrentIndex(j)
                break
        self._search_query_edit.setFocus(Qt.ShortcutFocusReason)
        hname = self.headers[logical_col]
        self.status_label.setText(f'Search: column "{hname}" selected. Enter a query, then Find.')

    @staticmethod
    def _search_split_comma_terms(needle: str) -> list[str]:
        return [p.strip() for p in (needle or "").split(",") if p.strip()]

    def _on_search_substructure_mode_toggled(self, on: bool) -> None:
        """Substructure search ignores plain-text options (column combo still chooses scroll anchor)."""
        self._search_partial_cb.setEnabled(not on)
        self._search_case_sensitive_cb.setEnabled(not on)

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

    def _run_table_search(self) -> None:
        combo: QComboBox = self._search_col_combo
        col = combo.currentData()
        if not self.headers or col is None or (isinstance(col, int) and col < 0):
            self.status_label.setText("Search: no columns loaded.")
            return
        if not isinstance(col, int) or col >= self._table_model.columnCount():
            self._populate_table_search_columns_combo()
            col = combo.currentData()
            if col is None or not isinstance(col, int) or col < 0:
                return
        needle = (self._search_query_edit.text() or "").strip()
        terms = self._search_split_comma_terms(needle)
        if not terms:
            self.table.clearSelection()
            self.status_label.setText("Search: empty query; selection cleared.")
            return
        substructure = self._search_substructure_cb.isChecked()
        match_and = self._search_match_combo.currentData() == "and"
        rows: list[int] = []

        if substructure:
            patterns: list[Chem.Mol] = []
            for t in terms:
                q = self._search_query_pattern_mol(t)
                if q is None:
                    QMessageBox.warning(
                        self,
                        "Search",
                        f"Could not parse term as SMARTS or SMILES: {t!r}",
                    )
                    return
                patterns.append(q)
            for r in range(self._table_model.rowCount()):
                mol = self._mol_for_structure_row(r)
                if mol is None:
                    continue
                try:
                    if match_and:
                        ok = all(mol.HasSubstructMatch(p) for p in patterns)
                    else:
                        ok = any(mol.HasSubstructMatch(p) for p in patterns)
                except Exception:
                    ok = False
                if ok:
                    rows.append(r)
        else:
            case_sensitive = self._search_case_sensitive_cb.isChecked()
            partial = self._search_partial_cb.isChecked()

            def matches(s: str) -> bool:
                raw = s or ""
                if partial:
                    hay = raw
                    if case_sensitive:
                        if match_and:
                            return all(t in hay for t in terms)
                        return any(t in hay for t in terms)
                    slower = hay.lower()
                    tls = [t.lower() for t in terms]
                    if match_and:
                        return all(tl in slower for tl in tls)
                    return any(tl in slower for tl in tls)
                cell = raw.strip()
                if case_sensitive:
                    if match_and:
                        return all(cell == t for t in terms)
                    return any(cell == t for t in terms)
                cl = cell.lower()
                tls = [t.lower() for t in terms]
                if match_and:
                    return all(cl == tl for tl in tls)
                return any(cl == tl for tl in tls)

            for r in range(self._table_model.rowCount()):
                hay = self._table_model.cell_text(r, col)
                if matches(hay):
                    rows.append(r)

        self.table.clearSelection()
        if not rows:
            self.status_label.setText("Search: no matches.")
            return
        nc = self._table_model.columnCount()
        if nc <= 0:
            return
        sm = self.table.selectionModel()
        for i, r in enumerate(rows):
            tl = self._table_model.index(r, 0)
            br = self._table_model.index(r, nc - 1)
            row_sel = QItemSelection(tl, br)
            flags = QItemSelectionModel.ClearAndSelect if i == 0 else QItemSelectionModel.Select
            sm.select(row_sel, flags)
        # Anchor the current cell on a stable visible column so the focus ring is not
        # tied only to the searched column while the whole row stays selected.
        anchor_col = 1 if nc > 1 else 0
        idx = self._table_model.index(rows[0], anchor_col)
        # setCurrentIndex() defaults to ClearAndSelect and would wipe multi-cell row picks.
        sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
        self.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        # Active palette for selection (otherwise focus stays in the search bar and
        # selected rows look like inactive / dimmed "gray" instead of highlighted).
        QTimer.singleShot(
            0,
            lambda: (
                self.activateWindow(),
                self.raise_(),
                self.table.setFocus(Qt.OtherFocusReason),
                self.table.viewport().update(),
            ),
        )
        mode_lbl = "AND" if match_and else "OR"
        nterms = len(terms)
        term_note = f", {nterms} terms ({mode_lbl})" if nterms > 1 else ""
        if substructure:
            self.status_label.setText(
                f"Search: {len(rows)} matching row(s) selected{term_note} (substructure)."
            )
        else:
            case_sensitive = self._search_case_sensitive_cb.isChecked()
            partial = self._search_partial_cb.isChecked()
            match_kind = "partial" if partial else "full"
            case_note = ", case-sensitive" if case_sensitive else ""
            self.status_label.setText(
                f"Search: {len(rows)} matching row(s) selected{term_note} ({match_kind} match{case_note})."
            )
