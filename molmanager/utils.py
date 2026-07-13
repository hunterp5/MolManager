import re

# Upper bound for attempting RDKit parses from a single table cell (mol blocks, etc.).
_CELL_TEXT_MAX_PARSE_CHARS = 2_000_000


def looks_like_mol_block(text: str) -> bool:
    """Heuristic: cell text resembles an MDL mol block."""
    t = text or ""
    return "V2000" in t or "V3000" in t or ("M  END" in t and "\n" in t)


def parse_molecule_from_cell_text(raw: str):
    """
    Best-effort RDKit molecule from arbitrary table cell text: SMILES, InChI, MolBlock, simple PDB.

    Returns ``None`` when nothing parses. Import RDKit lazily so non-chemistry code paths stay light.
    """
    from rdkit import Chem

    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) > _CELL_TEXT_MAX_PARSE_CHARS:
        return None
    try:
        m = Chem.MolFromSmiles(raw)
        if m is not None:
            return m
    except Exception:
        pass
    try:
        m = Chem.MolFromInchi(raw)
        if m is not None:
            return m
    except Exception:
        pass
    if looks_like_mol_block(raw):
        try:
            m = Chem.MolFromMolBlock(raw)
            if m is not None:
                return m
        except Exception:
            pass
    head = raw[:200]
    if "ATOM  " in head or raw.startswith("COMPND") or raw.startswith("HEADER"):
        try:
            m = Chem.MolFromPDBBlock(raw)
            if m is not None:
                return m
        except Exception:
            pass
    # SMARTS / reaction SMARTS (SMILES already attempted; skip huge mol blocks that contain '[').
    if (
        len(raw) < 600
        and not looks_like_mol_block(raw)
        and ("[" in raw or ">>" in raw or raw.startswith("^"))
    ):
        try:
            from .smarts_patterns import mol_from_smarts

            m = mol_from_smarts(raw)
            if m is not None:
                return m
        except Exception:
            pass
        return None


def redact_sqlalchemy_url(url: str) -> str:
    """Mask ``user:password`` in a SQLAlchemy URL for logs (best-effort, not a security guarantee)."""
    if not url or "@" not in url:
        return url
    # scheme://user:pass@host -> scheme://user:***@host
    return re.sub(r"(://[^/?#:@]+):([^@/?#]+)@", r"\1:***@", url, count=1)


def safe_float(value):
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def mol_to_canonical_smiles(mol, *, isomeric: bool = True) -> str:
    """Canonical SMILES for ``mol`` (explicit ``canonical=True`` for all app-generated SMILES)."""
    if mol is None:
        return ""
    from rdkit import Chem

    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=isomeric)


def morgan_tanimoto_to_query(
    query_smiles: str,
    hit_smiles: str,
    *,
    radius: int = 2,
    n_bits: int = 2048,
) -> float | None:
    """
    Tanimoto similarity between two SMILES strings using RDKit Morgan bit vectors.

    Used when an external service (e.g. PubChem 2D similarity) does not return a
    per-hit coefficient in the client library; values are comparable for ranking
    but may not match the remote fingerprint definition exactly.
    """
    from rdkit import Chem, DataStructs
    from rdkit.Chem import AllChem

    q = (query_smiles or "").strip()
    h = (hit_smiles or "").strip()
    if not q or not h:
        return None
    mq = Chem.MolFromSmiles(q)
    mh = Chem.MolFromSmiles(h)
    if mq is None or mh is None:
        return None
    try:
        fp1 = AllChem.GetMorganFingerprintAsBitVect(mq, radius, nBits=n_bits)
        fp2 = AllChem.GetMorganFingerprintAsBitVect(mh, radius, nBits=n_bits)
        return float(DataStructs.TanimotoSimilarity(fp1, fp2))
    except Exception:
        return None


def safe_mol_prop_string(mol, name: str) -> str:
    """Read an RDKit string property without crashing on non-UTF-8 SD field data."""
    if mol is None or not mol.HasProp(name):
        return ""
    try:
        v = mol.GetProp(name)
        return "" if v is None else str(v)
    except UnicodeDecodeError:
        # RDKit's Python binding decodes SD tags as UTF-8; some files use Latin-1 or raw bytes.
        return ""
    except Exception:
        return ""

