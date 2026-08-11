"""Tests for Tools → Random → Number generation helpers."""

import pytest

from molmanager.random_numbers import RandomNumberParams, generate_random_values


def test_uniform_reproducible_with_seed():
    p = RandomNumberParams(distribution="uniform", low=0.0, high=1.0, seed=42, decimals=4)
    a = generate_random_values(5, p)
    b = generate_random_values(5, p)
    assert a == b
    assert len(a) == 5
    for text in a:
        v = float(text)
        assert 0.0 <= v < 1.0


def test_integer_inclusive_range():
    p = RandomNumberParams(distribution="integer", low=2, high=2, seed=1)
    vals = generate_random_values(10, p)
    assert vals == ["2"] * 10


def test_integer_rejects_inverted_range():
    p = RandomNumberParams(distribution="integer", low=5, high=1)
    with pytest.raises(ValueError, match="Maximum"):
        generate_random_values(3, p)


def test_normal_clip():
    p = RandomNumberParams(
        distribution="normal",
        low=0.0,
        high=1.0,
        mean=0.5,
        std=10.0,
        seed=7,
        clip_normal=True,
        decimals=3,
    )
    vals = [float(x) for x in generate_random_values(50, p)]
    assert all(0.0 <= v <= 1.0 for v in vals)


def test_normal_requires_positive_std():
    p = RandomNumberParams(distribution="normal", low=0.0, high=1.0, mean=0.0, std=0.0)
    with pytest.raises(ValueError, match="Standard deviation"):
        generate_random_values(1, p)


def test_empty_count():
    p = RandomNumberParams(distribution="uniform", low=0.0, high=1.0, seed=0)
    assert generate_random_values(0, p) == []
