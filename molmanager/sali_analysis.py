# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Structure–Activity Landscape Index (SALI) pair metrics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SaliPoint:
    """One molecule pair on the SALI landscape plot."""

    pair_index: int
    oid_a: int
    oid_b: int
    similarity: float
    abs_delta: float
    signed_delta: float
    sali: float


def sali_index(
    abs_delta: float,
    similarity: float,
    *,
    eps: float = 1e-9,
) -> float:
    """
    Structure–Activity Landscape Index.

    ``SALI = |Δactivity| / (1 − similarity)``. When similarity is ≥ ``1 − eps``,
    returns a large finite sentinel so near-identical structures with any Δ remain
    visible rather than producing ``inf``.
    """
    d = abs(float(abs_delta))
    sim = float(similarity)
    denom = 1.0 - sim
    if denom <= float(eps):
        return d / float(eps)
    return d / denom


def build_sali_points(
    records: Sequence[tuple[int, float]],
    similarities: Sequence[tuple[int, int, float]],
    *,
    min_similarity: float = 0.0,
    min_activity_difference: float = 0.0,
    max_pairs: int = 0,
) -> list[SaliPoint]:
    """
    Build SALI plot points from activities and pairwise similarities.

    *records* is ``(oid, activity)`` in any order. *similarities* is
    ``(oid_a, oid_b, similarity)`` for unordered pairs. ``max_pairs`` of 0 keeps
    all qualifying pairs; otherwise the highest-SALI pairs are retained.
    """
    act_by_oid = {int(oid): float(act) for oid, act in records}
    min_sim = max(0.0, float(min_similarity))
    min_dact = max(0.0, float(min_activity_difference))
    points: list[SaliPoint] = []
    for idx, (oid_a, oid_b, sim) in enumerate(similarities):
        a, b = int(oid_a), int(oid_b)
        if a == b:
            continue
        if a not in act_by_oid or b not in act_by_oid:
            continue
        sim_f = float(sim)
        if sim_f < min_sim:
            continue
        signed = act_by_oid[b] - act_by_oid[a]
        abs_delta = abs(signed)
        if abs_delta < min_dact:
            continue
        if a > b:
            a, b = b, a
            signed = -signed
        points.append(
            SaliPoint(
                pair_index=idx,
                oid_a=a,
                oid_b=b,
                similarity=sim_f,
                abs_delta=abs_delta,
                signed_delta=float(signed),
                sali=sali_index(abs_delta, sim_f),
            )
        )
    points.sort(key=lambda p: (-p.sali, -p.abs_delta, -p.similarity, p.oid_a, p.oid_b))
    limit = int(max_pairs)
    if limit > 0 and len(points) > limit:
        points = points[:limit]
        for i, p in enumerate(points):
            points[i] = SaliPoint(
                pair_index=i,
                oid_a=p.oid_a,
                oid_b=p.oid_b,
                similarity=p.similarity,
                abs_delta=p.abs_delta,
                signed_delta=p.signed_delta,
                sali=p.sali,
            )
    else:
        points = [
            SaliPoint(
                pair_index=i,
                oid_a=p.oid_a,
                oid_b=p.oid_b,
                similarity=p.similarity,
                abs_delta=p.abs_delta,
                signed_delta=p.signed_delta,
                sali=p.sali,
            )
            for i, p in enumerate(points)
        ]
    return points
