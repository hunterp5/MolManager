"""Tests for chemmanager.utils helpers."""

from __future__ import annotations

from rdkit import Chem

from chemmanager.utils import redact_sqlalchemy_url, safe_float, safe_mol_prop_string


def test_safe_float_none():
    assert safe_float(None) is None


def test_safe_float_valid():
    assert safe_float(" 3.14 ") == 3.14
    assert safe_float(42) == 42.0


def test_safe_float_invalid():
    assert safe_float("not a number") is None
    assert safe_float("") is None


def test_safe_mol_prop_string_missing():
    mol = Chem.MolFromSmiles("CC")
    assert safe_mol_prop_string(mol, "nonexistent_prop") == ""


def test_redact_sqlalchemy_url_masks_password():
    u = "postgresql+psycopg://alice:secret@localhost:5432/mydb"
    r = redact_sqlalchemy_url(u)
    assert "secret" not in r
    assert "***" in r
    assert "alice" in r


def test_redact_sqlalchemy_url_no_password_unchanged():
    assert redact_sqlalchemy_url("sqlite:///C:/data/app.db") == "sqlite:///C:/data/app.db"


def test_safe_mol_prop_string_present():
    mol = Chem.MolFromSmiles("CC")
    mol.SetProp("CustomTag", "hello")
    assert safe_mol_prop_string(mol, "CustomTag") == "hello"
