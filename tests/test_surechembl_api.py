"""Unit tests for SureChEMBL client helpers (no network)."""

from __future__ import annotations

import pytest

from chemmanager.surechembl_api import _similarity_options_string


def test_similarity_options_string() -> None:
    assert _similarity_options_string(0.7) == "0.7"
    assert _similarity_options_string(1.0) == "1"
    assert _similarity_options_string(0.5) == "0.5"


def test_similarity_options_out_of_range() -> None:
    with pytest.raises(ValueError):
        _similarity_options_string(-0.1)
    with pytest.raises(ValueError):
        _similarity_options_string(1.1)
