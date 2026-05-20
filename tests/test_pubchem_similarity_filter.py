"""PubChem similarity client-side Tanimoto filtering."""

from __future__ import annotations

from chemmanager.ui.external.pubchem import pubchem_hit_passes_tanimoto_threshold


def test_passes_at_threshold() -> None:
    assert pubchem_hit_passes_tanimoto_threshold(0.85, 0.85) is True


def test_rejects_below_threshold() -> None:
    assert pubchem_hit_passes_tanimoto_threshold(0.84, 0.85) is False


def test_rejects_missing_score() -> None:
    assert pubchem_hit_passes_tanimoto_threshold(None, 0.7) is False
