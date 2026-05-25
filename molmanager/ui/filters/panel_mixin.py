"""Filter panel UI, global numeric bounds sync, and table row visibility filtering."""

from __future__ import annotations

import logging
from contextlib import nullcontext

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox

from ...config import MolManagerConfig, load_config
from ...utils import mol_to_canonical_smiles, safe_float
from ...workers import SubstructureFilterWorker
from .cards import CategoryFilterCard, FilterCard, SubstructureFilterCard, TextFilterCard

logger = logging.getLogger(__name__)


class FilterPanelMixin:
    """Expects ``headers``, ``_table_model``, ``table``, ``mols``, ``filters``, ``f_panel``,
    ``f_container``, ``global_bounds``, ``status_label``, ``threadpool``, ``_apply_filters_timer``,
    and optional ``_substructure_filter_signals`` on ``self`` (provided by ``ChemicalTableApp``).
    """

    def _clear_filter_target_smiles_cache(self) -> None:
        """Drop cached MolToSmiles results (e.g. after wholesale ``mols`` replacement)."""
        self._filter_target_smiles_cache = None
        self._substructure_target_mol_cache = {}

    def _smiles_for_substructure_target(self, oid: int, mol) -> str:
        """Stable SMILES string for filter worker targets; cache per (oid, mol object id)."""
        if mol is None:
            return ""
        cache = getattr(self, "_filter_target_smiles_cache", None)
        if cache is None:
            cache = {}
            self._filter_target_smiles_cache = cache
        mid = id(mol)
        t = cache.get(oid)
        if t is not None and t[0] == mid:
            return t[1]
        smi = mol_to_canonical_smiles(mol)
        cache[oid] = (mid, smi)
        return smi

    def _filterable_data_column_names(self) -> list[str]:
        return [h for h in self.headers[2:] if h and not self._table_model.is_pixmap_data_column(h)]

    def _sqlite_filter_matched_oids(self) -> frozenset[int] | None:
        """Try SQL pushdown for simple enabled filters; return None when unsupported."""
        ensure_sqlite = getattr(self, "_ensure_sqlite_store_current", None)
        if callable(ensure_sqlite) and not ensure_sqlite():
            return None
        store = getattr(self, "_sqlite_store", None)
        if store is None:
            return None
        where_parts: list[str] = []
        args: list[object] = []
        for f in self.filters:
            if isinstance(f, SubstructureFilterCard):
                return None
            if isinstance(f, CategoryFilterCard):
                if not f.filter_enabled():
                    continue
                prop = f.column_name()
                if not prop or prop not in self.headers:
                    continue
                checked = f.checked_values()
                qp = str(prop).replace('"', '""')
                if not checked:
                    where_parts.append("0")
                else:
                    placeholders = ", ".join(["?"] * len(checked))
                    where_parts.append(f'"{qp}" IN ({placeholders})')
                    args.extend(sorted(checked))
                continue
            if isinstance(f, FilterCard):
                cfg = f.get_cfg()
                if not cfg.get("enabled", True):
                    continue
                prop = cfg.get("p")
                if not prop or prop not in self.headers:
                    continue
                qp = str(prop).replace('"', '""')
                lo = float(cfg.get("min", 0.0))
                hi = float(cfg.get("max", 0.0))
                if cfg.get("inverted", False):
                    where_parts.append(f'(CAST("{qp}" AS REAL) < ? OR CAST("{qp}" AS REAL) > ?)')
                else:
                    where_parts.append(f'(CAST("{qp}" AS REAL) >= ? AND CAST("{qp}" AS REAL) <= ?)')
                args.extend([lo, hi])
                continue
            if isinstance(f, TextFilterCard):
                cfg = f.get_cfg()
                if not cfg.get("enabled", True):
                    continue
                prop = cfg.get("p")
                needle = str(cfg.get("text", "") or "").strip()
                if not prop or not needle:
                    continue
                qp = str(prop).replace('"', '""')
                case_sensitive = bool(cfg.get("case_sensitive", False))
                partial = bool(cfg.get("partial_match", True))
                inverted = bool(cfg.get("inverted", False))
                from ..search_query import sqlite_text_match_clause

                expr, match_args = sqlite_text_match_clause(
                    qp, needle, partial=partial, case_sensitive=case_sensitive
                )
                where_parts.append(
                    f"(NOT ({expr}))" if inverted else f"({expr})"
                )
                args.extend(match_args)
                continue
            return None
        if not where_parts:
            return None
        where_sql = " AND ".join(where_parts)
        total = store.count(where_sql=where_sql, args=tuple(args))
        page = max(1000, int(getattr(load_config(), "sqlite_backend_page_size", 5000)))
        out: set[int] = set()
        offset = 0
        while offset < total:
            recs = store.fetch_page(limit=page, offset=offset, where_sql=where_sql, args=tuple(args), sort_by="oid")
            if not recs:
                break
            out.update(int(oid) for oid, _ in recs)
            offset += len(recs)
        return frozenset(out)

    def calculate_global_bounds(self):
        self.global_bounds = self._table_model.numeric_bounds_by_column()
        data_cols = self._filterable_data_column_names()
        for f in self.filters:
            if isinstance(f, FilterCard):
                f.update_prop_list(list(self.global_bounds.keys()))
            elif isinstance(f, (TextFilterCard, CategoryFilterCard)):
                f.update_prop_list(data_cols)
        refresh_plot_axes = getattr(self, "_refresh_active_plot_axis_columns", None)
        if callable(refresh_plot_axes):
            refresh_plot_axes()

    def _filters_include_substructure(self) -> bool:
        return any(
            isinstance(f, SubstructureFilterCard) and f.filter_enabled() for f in self.filters
        )

    def apply_filters(self) -> None:
        """Coalesce expensive filter passes on large tables (slider drags)."""
        n = self._table_model.rowCount()
        cfg = load_config()
        # Substructure matching runs RDKit per row — debounce earlier and slightly longer while typing SMARTS.
        if self._filters_include_substructure():
            threshold, delay_ms = cfg.filter_debounce_substructure_rows, cfg.filter_debounce_substructure_ms
        else:
            threshold, delay_ms = cfg.filter_debounce_default_rows, cfg.filter_debounce_default_ms
        if n < threshold:
            self._apply_filters_impl(cfg)
            return
        self._apply_filters_timer.stop()
        self._apply_filters_timer.start(delay_ms)

    def _invalidate_substructure_async_jobs(self) -> None:
        """Drop in-flight substructure jobs (completion handler will no-op)."""
        self._substructure_job_gen = int(getattr(self, "_substructure_job_gen", 0)) + 1
        self._substructure_job_smarts = None

    def _substructure_filter_targets(self) -> list[tuple[int, str]]:
        """(oid, smiles) per row — RDKit parsing runs in ``SubstructureFilterWorker``."""
        targets: list[tuple[int, str]] = []
        smiles_col = self.headers.index("SMILES") if "SMILES" in self.headers else None
        for r in range(self._table_model.rowCount()):
            oid = self._table_model.row_oid(r)
            smi = ""
            if smiles_col is not None:
                smi = (self._table_model.cell_text(r, smiles_col) or "").strip()
            if not smi:
                mol = self.mols.get(oid)
                if mol is None:
                    mol = self._mol_for_structure_row(r)
                if mol is not None:
                    smi = self._smiles_for_substructure_target(oid, mol)
            targets.append((oid, smi))
        return targets

    def _on_substructure_filter_finished(self, job_gen: int, matched) -> None:
        if job_gen != getattr(self, "_substructure_job_gen", 0):
            return
        dispatched = getattr(self, "_substructure_job_smarts", None) or ""
        ss_cards = [f for f in self.filters if isinstance(f, SubstructureFilterCard) and f.filter_enabled()]
        if len(ss_cards) != 1:
            self._apply_filters_impl_sync(None)
            return
        card = ss_cards[0]
        current = (card.smarts_edit.text() or "").strip()
        if current != dispatched:
            self.apply_filters()
            return
        oids = matched if isinstance(matched, frozenset) else frozenset()
        self._apply_filters_impl_sync((dispatched, oids))

    def _on_substructure_filter_failed(self, job_gen: int, msg: str) -> None:
        if job_gen != getattr(self, "_substructure_job_gen", 0):
            return
        logger.warning("Substructure filter job failed: %s", msg)
        self._invalidate_substructure_async_jobs()
        self._apply_filters_impl_sync(None)

    def _apply_substructure_override_to_visible(
        self,
        base: frozenset[int],
        override_smarts: str,
        override_oids: frozenset[int],
    ) -> set[int]:
        for f in self.filters:
            if not isinstance(f, SubstructureFilterCard) or not f.filter_enabled():
                continue
            if override_smarts != (f.smarts_edit.text() or "").strip():
                continue
            inv = f.filter_inverted()
            if inv:
                return {oid for oid in base if oid not in override_oids}
            return {oid for oid in base if oid in override_oids}
        return set(base)

    def _apply_filters_impl_sync(self, substructure_matches: tuple[str, frozenset] | None) -> None:
        """Apply all filters on the UI thread. If ``substructure_matches`` is ``(smarts, oids)``, use that for the matching SMARTS."""
        n_rows = self._table_model.rowCount()
        proxy = self._filter_proxy_model
        perf = getattr(self, "_perf", None)
        scope = perf.track if perf is not None else (lambda *_args, **_kwargs: nullcontext())
        if not self.filters:
            with scope("filters.apply_sync"):
                proxy.set_visible_oids(None)
            self.status_label.setText(f"Showing {n_rows} / {len(self.mols)} molecules")
            return

        override_smarts = substructure_matches[0] if substructure_matches else None
        override_oids = substructure_matches[1] if substructure_matches else None
        sqlite_oids = self._sqlite_filter_matched_oids()

        h_map = {h: i for i, h in enumerate(self.headers)}
        vis = 0
        visible_oids: set[int] = set()
        table = getattr(self, "table", None)
        if table is not None:
            table.setUpdatesEnabled(False)
        try:
            with scope("filters.apply_sync"):
                if sqlite_oids is not None and override_oids is None:
                    visible_oids = set(sqlite_oids)
                    vis = len(visible_oids)
                elif sqlite_oids is not None and override_oids is not None and override_smarts is not None:
                    visible_oids = self._apply_substructure_override_to_visible(
                        sqlite_oids, override_smarts, override_oids
                    )
                    vis = len(visible_oids)
                elif (
                    override_oids is not None
                    and override_smarts is not None
                    and sqlite_oids is None
                    and len([f for f in self.filters if f.filter_enabled()]) == 1
                ):
                    for f in self.filters:
                        if isinstance(f, SubstructureFilterCard) and f.filter_enabled():
                            inv = f.filter_inverted()
                            visible_oids = (
                                {oid for oid in self.mols if oid not in override_oids}
                                if inv
                                else set(override_oids)
                            )
                            vis = len(visible_oids)
                            break
                else:
                    for r in range(n_rows):
                        hide = False
                        oid = self._table_model.row_oid(r)
                        if sqlite_oids is not None:
                            hide = oid not in sqlite_oids
                        for f in ([] if sqlite_oids is not None else self.filters):
                            if isinstance(f, SubstructureFilterCard):
                                if not f.filter_enabled():
                                    continue
                                inv = f.filter_inverted()
                                if (
                                    override_smarts is not None
                                    and override_oids is not None
                                    and override_smarts == (f.smarts_edit.text() or "").strip()
                                ):
                                    matched = oid in override_oids
                                    if inv:
                                        if matched:
                                            hide = True
                                            break
                                    elif not matched:
                                        hide = True
                                        break
                                    continue
                                mol = self.mols.get(oid)
                                matched = f.match_mol(mol)
                                if inv:
                                    if matched:
                                        hide = True
                                        break
                                elif not matched:
                                    hide = True
                                    break
                                continue
                            if isinstance(f, TextFilterCard):
                                if not f.filter_enabled():
                                    continue
                                if not f.row_matches(r):
                                    hide = True
                                    break
                                continue
                            if isinstance(f, CategoryFilterCard):
                                if not f.filter_enabled():
                                    continue
                                if not f.row_matches(r):
                                    hide = True
                                    break
                                continue
                            if not isinstance(f, FilterCard):
                                continue
                            cfg = f.get_cfg()
                            if not cfg.get("enabled", True):
                                continue
                            prop = cfg.get("p")
                            if not prop or prop not in h_map:
                                continue
                            v = safe_float(self._table_model.value_for_header(r, prop))
                            if v is None:
                                hide = True
                                break
                            lo, hi = cfg["min"], cfg["max"]
                            inside = lo <= v <= hi
                            if cfg.get("inverted", False):
                                if inside:
                                    hide = True
                                    break
                            elif not inside:
                                hide = True
                                break
                        if not hide:
                            visible_oids.add(oid)
                            vis += 1
                proxy.set_visible_oids(frozenset(visible_oids))
        finally:
            if table is not None:
                table.setUpdatesEnabled(True)
        invalid_smarts_msg = None
        for f in self.filters:
            if isinstance(f, SubstructureFilterCard):
                sm = (f.smarts_edit.text() or "").strip()
                if sm and f._compiled_query() is None:
                    invalid_smarts_msg = "Substructure filter: invalid SMARTS."
                    break
        if invalid_smarts_msg:
            self.status_label.setText(invalid_smarts_msg)
        else:
            self.status_label.setText(f"Showing {vis} / {len(self.mols)} molecules")

    def _apply_filters_impl(self, cfg: MolManagerConfig | None = None) -> None:
        if cfg is None:
            cfg = load_config()
        n_rows = self._table_model.rowCount()
        if self.filters and not self._filters_include_substructure():
            ensure_sqlite = getattr(self, "_ensure_sqlite_store_current", None)
            if callable(ensure_sqlite) and not ensure_sqlite():
                self._sqlite_rebuild_pending_filters = True
                return
        if not self.filters:
            self._invalidate_substructure_async_jobs()
            self._apply_filters_impl_sync(None)
            return

        ss_cards = [f for f in self.filters if isinstance(f, SubstructureFilterCard) and f.filter_enabled()]
        thresh = cfg.substructure_async_rows

        if len(ss_cards) != 1:
            self._invalidate_substructure_async_jobs()
            self._apply_filters_impl_sync(None)
            return

        card = ss_cards[0]
        smarts = (card.smarts_edit.text() or "").strip()
        if not smarts:
            self._invalidate_substructure_async_jobs()
            self._apply_filters_impl_sync(None)
            return

        if n_rows < thresh:
            self._invalidate_substructure_async_jobs()
            self._apply_filters_impl_sync(None)
            return

        if card._compiled_query() is None:
            self._invalidate_substructure_async_jobs()
            self._apply_filters_impl_sync(None)
            return

        self._substructure_job_gen = int(getattr(self, "_substructure_job_gen", 0)) + 1
        gen = self._substructure_job_gen
        self._substructure_job_smarts = smarts
        perf = getattr(self, "_perf", None)
        scope = perf.track if perf is not None else (lambda *_args, **_kwargs: nullcontext())
        with scope("filters.substructure_targets"):
            targets = self._substructure_filter_targets()
        self.status_label.setText(f"Filtering substructure… ({n_rows} rows)")
        sigs = getattr(self, "_substructure_filter_signals", None)
        if sigs is None:
            self._apply_filters_impl_sync(None)
            return
        self.threadpool.start(SubstructureFilterWorker(gen, smarts, targets, sigs))

    def add_filter_card(self, initial_property: str | None = None):
        if isinstance(initial_property, bool):
            initial_property = None
        if not self.headers:
            return
        props = list(self.global_bounds.keys())
        if not props:
            props = ["SMILES"]
        init = initial_property if initial_property and initial_property in props else None
        self.f_panel.setVisible(True)
        card = FilterCard(props, self, initial_property=init)
        card.changed.connect(self.apply_filters)
        card.removed.connect(lambda c: self.remove_filter(c))
        self.f_container.addWidget(card)
        self.filters.append(card)
        self.apply_filters()
        self._sync_filter_panel_scroll_content()

    def add_text_filter_card(self, initial_column: str | None = None):
        if isinstance(initial_column, bool):
            initial_column = None
        if not self.headers:
            return
        cols = self._filterable_data_column_names()
        if not cols:
            QMessageBox.warning(self, "Add Filter", "No text columns are available.")
            return
        self.f_panel.setVisible(True)
        card = TextFilterCard(cols, self)
        card.changed.connect(self.apply_filters)
        card.removed.connect(lambda c: self.remove_filter(c))
        self.f_container.addWidget(card)
        self.filters.append(card)
        if initial_column and card.cb.findText(initial_column) >= 0:
            card.set_column(initial_column)
        self.apply_filters()
        self._sync_filter_panel_scroll_content()

    def add_category_filter_card(self, initial_column: str | None = None):
        if isinstance(initial_column, bool):
            initial_column = None
        if not self.headers:
            return
        cols = self._filterable_data_column_names()
        if not cols:
            QMessageBox.warning(self, "Add Filter", "No text columns are available.")
            return
        self.f_panel.setVisible(True)
        card = CategoryFilterCard(cols, self)
        card.changed.connect(self.apply_filters)
        card.removed.connect(lambda c: self.remove_filter(c))
        self.f_container.addWidget(card)
        self.filters.append(card)
        if initial_column and card.cb.findText(initial_column) >= 0:
            card.set_column(initial_column)
        self.apply_filters()
        self._sync_filter_panel_scroll_content()

    def add_substructure_filter_card(self, *_args, **_kwargs):
        if not self.headers:
            return
        self.f_panel.setVisible(True)
        card = SubstructureFilterCard()
        card.changed.connect(self.apply_filters)
        card.removed.connect(lambda c: self.remove_filter(c))
        self.f_container.addWidget(card)
        self.filters.append(card)
        self.apply_filters()
        self._sync_filter_panel_scroll_content()

    def remove_filter(self, card):
        if card in self.filters:
            self.filters.remove(card)
            card.deleteLater()
            self.apply_filters()
            self._sync_filter_panel_scroll_content()

    def delete_all_filters_from_panel(self) -> None:
        """Remove every filter card from the panel (same as deleting each card)."""
        for card in list(self.filters):
            self.remove_filter(card)

    def _sync_filter_panel_scroll_content(self) -> None:
        """Refresh scroll-area geometry after cards are added or the panel is shown."""
        scroll = getattr(self, "_filter_scroll", None)
        if scroll is None:
            return
        clamp = getattr(scroll, "_clamp_host_width", None)
        if callable(clamp):
            clamp()
        host = getattr(self, "_filter_cards_host", None)
        if host is not None:
            host.updateGeometry()
        scroll.updateGeometry()

    def toggle_filter_panel(self) -> None:
        """Show or hide the filter panel and resync card width when opening."""
        self.f_panel.setVisible(not self.f_panel.isVisible())
        if self.f_panel.isVisible():
            QTimer.singleShot(0, self._sync_filter_panel_scroll_content)

    def disable_all_filters_keep_panel(self) -> None:
        """Turn off every filter card but leave the filter panel open."""
        for f in self.filters:
            f.restore_filter_flags(enabled=False, inverted=f.filter_inverted())
        self.apply_filters()

    def close_filter_panel_and_disable_filters(self) -> None:
        """Hide the filter panel and turn off every filter card (cards remain; re-enable with On)."""
        self.disable_all_filters_keep_panel()
        self.f_panel.setVisible(False)

    def close_filter_panel_keep_filters(self) -> None:
        """Hide the filter panel only; active filters keep affecting the table."""
        self.f_panel.setVisible(False)
