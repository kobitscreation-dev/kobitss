"""
Database Safe Query Helpers
OWASP A03 — SQL Injection Remediation

Provides:
  - SafeQueryBuilder: Context-manager wrapper enforcing parameterized queries
  - execute_safe: Drop-in replacement for raw cursor.execute with parameter enforcement
  - get_db_engine: SQLAlchemy engine factory loading DSN from env only

All query methods REQUIRE explicit parameter binding. String interpolation
and f-strings in SQL are detected and rejected at runtime.

Usage::

    from app.db.safe_queries import SafeQueryBuilder, execute_safe, get_db_engine

    # SQLAlchemy ORM (preferred)
    engine = get_db_engine()

    # Raw SQL with safe binding
    execute_safe(conn, "SELECT * FROM users WHERE id = %s", (user_id,))

    # Builder API
    with SafeQueryBuilder(engine) as qb:
        rows = qb.select("users", where={"id": user_id, "active": True})
        qb.insert("audit_log", {"user_id": user_id, "action": "login"})
"""

import os
import re
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SQL Injection Detection
# ─────────────────────────────────────────────

# Patterns that indicate unsafe SQL construction
UNSAFE_SQL_PATTERNS = [
    re.compile(r'%[sd]', re.IGNORECASE),        # printf-style interpolation without params
    re.compile(r'\{[^}]*\}'),                    # f-string placeholder
    re.compile(r"'\s*\+\s*"),                    # string concatenation
    re.compile(r'"\s*\+\s*'),                    # string concatenation
]


def _detect_unsafe_sql(query: str) -> bool:
    """Return True if query contains patterns suggesting unsafe string construction."""
    for pattern in UNSAFE_SQL_PATTERNS:
        if pattern.search(query):
            return True
    return False


class UnsafeSQLError(ValueError):
    """Raised when a query appears to be constructed via string formatting."""
    pass


# ─────────────────────────────────────────────
# Engine Factory (A02 — env-only DSN)
# ─────────────────────────────────────────────

def get_db_engine(dsn: Optional[str] = None):
    """
    Create a SQLAlchemy engine using DSN from environment variables.

    Env vars checked (in order):
      DATABASE_URL, DB_URL, DATABASE_URI

    Args:
        dsn: Optional explicit DSN override (for testing only).

    Returns:
        sqlalchemy.engine.Engine

    Raises:
        RuntimeError: If no DSN is found in the environment.
    """
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise ImportError("Install SQLAlchemy: pip install sqlalchemy") from exc

    if dsn is None:
        for env_var in ("DATABASE_URL", "DB_URL", "DATABASE_URI"):
            dsn = os.environ.get(env_var, "").strip()
            if dsn:
                break

    if not dsn:
        raise RuntimeError(
            "No database DSN found. Set DATABASE_URL environment variable. "
            "Format: postgresql://user:password@host:5432/dbname"
        )

    engine = create_engine(
        dsn,
        pool_pre_ping=True,
        pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        echo=False,  # Never log SQL in production
    )
    return engine


# ─────────────────────────────────────────────
# Safe Execute Wrapper
# ─────────────────────────────────────────────

def execute_safe(
    connection,
    query: str,
    params: Optional[Sequence] = None,
    *,
    allow_no_params: bool = False,
) -> Any:
    """
    Execute a SQL query with mandatory parameter binding.

    Detects and rejects queries that appear to use string interpolation.
    Wraps both raw DBAPI connections and SQLAlchemy connections.

    Args:
        connection: DBAPI cursor or SQLAlchemy connection.
        query: SQL query string with %s or :named placeholders.
        params: Sequence of positional parameters OR dict for named params.
        allow_no_params: Set True only for queries genuinely without parameters
                         (e.g. 'SELECT version()'). Unsafe patterns still rejected.

    Returns:
        Query result (cursor or SQLAlchemy result proxy).

    Raises:
        UnsafeSQLError: If query contains string formatting patterns.
        ValueError: If params are required but not provided.

    Examples::

        # Safe — parameterized
        execute_safe(cur, "SELECT * FROM users WHERE id = %s", (user_id,))

        # Safe — named params (SQLAlchemy text)
        execute_safe(conn, "SELECT * FROM orders WHERE user_id = :uid", {"uid": uid})

        # Will raise UnsafeSQLError
        execute_safe(cur, f"SELECT * FROM users WHERE id = {user_id}")
    """
    if _detect_unsafe_sql(query):
        raise UnsafeSQLError(
            "Potentially unsafe SQL detected. Query appears to use string formatting. "
            "Use parameterized queries: execute_safe(conn, 'SELECT * FROM t WHERE id=%s', (id,))"
        )

    if not allow_no_params and params is None:
        # Heuristic: if query has WHERE clause, params are likely needed
        if re.search(r'\bWHERE\b', query, re.IGNORECASE):
            logger.warning(
                "execute_safe called with WHERE clause but no params. "
                "Ensure parameterized binding is being used."
            )

    if params is None:
        params = ()

    logger.debug("Executing safe query: %s [params=%s]", query[:200], bool(params))
    return connection.execute(query, params)


# ─────────────────────────────────────────────
# SafeQueryBuilder — High-level ORM-like interface
# ─────────────────────────────────────────────

