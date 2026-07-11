"""Background worker that streams SQL query results off the GUI thread."""

from __future__ import annotations

import logging
import re

from PyQt5.QtCore import QRunnable
from rdkit import Chem

logger = logging.getLogger(__name__)


class SqlLoadWorker(QRunnable):
    """Fetch rows with SQLAlchemy streaming and emit GUI-ready batches.

    Each batch item is ``(row_cells: dict[str, str], mol: Chem.Mol | None)`` where *mol* is set
    when a SMILES column was detected and the cell parses.
    """

    def __init__(
        self,
        url: str,
        sql: str,
        *,
        page_size: int,
        row_limit: int,
        engine_kwargs: dict,
        signals,
        cancel_event,
    ):
        super().__init__()
        self._url = url
        self._sql = sql
        self._page_size = max(128, int(page_size))
        self._row_limit = max(0, int(row_limit))
        self._engine_kwargs = dict(engine_kwargs)
        self._signals = signals
        self._cancel_event = cancel_event

    def _cancelled(self) -> bool:
        ev = self._cancel_event
        return ev is not None and ev.is_set()

    def run(self) -> None:
        try:
            from sqlalchemy import create_engine, text
        except Exception as e:
            self._emit_failed(f"sqlalchemy is required for SQL loading: {e}")
            return
        try:
            self._signals.tool_progress.emit("Loading from SQL…", -1, -1)
        except Exception:
            pass
        try:
            eng = create_engine(self._url, **self._engine_kwargs)
            with eng.connect() as conn:
                rs = conn.execution_options(stream_results=True).execute(text(self._sql))
                cols = [str(c) for c in rs.keys()]
                if not cols:
                    rs.close()
                    self._emit_failed("Query returned 0 columns.")
                    return
                smiles_col = next((c for c in cols if c.lower() == "smiles"), None)
                first = True
                emitted = 0
                while not self._cancelled():
                    chunk = rs.fetchmany(self._page_size)
                    if not chunk:
                        break
                    batch: list[tuple[dict[str, str], Chem.Mol | None]] = []
                    for rec in chunk:
                        if self._cancelled():
                            break
                        row_cells: dict[str, str] = {}
                        for c in cols:
                            v = rec._mapping.get(c)
                            row_cells[c] = "" if v is None else str(v)
                        mol = None
                        if smiles_col is not None:
                            smi = (row_cells.get(smiles_col, "") or "").strip()
                            if smi:
                                try:
                                    mol = Chem.MolFromSmiles(smi)
                                except Exception:
                                    mol = None
                        batch.append((row_cells, mol))
                        emitted += 1
                        if self._row_limit and emitted >= self._row_limit:
                            break
                    if batch:
                        self._signals.sql_rows_loaded.emit(batch, cols if first else [], first, False)
                        first = False
                    if self._row_limit and emitted >= self._row_limit:
                        break
                rs.close()
            if self._cancelled():
                return
            if emitted <= 0:
                self._emit_failed("Query returned 0 rows.")
                return
            self._signals.sql_rows_loaded.emit([], [], first, True)
        except Exception as e:
            logger.exception("SqlLoadWorker failed")
            self._emit_failed(str(e))

    def _emit_failed(self, msg: str) -> None:
        try:
            self._signals.sql_load_failed.emit(str(msg))
        except Exception:
            pass


def build_sql_statement(
    *,
    query: str | None,
    table: str | None,
    limit: int,
    apply_limit: bool,
) -> str:
    """Return the SQL text to execute (LIMIT applied when configured)."""
    if table is not None:
        sql = f"SELECT * FROM {table}"
        if apply_limit and limit > 0:
            sql += f" LIMIT {int(limit)}"
        return sql
    sql = (query or "").strip()
    if apply_limit and limit > 0 and re.search(r"\blimit\b", sql, flags=re.IGNORECASE) is None:
        sql = f"SELECT * FROM ({sql}) AS subq LIMIT {int(limit)}"
    return sql
