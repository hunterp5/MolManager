"""One-time RDKit runtime tweaks for the desktop app (logging noise, etc.)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CONFIGURED = False


def configure_rdkit_for_desktop_app() -> None:
    """Idempotent: reduce RDKit console spam when ChemManager loads."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    try:
        from rdkit import RDLogger

        RDLogger.DisableLog("rdApp.*")
    except Exception:
        logger.debug("RDKit RDLogger tweak skipped", exc_info=True)
