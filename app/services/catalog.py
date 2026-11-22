"""Data Catalog service: the same semantic layer the AI analyst uses, plus live column profiles.

Descriptions, join keys, metrics, rules and conventions come from the backend's SchemaContext (built from
the data catalog and checked against the database). Profiles and sample rows are fixed SQL that pass
the SQL validator and run read-only. The SQL guard demo only validates; it never executes.
"""
from __future__ import annotations

import re

import pandas as pd

from ai_analyst.schema_context import TABLES
from ai_analyst.sql_validator import FORBIDDEN_FUNCTION_PREFIXES, FORBIDDEN_NODES, ValidationResult

from .backend import QueryOutcome, get_executor, get_schema, get_validator, run_query

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name or ""):
        raise ValueError(f"unexpected identifier {name!r}")
    return f'"{name}"'


def table_names() -> list[str]:
    schema = get_schema()
    return [t for t in TABLES if t in schema.tables]


def summary() -> dict:
    s = get_schema()
    return {
        "tables": len(s.tables),
        "columns": sum(len(t.columns) for t in s.tables.values()),
        "rows": sum(t.row_count for t in s.tables.values()),
        "join_keys": len(s.join_keys),
        "metrics": len(s.metrics),
        "rules": len(s.validity_rules),
        "warnings": list(s.warnings),
    }


def summary_table_rows(table: str) -> int:
    """Row count recorded when the schema context was loaded."""
    return get_schema().tables[table].row_count


def tables_overview() -> pd.DataFrame:
    s = get_schema()
    return pd.DataFrame([{"table": t.name, "description": t.description, "grain": t.grain,
                          "primary_key": t.primary_key, "rows": t.row_count, "columns": len(t.columns)}
                         for t in (s.tables[n] for n in table_names())])


def columns(table: str) -> pd.DataFrame:
    t = get_schema().tables[table]
    return pd.DataFrame([{"column": c.name, "type": c.db_type, "nullable": c.nullable, "description": c.description,
                          "values_or_notes": c.allowed_values} for c in t.columns])


def all_columns() -> pd.DataFrame:
    frames = [columns(t).assign(table=t) for t in table_names()]
    df = pd.concat(frames, ignore_index=True)
    return df[["table", "column", "type", "nullable", "description", "values_or_notes"]]


def relationships(table: str | None = None) -> pd.DataFrame:
    rows = []
    for j in get_schema().join_keys:
        if table and table not in (j.left_table, j.right_table):
            continue
        # written out here because the backend's on_clause() prefixes expressions too ("t.CAST(x AS DATE)")
        side = lambda tbl, col: re.sub(r"\((\w+)", rf"({tbl}.\1", col) if "(" in col else f"{tbl}.{col}"  # noqa: E731
        join_on = " AND ".join(f"{side(j.left_table, lc)} = {side(j.right_table, rc)}"
                               for lc, rc in zip(j.left_columns, j.right_columns))
        rows.append({"from": j.left_table, "to": j.right_table, "join_on": join_on,
                     "cardinality": j.cardinality, "note": j.note})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- live queries
def profile(table: str) -> QueryOutcome:
    """Per column: non-null share, distinct values, min and max, measured on the raw table."""
    if table not in get_schema().allowed_tables():
        raise ValueError(f"unknown table {table!r}")
    t = get_schema().tables[table]
    parts = [f"SELECT {i} AS pos, '{c.name}' AS column_name, count(*) AS rows, count({_ident(c.name)}) AS non_null, "
             f"count(DISTINCT {_ident(c.name)}) AS distinct_values, "
             f"min({_ident(c.name)})::VARCHAR AS min_value, max({_ident(c.name)})::VARCHAR AS max_value "
             f"FROM {_ident(table)}"
             for i, c in enumerate(t.columns)]
    return run_query("\nUNION ALL\n".join(parts) + "\nORDER BY pos")


