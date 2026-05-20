"""BRICS and RECAP fragment decomposition and recomposition (RDKit, no Qt)."""

from __future__ import annotations

import re
from typing import Literal

from rdkit import Chem
from rdkit.Chem import BRICS, Recap

DecompositionMethod = Literal["brics", "recap"]
RecompositionMethod = Literal["brics", "recap"]

_FRAGMENT_COL_RE = re.compile(r"^(.+)_(\d+)$")


def decompose_brics(mol: Chem.Mol) -> list[str]:
    """Return sorted BRICS fragment SMILES for ``mol`` (may be a single fragment)."""
    frags = BRICS.BRICSDecompose(mol)
    return sorted(str(s) for s in frags)


def decompose_recap(mol: Chem.Mol) -> list[str]:
    """Return sorted RECAP leaf fragment SMILES; use the root when no leaves exist."""
    tree = Recap.RecapDecompose(mol)
    if tree is None:
        return []
    leaves = tree.GetLeaves()
    if leaves:
        return sorted(str(k) for k in leaves.keys())
    if tree.smiles:
        return [str(tree.smiles)]
    return []


def decompose_fragments(mol: Chem.Mol, method: DecompositionMethod) -> list[str]:
    if method == "brics":
        return decompose_brics(mol)
    return decompose_recap(mol)


def assemble_fragment_table_rows(
    oids: list[int],
    per_row_fragments: list[list[str]],
    column_prefix: str,
) -> tuple[list[tuple[int, dict[str, str]]], list[str]]:
    """Build table rows and column headers; pad short rows with ``N/A``."""
    prefix = (column_prefix or "Frag").strip() or "Frag"
    max_n = max((len(fr) for fr in per_row_fragments), default=0)
    if max_n == 0:
        return [], []
    headers = [f"{prefix}_{i}" for i in range(1, max_n + 1)]
    rows: list[tuple[int, dict[str, str]]] = []
    for oid, frags in zip(oids, per_row_fragments, strict=True):
        row = {h: (frags[i] if i < len(frags) else "N/A") for i, h in enumerate(headers)}
        rows.append((int(oid), row))
    return rows, headers


def detect_fragment_column_prefixes(headers: list[str]) -> list[str]:
    """Return sorted unique prefixes from columns like ``BRICS_1``, ``RECAP_2``."""
    found: set[str] = set()
    for h in headers:
        m = _FRAGMENT_COL_RE.match((h or "").strip())
        if m:
            found.add(m.group(1))
    return sorted(found, key=str.lower)


def fragment_columns_for_prefix(headers: list[str], prefix: str) -> list[str]:
    """Ordered ``PREFIX_1``, ``PREFIX_2``, … column names present in ``headers``."""
    pref = (prefix or "").strip()
    if not pref:
        return []
    cols = [h for h in headers if _FRAGMENT_COL_RE.match(h) and h.startswith(f"{pref}_")]
    return sorted(cols, key=lambda name: int(_FRAGMENT_COL_RE.match(name).group(2)))


def _recap_smiles_for_brics_coupling(smi: str) -> str:
    """Map RECAP ``*`` attachment points to BRICS-style ``[16*]`` for coupling."""
    return re.sub(r"(?<!\[)\*(?!\])", "[16*]", smi)


def fragment_smiles_to_mol(smi: str, method: RecompositionMethod) -> Chem.Mol | None:
    """Parse a decomposition fragment SMILES for recomposition."""
    text = (smi or "").strip()
    if not text or text.upper() == "N/A":
        return None
    if method == "recap":
        text = _recap_smiles_for_brics_coupling(text)
    return Chem.MolFromSmiles(text)


def unique_fragment_smiles(fragment_smiles: list[str]) -> list[str]:
    """Stable unique list, skipping blanks and ``N/A``."""
    seen: set[str] = set()
    out: list[str] = []
    for smi in fragment_smiles:
        text = (smi or "").strip()
        if not text or text.upper() == "N/A" or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def recompose_fragments(
    fragment_smiles: list[str],
    method: RecompositionMethod,
    *,
    max_depth: int = 3,
    max_products: int = 2000,
    uniquify: bool = True,
) -> list[str]:
    """
    Combine fragment SMILES into new molecules using :func:`BRICS.BRICSBuild`.

    RECAP fragments (``*`` attachment points) are mapped to BRICS dummies before coupling.
  """
    unique = unique_fragment_smiles(fragment_smiles)
    mols: list[Chem.Mol] = []
    for smi in unique:
        mol = fragment_smiles_to_mol(smi, method)
        if mol is not None:
            mols.append(mol)
    if len(mols) < 2:
        raise ValueError("Need at least two distinct, parseable fragments to recompose.")

    depth = max(1, int(max_depth))
    cap = max(1, int(max_products))
    seen: set[str] = set()
    products: list[str] = []
    for mol in BRICS.BRICSBuild(
        mols,
        maxDepth=depth,
        uniquify=bool(uniquify),
        scrambleReagents=False,
        onlyCompleteMols=True,
    ):
        try:
            smi = Chem.MolToSmiles(mol)
        except Exception:
            continue
        if smi in seen:
            continue
        seen.add(smi)
        products.append(smi)
        if len(products) >= cap:
            break
    if not products:
        raise ValueError("No products were generated from the fragment pool.")
    return sorted(products)
