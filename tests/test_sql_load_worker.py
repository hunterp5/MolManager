"""Tests for SQL load worker helpers."""

from __future__ import annotations

from molmanager.workers.sql_load import build_sql_statement


def test_build_sql_statement_table_with_limit():
    sql = build_sql_statement(table="compounds", query=None, limit=100, apply_limit=True)
    assert sql == "SELECT * FROM compounds LIMIT 100"


def test_build_sql_statement_query_wraps_limit():
    sql = build_sql_statement(
        table=None,
        query="SELECT * FROM t WHERE x > 1",
        limit=50,
        apply_limit=True,
    )
    assert "LIMIT 50" in sql
    assert "subq" in sql
