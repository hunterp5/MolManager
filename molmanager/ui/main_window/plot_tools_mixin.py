"""Plot docking, plot↔table sync, and plot panel UI."""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QMessageBox,
    QVBoxLayout,
)



logger = logging.getLogger(__name__)

class PlotToolsMixin:
    def _sync_dialog_only_selected_scope(self, dialog: QDialog) -> None:
        """Refresh a tool dialog's scope checkbox label/count from the current table selection."""
        cb = getattr(dialog, "only_selected_cb", None)
        if cb is None:
            return
        try:
            from PyQt5 import sip

            if sip.isdeleted(cb):
                return
        except Exception:
            pass
        prefix = getattr(dialog, "_only_selected_scope_prefix", "Only selected rows")
        n = len(self._selected_logical_rows())
        try:
            if n > 0:
                cb.setEnabled(True)
                cb.setText(f"{prefix} ({n} row(s))")
            else:
                cb.setEnabled(False)
                cb.setChecked(False)
                cb.setText(prefix)
        except RuntimeError:
            return

    def _prepare_tool_dialog(self, dialog: QDialog) -> None:
        """Let the main table stay interactive and keep scope UI in sync while the dialog is open."""
        dialog.setModal(False)
        dialog.setWindowModality(Qt.NonModal)
        self._attach_tool_scope_sync(dialog, on_finished_signal=dialog.finished)

    def _prepare_tool_plot(self, plot_widget) -> None:
        """Keep docked plot scope UI in sync with table selection changes."""
        self._attach_tool_scope_sync(plot_widget, on_finished_signal=plot_widget.destroyed)

    def _iter_active_plot_selection_views(self) -> list:
        """Plot surfaces that mirror table row selection (dock, floating plotter, PCA/t-SNE)."""
        from ..dockable_plot import iter_plot_selection_views

        views: list = []
        docked = getattr(self, "_docked_plot_widget", None)
        if docked is not None:
            views.extend(iter_plot_selection_views(docked))
        for plot_dlg in self._iter_plot_dialogs():
            pw = getattr(plot_dlg, "_plot_widget", None)
            if pw is not None:
                views.extend(iter_plot_selection_views(pw))
        for attr in (
            "_pca_dialog",
            "_tsne_dialog",
            "_umap_dialog",
            "_boiled_egg_dialog",
            "_golden_triangle_dialog",
            "_radar_plot_dialog",
        ):
            dlg = getattr(self, attr, None)
            if dlg is None:
                continue
            panel = getattr(dlg, "_panel", None)
            if panel is not None:
                views.extend(iter_plot_selection_views(panel))
                if callable(getattr(panel, "refresh_spoke_columns", None)):
                    views.append(panel)
                continue
            views.extend(iter_plot_selection_views(dlg))
        return views

    def _refresh_active_plot_axis_columns(self) -> None:
        """Update plotter axis dropdowns when table columns or numeric bounds change."""
        for view in self._iter_active_plot_selection_views():
            refresh = getattr(view, "refresh_axis_columns", None) or getattr(
                view, "refresh_spoke_columns", None
            )
            if callable(refresh):
                try:
                    refresh()
                except RuntimeError:
                    pass

    def _sync_active_plots_from_table_selection(self) -> None:
        for view in self._iter_active_plot_selection_views():
            try:
                sync = getattr(view, "sync_from_table_selection", None)
                if callable(sync):
                    sync()
            except RuntimeError:
                pass

    def _schedule_sync_active_plots_from_table_selection(self) -> None:
        timer = getattr(self, "_plot_table_sync_timer", None)
        if timer is None:
            return
        timer.start(40)

    def _replot_active_plots(self) -> None:
        """Refresh plot data after filters or table edits change visible rows."""
        for view in self._iter_active_plot_selection_views():
            schedule = getattr(view, "_schedule_plot", None)
            if callable(schedule):
                try:
                    schedule()
                except RuntimeError:
                    pass

    def _schedule_active_plots_replot(self, *, delay_ms: int = 80) -> None:
        if getattr(self, "_background_job_ui_active", None) and self._background_job_ui_active():
            return
        timer = getattr(self, "_plot_replot_timer", None)
        if timer is None:
            return
        timer.start(max(0, int(delay_ms)))

    def _prune_plot_dialogs(self) -> None:
        alive: list = []
        for dlg in getattr(self, "_plot_dialogs", []):
            try:
                dlg.isVisible()
                alive.append(dlg)
            except RuntimeError:
                pass
        self._plot_dialogs = alive

    def _iter_plot_dialogs(self) -> list:
        self._prune_plot_dialogs()
        return list(self._plot_dialogs)

    def _register_plot_dialog(self, dlg) -> None:
        """Track a floating plotter window (multiple instances allowed)."""
        if not hasattr(self, "_plot_dialogs"):
            self._plot_dialogs = []
        self._prune_plot_dialogs()
        self._plot_dialogs.append(dlg)
        n = len(self._plot_dialogs)
        dlg.setWindowTitle("Plot Data" if n == 1 else f"Plot Data ({n})")
        dlg.destroyed.connect(lambda *_a, d=dlg: self._unregister_plot_dialog(d))

    def _unregister_plot_dialog(self, dlg) -> None:
        try:
            self._plot_dialogs.remove(dlg)
        except (ValueError, AttributeError):
            pass
        self._prune_plot_dialogs()

    def _create_plot_dialog(self):
        from ..plot import PlotDialog

        d = PlotDialog(self)
        self._prepare_tool_dialog(d)
        return d

    def _attach_tool_scope_sync(self, target, *, on_finished_signal) -> None:
        """Wire table selection changes to a dialog/plot ``only_selected_cb`` until teardown."""
        if getattr(target, "only_selected_cb", None) is None:
            return
        prior = getattr(target, "_scope_sync_disconnect", None)
        if callable(prior):
            prior()
        self._sync_dialog_only_selected_scope(target)
        sm = self.table.selectionModel()
        if sm is None:
            return

        def on_sel_changed(*_args):
            self._sync_dialog_only_selected_scope(target)
            self._schedule_sync_active_plots_from_table_selection()

        sm.selectionChanged.connect(on_sel_changed)

        def teardown(*_args):
            try:
                from PyQt5 import sip

                if sm is not None and not sip.isdeleted(sm):
                    sm.selectionChanged.disconnect(on_sel_changed)
            except (TypeError, RuntimeError):
                pass
            target._scope_sync_disconnect = None

        on_finished_signal.connect(teardown)
        target._scope_sync_disconnect = teardown

    def dock_plot_widget(self, plot_widget) -> bool:
        """Move a plot widget into the main-window panel beside the table."""
        from ..dockable_plot import is_dockable_plot_widget
        from ..plot import PlotWidget

        if not is_dockable_plot_widget(plot_widget) and not isinstance(plot_widget, PlotWidget):
            return False
        existing = getattr(self, "_docked_plot_widget", None)
        if existing is not None and existing is not plot_widget:
            try:
                QMessageBox.information(
                    self,
                    "Plot",
                    "A plot is already docked in the main window. Close it from the plot panel first.",
                )
            except RuntimeError:
                self._docked_plot_widget = None
            else:
                return False

        host = self._plot_panel_host
        lay = host.layout()
        if lay is None:
            lay = QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
        else:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        lay.addWidget(plot_widget, 1)
        self._docked_plot_widget = plot_widget
        self._plot_panel.setVisible(True)
        prior_teardown = getattr(plot_widget, "_scope_sync_disconnect", None)
        if callable(prior_teardown):
            prior_teardown()
        self._prepare_tool_plot(plot_widget)
        plot_widget.destroyed.connect(self._on_docked_plot_destroyed)
        self._sync_active_plots_from_table_selection()
        self.status_label.setText("Plot: docked to the right of the table.")
        return True

    def _on_docked_plot_destroyed(self, *_args) -> None:
        self._docked_plot_widget = None
        try:
            self._plot_panel.setVisible(False)
        except Exception:
            pass

    def close_plot_panel_keep_plot(self) -> None:
        """Hide the docked plot panel; the plot widget and its state are preserved."""
        self._plot_panel.setVisible(False)
        self.status_label.setText("Plot panel hidden.")

    def _release_plot_widget_from_panel_host(self, plot_widget) -> None:
        host = self._plot_panel_host
        lay = host.layout()
        if lay is not None:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        teardown = getattr(plot_widget, "_scope_sync_disconnect", None)
        if callable(teardown):
            teardown()
        self._docked_plot_widget = None

    def undock_plot_to_window(self) -> bool:
        """Move the docked plot from the main window into a floating window."""
        from ..plot import PlotDialog, PlotWidget

        plot_widget = getattr(self, "_docked_plot_widget", None)
        if plot_widget is None:
            return False

        factory = getattr(plot_widget, "create_floating_dialog", None)
        if callable(factory):
            self._release_plot_widget_from_panel_host(plot_widget)
            self._plot_panel.setVisible(False)
            dlg = factory(self)
            self._prepare_tool_dialog(dlg)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            self.status_label.setText("Plot: moved to separate window.")
            return True

        if not isinstance(plot_widget, PlotWidget):
            self._docked_plot_widget = None
            return False

        self._release_plot_widget_from_panel_host(plot_widget)
        self._plot_panel.setVisible(False)

        dlg = PlotDialog(self, plot_widget=plot_widget)
        self._register_plot_dialog(dlg)
        self._prepare_tool_dialog(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self.status_label.setText("Plot: moved to separate window.")
        return True

    def toggle_plot_panel(self) -> None:
        """Show/hide the docked plot panel, or open the floating plotter if none is docked."""
        w = getattr(self, "_docked_plot_widget", None)
        if w is None:
            self.open_plot()
            return
        try:
            visible = self._plot_panel.isVisible()
            self._plot_panel.setVisible(not visible)
            if not visible:
                self._sync_dialog_only_selected_scope(w)
        except RuntimeError:
            self._docked_plot_widget = None
            self.open_plot()
