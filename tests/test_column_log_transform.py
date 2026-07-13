"""Tests for column log10 transform helpers."""

from __future__ import annotations

import math

from molmanager.column_log_transform import (
    column_can_apply_log10,
    format_transformed_number,
    transform_column_values_log10,
)


def test_column_can_apply_log10_requires_positive_numerics() -> None:
    assert column_can_apply_log10(["1", "10", "100"])
    assert column_can_apply_log10(["", "2.5", "N/A"])
    assert not column_can_apply_log10([])
    assert not column_can_apply_log10(["", "abc"])
    assert not column_can_apply_log10(["1", "0"])
    assert not column_can_apply_log10(["1", "-3"])


def test_transform_column_values_log10_roundtrip() -> None:
    src = {1: "100", 2: "10", 3: "", 4: "N/A"}
    logged = transform_column_values_log10(src, to_log=True)
    assert logged[1] == "2"
    assert logged[2] == "1"
    assert 3 not in logged
    assert 4 not in logged

    restored = transform_column_values_log10({**src, **logged}, to_log=False)
    assert float(restored[1]) == 100.0
    assert float(restored[2]) == 10.0


def test_format_transformed_number_handles_edge_cases() -> None:
    assert format_transformed_number(0.0) == "0"
    assert format_transformed_number(math.inf) == ""
    assert format_transformed_number(1.25) == "1.25"