def sample_rows(table: str, n: int = 10) -> QueryOutcome:
    if table not in get_schema().allowed_tables():
        raise ValueError(f"unknown table {table!r}")
    return run_query(f"SELECT * FROM {_ident(table)} LIMIT {int(n)}")


def top_values(table: str, column: str, k: int = 8) -> QueryOutcome:
    """Most frequent values of one column (used for low-cardinality columns)."""
    if table not in get_schema().allowed_tables() or column not in get_schema().tables[table].column_names:
        raise ValueError("unknown table or column")
    c = _ident(column)
    sql = (f"SELECT coalesce({c}::VARCHAR, 'NULL') AS value, count(*) AS rows, "
           f"count(*)::DOUBLE / sum(count(*)) OVER () AS share FROM {_ident(table)} "
           f"GROUP BY 1 ORDER BY rows DESC LIMIT {int(k)}")
    return run_query(sql)


# ----------------------------------------------------------------------------- access & safety
def access_policy() -> dict:
    ex = get_executor()
    return {
        "allowed_tables": sorted(get_schema().allowed_tables()),
        "forbidden_operations": sorted(n.__name__.upper() for n in FORBIDDEN_NODES),
        "forbidden_functions": [f"{p}*" if p.endswith("_") else p for p in FORBIDDEN_FUNCTION_PREFIXES],
        "timeout_s": ex.timeout_s,
        "max_rows": ex.max_rows,
    }


def check_sql(sql: str) -> ValidationResult:
    """Run the backend's SQL validator only. Nothing is executed."""
    return get_validator().validate(sql)


def er_diagram_dot() -> str:
    """Graphviz DOT for the tables and their join keys (built from the backend's JOIN_KEYS)."""
    s = get_schema()
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; pad=0.2; nodesep=0.5; ranksep=1.1;',
             'node [shape=plaintext fontname="Inter, Segoe UI, Arial" fontsize=11];',
             'edge [color="#94A3B8" arrowsize=0.6 fontname="Inter, Segoe UI, Arial" fontsize=9 fontcolor="#64748B"];']
    keys = {}
    for j in s.join_keys:
        keys.setdefault(j.left_table, set()).update(c for c in j.left_columns if "(" not in c)
        keys.setdefault(j.right_table, set()).update(c for c in j.right_columns if "(" not in c)
    for name in table_names():
        t = s.tables[name]
        rows = "".join(
            f'<TR><TD ALIGN="LEFT" PORT="{c.name}"><FONT COLOR="{"#0F766E" if c.name == t.primary_key else "#334155"}">'
            f'{"<B>" if c.name == t.primary_key else ""}{c.name}{"</B>" if c.name == t.primary_key else ""}</FONT>'
            f'</TD></TR>' for c in t.columns if c.name == t.primary_key or c.name in keys.get(name, set()))
        # row count on its own line: browsers render Inter wider than Graphviz estimates, which clipped one line
        header = (f'<TR><TD BGCOLOR="#0F766E" ALIGN="LEFT" BALIGN="LEFT"><FONT COLOR="white"><B>{name}</B></FONT><BR/>'
                  f'<FONT COLOR="#CCFBF1" POINT-SIZE="9">{t.row_count:,} rows</FONT>   </TD></TR>')
        lines.append(f'"{name}" [label=<<TABLE BORDER="1" COLOR="#CBD5E1" CELLBORDER="0" CELLSPACING="0" '
                     f'CELLPADDING="5" BGCOLOR="white">{header}{rows}</TABLE>>];')
    for j in s.join_keys:
        lc, rc = j.left_columns[0], j.right_columns[0]
        label = j.cardinality.replace("many-to-one", "N:1").replace("one-to-many", "1:N")
        src = f'"{j.left_table}":"{lc}"' if "(" not in lc else f'"{j.left_table}"'
        dst = f'"{j.right_table}":"{rc}"' if "(" not in rc else f'"{j.right_table}"'
        lines.append(f'{src} -> {dst} [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)
