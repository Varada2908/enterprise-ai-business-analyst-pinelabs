"""Static safety checks on generated SQL before it reaches the database.

A query passes only if it is exactly one SELECT (optionally with CTEs or UNION) that reads
from the six known tables and calls no file, system or extension functions. This is the
first of two guards: the executor also opens DuckDB read-only with external access disabled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)   # quiet "falling back to Command" warnings

# Statement / clause types that write, change state or reach outside the database.
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable", "Copy",
    "Command", "Pragma", "Set", "Use", "Attach", "Detach", "Install", "Transaction", "Commit",
    "Rollback", "Into", "LoadData", "Export", "Analyze", "Grant", "Revoke", "Kill", "Describe", "Summarize",
) if hasattr(exp, n))

# Function names (lower case) that read files, inspect the system or load code.
FORBIDDEN_FUNCTION_PREFIXES = (
    "read_", "glob", "getenv", "duckdb_", "pragma_", "sniff_csv", "query", "parquet_", "iceberg_",
    "delta_scan", "sqlite_", "postgres_", "mysql_", "load", "install", "current_setting", "set_",
    "which_secret", "httpfs", "json_execute",
)


@dataclass
class ValidationResult:
    ok: bool
    sql: str                                   # cleaned SQL (trailing semicolon removed)
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    @property
    def error_message(self) -> str:
        return "; ".join(self.errors)


class SQLValidator:
    def __init__(self, allowed_tables: set[str]):
        self.allowed_tables = {t.lower() for t in allowed_tables}

    def validate(self, sql: str) -> ValidationResult:
        cleaned = (sql or "").strip().rstrip(";").strip()
        if not cleaned:
            return ValidationResult(False, cleaned, ["Query is empty."])

        try:
            statements = [s for s in sqlglot.parse(cleaned, read="duckdb") if s is not None]
        except sqlglot.errors.ParseError as e:
            return ValidationResult(False, cleaned, [f"SQL could not be parsed: {str(e).splitlines()[0]}"])
        if len(statements) != 1:
            return ValidationResult(False, cleaned, [f"Expected exactly one statement, found {len(statements)}."])

        root = statements[0]
        errors: list[str] = []
        if not isinstance(root, (exp.Select, exp.SetOperation)):
            errors.append(f"Only SELECT queries are allowed (got {type(root).__name__.upper()}).")

        for node in root.walk():
            if isinstance(node, FORBIDDEN_NODES):
                errors.append(f"Forbidden operation: {type(node).__name__.upper()}.")

        for func in root.find_all(exp.Func):
            name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
            if name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                errors.append(f"Function not allowed: {name}().")

        cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
        tables: set[str] = set()
        for table in root.find_all(exp.Table):
            name = table.name.lower()
            if not name or not isinstance(table.this, exp.Identifier):
                errors.append("Table functions and file paths are not allowed in FROM.")
                continue
            if table.catalog or (table.db and table.db.lower() != "main"):
                errors.append(f"Schema-qualified table not allowed: {table.sql(dialect='duckdb')}.")
            if name in cte_names:
                continue
            if name not in self.allowed_tables:
                errors.append(f"Unknown or disallowed table: {table.name}.")
            tables.add(name)

        errors = list(dict.fromkeys(errors))          # de-duplicate, keep order
        return ValidationResult(not errors, cleaned, errors, sorted(tables))
