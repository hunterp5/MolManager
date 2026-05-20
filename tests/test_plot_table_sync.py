"""Tests for table ↔ plot OID index mapping."""


def test_point_indices_for_oids():
    from chemmanager.ui.plotly_interactive_view import PlotlyInteractiveView

    view = PlotlyInteractiveView.__new__(PlotlyInteractiveView)
    view.plotted_oids = [10, 20, 30]
    assert view.point_indices_for_oids({20, 99}) == {1}
    assert view.point_indices_for_oids(set()) == set()
