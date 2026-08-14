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

"""Shared helpers for chemistry tool workers."""

from __future__ import annotations

from .signals import WorkerSignals


def emit_tool_progress_throttled(
    signals: WorkerSignals,
    message: str,
    done: int,
    tot: int,
    state: list,
    *,
    progress_state=None,
) -> None:
    """Limit ``tool_progress`` emissions; always refresh ``ToolProgressState`` when provided."""
    from ..tool_progress import report_tool_progress

    report_tool_progress(
        message=message,
        done=done,
        total=tot,
        progress_state=progress_state,
        signals=signals,
        throttle=state,
    )
