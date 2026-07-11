"""Streaming and multiprocess helpers for large SDF ingest."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from io import BytesIO

from rdkit import Chem

logger = logging.getLogger(__name__)

_MOLBLOCK_TRAILER = "$$$$"


def _prepare_sdf_record_text(block: str) -> str:
    """Normalize a split SDF record for :class:`~rdkit.Chem.ForwardSDMolSupplier`.

    SDWriter emits a blank title line before the program line; some databases use
    whitespace-only titles or padded numeric IDs. Only prepend a title when the
    first line is already the RDKit program line.
    """
    text = (block or "").replace("\r\n", "\n").rstrip()
    if not text:
        return ""
    lines = text.split("\n")
    first_line = lines[0] if lines else ""
    if first_line.lstrip().startswith("RDKit"):
        text = "\n" + text
    return f"{text.rstrip()}\n\n{_MOLBLOCK_TRAILER}\n"


def _parse_sdf_block(block: str) -> Chem.Mol | None:
    """Parse one SDF record (mol block + SD data)."""
    text = _prepare_sdf_record_text(block)
    if not text:
        return None
    payload = BytesIO(text.encode("utf-8"))
    for supplier_cls in (Chem.ForwardSDMolSupplier, Chem.SDMolSupplier):
        try:
            suppl = supplier_cls(payload, sanitize=True, removeHs=False)
            mol = next(iter(suppl), None)
            if mol is not None:
                return mol
        except Exception:
            logger.debug("SDF supplier parse failed", exc_info=True)
        payload.seek(0)
    return None

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
        mol = _parse_sdf_block(block)
        if mol is None:
            out.append(None)
        else:
            out.append(_mol_to_blob(mol))
    return out
