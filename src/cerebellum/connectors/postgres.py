"""PostgreSQL connector (psycopg 3) plus a SQLite sandbox used when `dsn: sandbox`."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from cerebellum.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    HealthStatus,
    SqlConnector,
    jsonable,
    register_connector,
)

SANDBOX_DSN = "sandbox"
T = TypeVar("T")


def sandbox_db_path(home: Path, connector_name: str) -> Path:
    return home / f"sandbox_{connector_name}.db"


def to_pyformat(sql: str) -> str:
    """Convert `:name` placeholders to psycopg's `%(name)s`, escape literal `%`, and leave
    `::casts` and quoted text untouched."""
    out: list[str] = []
    i, n, quote = 0, len(sql), ""
    while i < n:
        ch = sql[i]
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = ""
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
        elif ch == "%":
            out.append("%%")
            i += 1
        elif sql.startswith("::", i):
            out.append("::")
            i += 2
        elif ch == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append(f"%({sql[i + 1 : j]})s")
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class SqliteSandboxConnector(SqlConnector):
    """Runs the same parameterised SQL against a local SQLite file seeded on first use."""

    def __init__(self, name: str, path: Path, seed: Path | None):
        super().__init__(name)
        self.path = path
        self.seed = seed
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._conn is None:
            self._conn = await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> sqlite3.Connection:
        fresh = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        if fresh and self.seed is not None:
            try:
                conn.executescript(self.seed.read_text(encoding="utf-8"))
                conn.commit()
            except (OSError, sqlite3.Error) as exc:
                conn.close()
                self.path.unlink(missing_ok=True)
                raise ConnectorError(
                    f"cannot seed sandbox database from {self.seed}: {exc}",
                    retryable=False,
                    kind="seed",
                ) from exc
        return conn

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    async def query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(sql, dict(params)).fetchall()
            return [{key: jsonable(row[key]) for key in row.keys()} for row in rows]

        return await self._run(op)

    async def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        def op(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(sql, dict(params))
            conn.commit()
            return cursor.rowcount

        return await self._run(op)

    async def _run(self, op: Callable[[sqlite3.Connection], T]) -> T:
        await self.open()
        assert self._conn is not None
        async with self._lock:
            try:
                return await asyncio.to_thread(op, self._conn)
            except sqlite3.OperationalError as exc:
                transient = "locked" in str(exc) or "busy" in str(exc)
                raise ConnectorError(
                    f"sqlite error: {exc}", retryable=transient, kind="sql"
                ) from exc
            except sqlite3.Error as exc:
                raise ConnectorError(f"sqlite error: {exc}", retryable=False, kind="sql") from exc

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            await self.query("SELECT 1 AS ok", {})
        except ConnectorError as exc:
            return HealthStatus(False, str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        return HealthStatus(True, f"sqlite sandbox at {self.path}", elapsed)


class PostgresConnector(SqlConnector):
    def __init__(self, name: str, dsn: str):
        super().__init__(name)
        self.dsn = dsn
        self._conn: Any = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._conn is not None:
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ConnectorError(
                "PostgreSQL support requires: pip install 'cerebellum[postgres]'",
                retryable=False,
                kind="dependency",
            ) from exc
        try:
            self._conn = await psycopg.AsyncConnection.connect(
                self.dsn, autocommit=True, row_factory=dict_row
            )
        except psycopg.OperationalError as exc:
            raise ConnectorError(
                f"cannot connect to postgres: {exc}", retryable=True, kind="connection"
            ) from exc

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await conn.close()

    async def query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        return await self._run(sql, params, fetch=True)

    async def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        return await self._run(sql, params, fetch=False)

    async def _run(self, sql: str, params: Mapping[str, Any], *, fetch: bool) -> Any:
        import psycopg

        await self.open()
        async with self._lock:
            try:
                async with self._conn.cursor() as cursor:
                    await cursor.execute(to_pyformat(sql), dict(params))
                    if fetch:
                        rows = await cursor.fetchall()
                        return [{k: jsonable(v) for k, v in row.items()} for row in rows]
                    return cursor.rowcount
            except psycopg.OperationalError as exc:
                self._conn = None  # reconnect on the next attempt
                raise ConnectorError(
                    f"postgres connection error: {exc}", retryable=True, kind="connection"
                ) from exc
            except psycopg.Error as exc:
                raise ConnectorError(f"postgres error: {exc}", retryable=False, kind="sql") from exc

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            await self.query("SELECT 1 AS ok", {})
        except ConnectorError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(True, "postgres reachable", (time.perf_counter() - started) * 1000)


@register_connector("postgres")
def _make_postgres(name: str, spec: Any, env: ConnectorEnv) -> SqlConnector:
    if spec.dsn == SANDBOX_DSN:
        seed = env.base_dir / spec.seed if spec.seed else None
        return SqliteSandboxConnector(name, sandbox_db_path(env.home, name), seed)
    return PostgresConnector(name, spec.dsn)
