from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
    HAS_PG = True
except ImportError:
    HAS_PG = False

try:
    import pymysql
    HAS_MYSQL = True
except ImportError:
    HAS_MYSQL = False

import pandas as pd


# ─────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────

@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    is_primary: bool = False
    is_foreign: bool = False

    def __str__(self):
        return self.name


@dataclass
class TableInfo:
    schema: str
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: Optional[int] = None

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def schema_string(self) -> str:
        """Returns the compact schema string used by the SQL generator."""
        col_names = ", ".join(c.name for c in self.columns)
        return f"{self.name}({col_names})"

    def schema_string_full(self) -> str:
        """Returns a detailed schema with type info for the prompt."""
        parts = []
        for c in self.columns:
            flag = ""
            if c.is_primary:
                flag = " PK"
            elif c.is_foreign:
                flag = " FK"
            parts.append(f"{c.name} {c.data_type}{flag}")
        return f"{self.name}({', '.join(parts)})"


@dataclass
class ConnectionConfig:
    db_type: str        # "postgresql" | "mysql"
    host: str
    port: int
    database: str
    username: str
    password: str

    @property
    def display_name(self) -> str:
        return f"{self.db_type.upper()}  {self.username}@{self.host}:{self.port}/{self.database}"


# ─────────────────────────────────────────────
# Query result
# ─────────────────────────────────────────────

@dataclass
class QueryResult:
    success: bool
    data: Optional[pd.DataFrame] = None
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    error: str = ""
    execution_ms: float = 0.0


# ─────────────────────────────────────────────
# Main connector class
# ─────────────────────────────────────────────