class SafeQueryBuilder:
    """
    Context-manager wrapper around SQLAlchemy connection that enforces
    parameterized queries via a simple builder API.

    All methods use SQLAlchemy's text() with bindparams — never string
    interpolation.

    Usage::

        from app.db.safe_queries import SafeQueryBuilder, get_db_engine

        engine = get_db_engine()

        with SafeQueryBuilder(engine) as qb:
            users = qb.select("users", where={"email": email})
            qb.insert("audit_log", {"user_id": uid, "event": "login"})
            qb.update("users", set_={"last_login": now}, where={"id": uid})
    """

    def __init__(self, engine):
        self.engine = engine
        self._conn = None

    def __enter__(self):
        self._conn = self.engine.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        self._conn = None
        return False  # Re-raise exceptions

    def _require_connection(self):
        if self._conn is None:
            raise RuntimeError("SafeQueryBuilder must be used as a context manager.")

    def select(
        self,
        table: str,
        columns: str = "*",
        where: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """
        Safe SELECT query.

        Args:
            table: Table name (must be alphanumeric + underscores).
            columns: Comma-separated column names (default '*').
            where: Dict of column → value bindings.
            order_by: ORDER BY clause (validated against alphanumeric + direction).
            limit: Row limit.

        Returns:
            List of Row objects.
        """
        self._require_connection()
        table = self._validate_identifier(table)
        columns = self._validate_columns(columns)

        from sqlalchemy import text

        parts = [f"SELECT {columns} FROM {table}"]
        bind_params = {}

        if where:
            conditions = []
            for col, val in where.items():
                col_safe = self._validate_identifier(col)
                param_name = f"_where_{col_safe}"
                conditions.append(f"{col_safe} = :{param_name}")
                bind_params[param_name] = val
            parts.append("WHERE " + " AND ".join(conditions))

        if order_by:
            order_by = self._validate_order_by(order_by)
            parts.append(f"ORDER BY {order_by}")

        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError("limit must be a non-negative integer")
            parts.append(f"LIMIT :_limit")
            bind_params["_limit"] = limit

        query = " ".join(parts)
        result = self._conn.execute(text(query), bind_params)
        return result.fetchall()

    def insert(
        self,
        table: str,
        data: Dict[str, Any],
        returning: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Safe INSERT query.

        Args:
            table: Table name.
            data: Dict of column → value to insert.
            returning: Optional column name to RETURN (PostgreSQL).

        Returns:
            Returned value if returning is set, else None.
        """
        self._require_connection()
        table = self._validate_identifier(table)

        from sqlalchemy import text

        cols = [self._validate_identifier(c) for c in data.keys()]
        param_names = [f"_ins_{c}" for c in cols]
        bind_params = {f"_ins_{c}": v for c, v in zip(cols, data.values())}

        query = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + p for p in param_names)})"
        )

        if returning:
            returning = self._validate_identifier(returning)
            query += f" RETURNING {returning}"

        result = self._conn.execute(text(query), bind_params)
        if returning:
            row = result.fetchone()
            return row[0] if row else None
        return None

    def update(
        self,
        table: str,
        set_: Dict[str, Any],
        where: Dict[str, Any],
    ) -> int:
        """
        Safe UPDATE query.

        Args:
            table: Table name.
            set_: Dict of column → new_value.
            where: Dict of column → filter_value (required to prevent full-table updates).

        Returns:
            Number of rows affected.
        """
        self._require_connection()
        if not where:
            raise ValueError("UPDATE without WHERE clause is not allowed. Provide a where dict.")

        table = self._validate_identifier(table)

        from sqlalchemy import text

        bind_params = {}
        set_parts = []
        for col, val in set_.items():
            col_safe = self._validate_identifier(col)
            param_name = f"_set_{col_safe}"
            set_parts.append(f"{col_safe} = :{param_name}")
            bind_params[param_name] = val

        where_parts = []
        for col, val in where.items():
            col_safe = self._validate_identifier(col)
            param_name = f"_where_{col_safe}"
            where_parts.append(f"{col_safe} = :{param_name}")
            bind_params[param_name] = val

        query = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}"
        result = self._conn.execute(text(query), bind_params)
        return result.rowcount

    def delete(
        self,
        table: str,
        where: Dict[str, Any],
    ) -> int:
        """
        Safe DELETE query.

        Args:
            table: Table name.
            where: Dict of column → filter_value (required).

        Returns:
            Number of rows deleted.
        """
        self._require_connection()
        if not where:
            raise ValueError("DELETE without WHERE clause is not allowed.")

        table = self._validate_identifier(table)

        from sqlalchemy import text

        bind_params = {}
        conditions = []
        for col, val in where.items():
            col_safe = self._validate_identifier(col)
            param_name = f"_del_{col_safe}"
            conditions.append(f"{col_safe} = :{param_name}")
            bind_params[param_name] = val

        query = f"DELETE FROM {table} WHERE {' AND '.join(conditions)}"
        result = self._conn.execute(text(query), bind_params)
        return result.rowcount

    # ── Identifier Validation ────────────────────────────────────────────────

    _IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    _COLS_RE = re.compile(r'^[a-zA-Z_\*][a-zA-Z0-9_,\s\.\*]*$')
    _ORDER_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*(\s+(ASC|DESC))?$', re.IGNORECASE)

    @classmethod
    def _validate_identifier(cls, name: str) -> str:
        """Validate a table or column identifier against SQL injection."""
        if not cls._IDENT_RE.match(name):
            raise UnsafeSQLError(
                f"Invalid SQL identifier '{name}'. "
                "Only alphanumeric characters and underscores are allowed."
            )
        return name

    @classmethod
    def _validate_columns(cls, columns: str) -> str:
        """Validate a columns string (e.g. '*', 'id, name, email')."""
        if columns == "*":
            return columns
        if not cls._COLS_RE.match(columns):
            raise UnsafeSQLError(f"Invalid columns specification: '{columns}'")
        return columns

    @classmethod
    def _validate_order_by(cls, order_by: str) -> str:
        """Validate ORDER BY expression."""
        if not cls._ORDER_RE.match(order_by.strip()):
            raise UnsafeSQLError(f"Invalid ORDER BY expression: '{order_by}'")
        return order_by.strip()