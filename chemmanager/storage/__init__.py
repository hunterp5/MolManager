"""Storage backends for large-table workloads."""

from .sqlite_table_store import SqliteTableStore

__all__ = ["SqliteTableStore"]

