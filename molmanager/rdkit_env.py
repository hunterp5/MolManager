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

"""One-time RDKit runtime tweaks for the desktop app (logging noise, etc.)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CONFIGURED = False


def configure_rdkit_for_desktop_app() -> None:
    """Idempotent: reduce RDKit console spam when molmanager loads."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    try:
        from rdkit import RDLogger

        RDLogger.DisableLog("rdApp.*")
    except Exception:
        logger.debug("RDKit RDLogger tweak skipped", exc_info=True)
