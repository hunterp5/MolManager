"""Pack / unpack conformer ensembles for the ``confs`` table column and 3D viewer."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from rdkit import Chem

logger = logging.getLogger(__name__)

CONFS_PACK_VERSION = 1
# Lightweight cell: metadata only; coordinate payload lives in app ``confs_sidecar`` (see demote/rehydrate).
CONFS_PACK_SIDECAR_VERSION = 2
# Compact JSON-only summary (errors, counts without coordinates).
CONFS_CELL_JSON_MAX = 2000
# Packed cell (meta + base64 mol blocks) upper bound; truncate conformers if exceeded.
CONFS_CELL_PACK_MAX_CHARS = 950_000

_META_KEYS = (
    "ok",
    "n_requested",
    "n_embedded",
    "n_kept",
    "ewin_kcal",
    "ff",
    "e_min_kcal",
    "e_max_kept_kcal",
    "seed",
    "err",
    "truncated",
    "n_packed",
    # superpose / generic tool metadata
    "op",
    "ref_cid",
    "ref_clamped",
    "n_conf",
    "rms_mean",
    "rms_max",
    "heavy",
    "reflect",
    "max_align_iters",
    "n_align_atoms",
    "align_smarts",
    "align_pattern",
)


def format_confs_table_cell(meta: dict) -> str:
    """Single-line JSON for the ``confs`` table cell when no multi-conformer payload is stored."""
    d = {k: meta[k] for k in _META_KEYS if k in meta}
    try:
        s = json.dumps(d, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        s = '{"ok":false,"err":"serialize"}'
    if len(s) > CONFS_CELL_JSON_MAX:
        s = s[: CONFS_CELL_JSON_MAX - 1] + "…"
    return s


def conformer_mol_blocks_b64_json(mol: Chem.Mol) -> str:
    """Base64(JSON list of base64(mol block per conformer)), same encoding as the 3Dmol viewer."""
    try:
        m = Chem.Mol(mol)
    except Exception:
        return base64.b64encode(json.dumps([]).encode("utf-8")).decode("ascii")
    blocks: list[str] = []
    try:
        ids = sorted(c.GetId() for c in m.GetConformers())
    except Exception:
        ids = list(range(int(m.GetNumConformers())))
    for cid in ids:
        try:
            block = Chem.MolToMolBlock(m, confId=int(cid))
            blocks.append(base64.b64encode(block.encode("utf-8")).decode("ascii"))
        except Exception:
            continue
    payload = json.dumps(blocks).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def pack_confs_cell(meta: dict, mol: Chem.Mol | None, *, max_chars: int = CONFS_CELL_PACK_MAX_CHARS) -> str:
    """
    Store generation metadata plus, when possible, all conformers as mol blocks for later 3D viewing.

    Falls back to :func:`format_confs_table_cell` when there is no multi-conformer molecule or packing fails.
    """
    base = format_confs_table_cell(meta)
    if mol is None:
        return base
    try:
        nconf = int(mol.GetNumConformers())
    except Exception:
        nconf = 0
    if nconf < 2:
        return base
    try:
        blocks_b64 = conformer_mol_blocks_b64_json(mol)
    except Exception:
        logger.exception("pack_confs_cell: mol block serialization failed")
        return base
    meta_out = {k: meta[k] for k in _META_KEYS if k in meta}
    inner: dict[str, Any] = {"v": CONFS_PACK_VERSION, "m": meta_out, "b": blocks_b64}
    s = json.dumps(inner, separators=(",", ":"), ensure_ascii=True)
    if len(s) <= max_chars:
        return s
    try:
        blocks = json.loads(base64.b64decode(blocks_b64.encode("ascii")))
    except Exception:
        return base
    if not isinstance(blocks, list) or len(blocks) < 2:
        return base
    meta_mut = dict(meta_out)
    while len(blocks) >= 1:
        meta_mut["truncated"] = True
        meta_mut["n_packed"] = len(blocks)
        new_b64 = base64.b64encode(json.dumps(blocks).encode("utf-8")).decode("ascii")
        inner = {"v": CONFS_PACK_VERSION, "m": meta_mut, "b": new_b64}
        s = json.dumps(inner, separators=(",", ":"), ensure_ascii=True)
        if len(s) <= max_chars:
            return s
        if len(blocks) <= 1:
            return base
        blocks.pop()
    return base


def unpack_confs_blocks_json_b64(cell_text: str) -> str | None:
    """
    If *cell_text* is a packed ``confs`` cell (v1 with ``b``), return the inner ``blocks_json_b64`` string
    expected by :class:`~chemmanager.ui.mol_viewer_3d.Molecule3DViewerDialog` ``multi_conf_blocks_json_b64``.
    """
    s = (cell_text or "").strip()
    if not s or len(s) < 10:
        return None
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    if int(d.get("v", 0)) != CONFS_PACK_VERSION:
        return None
    b = d.get("b")
    if not isinstance(b, str) or not b.strip():
        return None
    try:
        inner = json.loads(base64.b64decode(b.encode("ascii")))
    except Exception:
        return None
    if not isinstance(inner, list) or len(inner) < 1:
        return None
    return b


def demote_v1_cell_to_sidecar(cell_text: str, column_key: str) -> tuple[str, str | None]:
    """
    Split a legacy v1 packed cell (``v`` 1 with embedded ``b``) into a short v2 cell plus the blocks payload.

    Returns ``(new_cell_text, blocks_b64_or_none)``. If *cell_text* is not a splittable v1 pack, returns
    ``(cell_text, None)`` unchanged.
    """
    s = (cell_text or "").strip()
    if not s:
        return cell_text, None
    b = unpack_confs_blocks_json_b64(s)
    if b is None:
        return cell_text, None
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return cell_text, None
    if not isinstance(d, dict) or int(d.get("v", 0)) != CONFS_PACK_VERSION:
        return cell_text, None
    m = d.get("m")
    if not isinstance(m, dict):
        m = {}
    light = {"v": CONFS_PACK_SIDECAR_VERSION, "h": str(column_key), "m": m}
    try:
        out = json.dumps(light, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        return cell_text, None
    return out, b


def rehydrate_v1_confs_cell(
    cell_text: str,
    column_key: str,
    oid: int,
    sidecar: dict[tuple[int, str], str],
) -> str:
    """Rebuild a legacy v1 JSON string for workers that expect embedded ``b`` (e.g. superpose)."""
    s = (cell_text or "").strip()
    if not s:
        return s
    if unpack_confs_blocks_json_b64(s) is not None:
        return s
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return s
    if not isinstance(d, dict) or int(d.get("v", 0)) != CONFS_PACK_SIDECAR_VERSION:
        return s
    if str(d.get("h") or "") != str(column_key):
        return s
    b64 = sidecar.get((int(oid), str(column_key)))
    if not isinstance(b64, str) or not b64.strip():
        return s
    m = d.get("m")
    if not isinstance(m, dict):
        m = {}
    inner = {"v": CONFS_PACK_VERSION, "m": m, "b": b64}
    try:
        return json.dumps(inner, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        return s


def resolve_blocks_b64_for_viewer(
    cell_text: str,
    column_key: str,
    oid: int | None,
    sidecar: dict[tuple[int, str], str],
) -> str | None:
    """Inner mol-blocks base64 string for the 3D viewer, from embedded v1 or v2 + *sidecar*."""
    b = unpack_confs_blocks_json_b64(cell_text)
    if b is not None:
        return b
    if oid is None:
        return None
    try:
        d = json.loads((cell_text or "").strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or int(d.get("v", 0)) != CONFS_PACK_SIDECAR_VERSION:
        return None
    if str(d.get("h") or "") != str(column_key):
        return None
    b2 = sidecar.get((int(oid), str(column_key)))
    if isinstance(b2, str) and b2.strip():
        return b2
    return None


def serialize_confs_sidecar(store: dict[tuple[int, str], str]) -> dict[str, str]:
    """JSON-friendly dict for session documents (keys ``\"{oid}:{column}\"``)."""
    out: dict[str, str] = {}
    for (oid, col), b64 in (store or {}).items():
        out[f"{int(oid)}:{str(col)}"] = str(b64)
    return out


def deserialize_confs_sidecar(raw: dict | None) -> dict[tuple[int, str], str]:
    out: dict[tuple[int, str], str] = {}
    for k, v in (raw or {}).items():
        if not isinstance(v, str) or not v.strip():
            continue
        parts = str(k).split(":", 1)
        if len(parts) != 2:
            continue
        try:
            out[(int(parts[0]), str(parts[1]))] = v
        except ValueError:
            continue
    return out


def mol_from_packed_confs_cell(cell_text: str) -> Chem.Mol | None:
    """
    Rebuild a multi-conformer :class:`rdkit.Chem.Mol` from a packed ``confs`` / ``superpose`` table cell
    (version 1 with coordinate payload ``b``). Returns ``None`` if the cell is not packed or has fewer than two conformers.
    """
    b64 = unpack_confs_blocks_json_b64(cell_text)
    if b64 is None:
        return None
    try:
        blocks_enc = json.loads(base64.b64decode(b64.encode("ascii")))
    except Exception:
        return None
    if not isinstance(blocks_enc, list) or len(blocks_enc) < 2:
        return None
    blocks: list[str] = []
    for enc in blocks_enc:
        if not isinstance(enc, str):
            return None
        try:
            blocks.append(base64.b64decode(enc.encode("ascii")).decode("utf-8"))
        except Exception:
            return None
    mol = Chem.MolFromMolBlock(blocks[0], sanitize=True, removeHs=False)
    if mol is None:
        return None
    if mol.GetNumConformers() < 1:
        return None
    na = mol.GetNumAtoms()
    for bi in blocks[1:]:
        frag = Chem.MolFromMolBlock(bi, sanitize=True, removeHs=False)
        if frag is None or frag.GetNumAtoms() != na:
            return None
        if frag.GetNumConformers() < 1:
            return None
        try:
            mol.AddConformer(frag.GetConformer(0), assignId=True)
        except Exception:
            return None
    return mol