class DBConnector:
    def __init__(self):
        self._conn = None
        self._config: Optional[ConnectionConfig] = None
        self._db_type: str = ""

    # ── Connection ──────────────────────────────
    def connect(self, config: ConnectionConfig) -> tuple[bool, str]:
        """Connect to the database. Returns (success, error_message)."""
        try:
            self.disconnect()
            self._config = config
            self._db_type = config.db_type.lower()

            if self._db_type == "postgresql":
                if not HAS_PG:
                    return False, "psycopg2 not installed. Run: pip install psycopg2-binary"
                self._conn = psycopg2.connect(
                    host=config.host,
                    port=config.port,
                    dbname=config.database,
                    user=config.username,
                    password=config.password,
                    connect_timeout=10,
                )
                self._conn.autocommit = True

            elif self._db_type == "mysql":
                if not HAS_MYSQL:
                    return False, "PyMySQL not installed. Run: pip install PyMySQL"
                self._conn = pymysql.connect(
                    host=config.host,
                    port=config.port,
                    database=config.database,
                    user=config.username,
                    password=config.password,
                    connect_timeout=10,
                    autocommit=True,
                )
            else:
                return False, f"Unsupported database type: {config.db_type}"

            return True, ""

        except Exception as e:
            self._conn = None
            return False, str(e)

    def disconnect(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def is_connected(self) -> bool:
        if not self._conn:
            return False
        try:
            if self._db_type == "postgresql":
                return self._conn.closed == 0
            elif self._db_type == "mysql":
                self._conn.ping(reconnect=False)
                return True
        except Exception:
            return False

    # ── Schema Discovery ────────────────────────
    def list_schemas(self) -> list[str]:
        """Return list of schemas/databases available to this user."""
        if not self.is_connected:
            return []
        try:
            if self._db_type == "postgresql":
                rows = self._fetchall(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT IN ('information_schema','pg_catalog','pg_toast') "
                    "ORDER BY schema_name"
                )
                return [r[0] for r in rows]
            elif self._db_type == "mysql":
                rows = self._fetchall(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT IN ('information_schema','mysql','performance_schema','sys') "
                    "ORDER BY schema_name"
                )
                return [r[0] for r in rows]
        except Exception:
            return []

    def list_tables(self, schema: str) -> list[str]:
        """Return table names in a given schema."""
        if not self.is_connected:
            return []
        try:
            if self._db_type == "postgresql":
                rows = self._fetchall(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name",
                    (schema,)
                )
            else:  # mysql
                rows = self._fetchall(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name",
                    (schema,)
                )
            return [r[0] for r in rows]
        except Exception:
            return []

    def get_table_info(self, schema: str, table: str) -> TableInfo:
        """Fetch full column metadata for one table."""
        info = TableInfo(schema=schema, name=table)
        if not self.is_connected:
            return info

        try:
            # Get primary key columns
            pk_cols = self._get_primary_keys(schema, table)
            fk_cols = self._get_foreign_keys(schema, table)

            if self._db_type == "postgresql":
                rows = self._fetchall(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (schema, table)
                )
            else:
                rows = self._fetchall(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (schema, table)
                )

            for col_name, data_type, nullable in rows:
                info.columns.append(ColumnInfo(
                    name=col_name,
                    data_type=data_type,
                    nullable=(nullable == "YES"),
                    is_primary=(col_name in pk_cols),
                    is_foreign=(col_name in fk_cols),
                ))

            # Approximate row count — use dialect-appropriate quoting
            try:
                if self._db_type == "postgresql":
                    count_rows = self._fetchall(
                        f'SELECT COUNT(*) FROM "{schema}"."{table}"'
                    )
                else:  # mysql — backtick quoting
                    count_rows = self._fetchall(
                        f"SELECT COUNT(*) FROM `{schema}`.`{table}`"
                    )
                if count_rows:
                    info.row_count = count_rows[0][0]
            except Exception:
                pass

        except Exception:
            pass

        return info

    def get_schema_for_tables(self, schema: str, tables: list[str]) -> dict[str, TableInfo]:
        """Batch-fetch TableInfo for multiple tables."""
        return {t: self.get_table_info(schema, t) for t in tables}

    def _get_primary_keys(self, schema: str, table: str) -> set[str]:
        try:
            if self._db_type == "postgresql":
                rows = self._fetchall(
                    "SELECT kcu.column_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name = kcu.constraint_name "
                    "  AND tc.table_schema = kcu.table_schema "
                    "WHERE tc.constraint_type = 'PRIMARY KEY' "
                    "  AND tc.table_schema = %s AND tc.table_name = %s",
                    (schema, table)
                )
            else:
                rows = self._fetchall(
                    "SELECT column_name FROM information_schema.key_column_usage "
                    "WHERE constraint_name = 'PRIMARY' "
                    "  AND table_schema = %s AND table_name = %s",
                    (schema, table)
                )
            return {r[0] for r in rows}
        except Exception:
            return set()

    def _get_foreign_keys(self, schema: str, table: str) -> set[str]:
        try:
            rows = self._fetchall(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "  AND tc.table_schema = kcu.table_schema "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "  AND tc.table_schema = %s AND tc.table_name = %s",
                (schema, table)
            )
            return {r[0] for r in rows}
        except Exception:
            return set()

    # ── Query Execution ─────────────────────────
    def execute_query(self, sql: str) -> QueryResult:
        """Execute SQL and return results as a QueryResult with DataFrame."""
        import time
        if not self.is_connected:
            return QueryResult(success=False, error="Not connected to database.")

        start = time.time()
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql)

            elapsed = (time.time() - start) * 1000

            # SELECT-like queries return rows
            if cursor.description:
                cols = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
                df = pd.DataFrame(rows, columns=cols)
                cursor.close()
                return QueryResult(
                    success=True,
                    data=df,
                    row_count=len(df),
                    columns=cols,
                    execution_ms=elapsed,
                )
            else:
                # DML — no rows returned
                rc = cursor.rowcount
                cursor.close()
                return QueryResult(
                    success=True,
                    data=pd.DataFrame(),
                    row_count=rc,
                    execution_ms=elapsed,
                )

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return QueryResult(
                success=False,
                error=str(e),
                execution_ms=elapsed,
            )

    # ── Internal helpers ────────────────────────
    def _fetchall(self, sql: str, params=None) -> list:
        cursor = self._conn.cursor()
        cursor.execute(sql, params or ())
        rows = cursor.fetchall()
        cursor.close()
        return rows
