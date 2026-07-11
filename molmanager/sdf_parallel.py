"""Streaming and multiprocess helpers for large SDF ingest."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from io import BytesIO

from rdkit import Chem

logger = logging.getLogger(__name__)

_MOLBLOCK_TRAILER = "$$$$"


def _prepare_sdf_record_text(block: str) -> str:
    """Normalize a split SDF record for :class:`~rdkit.Chem.ForwardSDMolSupplier`."""
    text = (block or "").replace("\r\n", "\n").rstrip()
    if not text:
        return ""
    first_nonempty = next((line for line in text.split("\n") if line.strip()), "")
    if first_nonempty.lstrip().startswith("RDKit") or (
        first_nonempty.startswith(" ") and "V2000" not in first_nonempty
    ):
        text = "\n" + text.lstrip("\n")
    return f"{text.rstrip()}\n\n{_MOLBLOCK_TRAILER}\n"

try:
    _PROP_FLAGS = int(Chem.PropertyPickleOptions.AllProps)
except Exception:  # pragma: no cover - older RDKit
    _PROP_FLAGS = None


def _mol_to_blob(mol: Chem.Mol) -> bytes | None:
    try:
        if _PROP_FLAGS is not None:
            return mol.ToBinary(_PROP_FLAGS)
        return mol.ToBinary()
    except Exception:
        try:
            return mol.ToBinary()
        except Exception:
            return None


def iter_sdf_molblocks(path: str) -> Iterator[str]:
    """Yield each SDF record (mol block + SD data) without the trailing ``$$$$`` line."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        lines: list[str] = []
        for line in fh:
            if line.rstrip("\r\n") == _MOLBLOCK_TRAILER:
                if lines:
                    yield "".join(lines)
                    lines = []
                continue
            lines.append(line)
        if lines:
            yield "".join(lines)


def mp_parse_sdf_molblocks(molblocks: list[str]) -> list[bytes | None]:
    """Parse SDF records in a child process; return pickled mol bytes (or None)."""
    out: list[bytes | None] = []
    for block in molblocks:
        mol = None
        try:
            text = _prepare_sdf_record_text(block)
            if text:
                suppl = Chem.ForwardSDMolSupplier(BytesIO(text.encode("utf-8")), sanitize=True, removeHs=False)
                mol = next(iter(suppl), None)
        except Exception:
            logger.debug("SDF mol block parse failed", exc_info=True)
            mol = None
        if mol is None:
            out.append(None)
        else:
            out.append(_mol_to_blob(mol))
    return out
