"""Schema context for the AI Analyst's text-to-SQL pipeline.

Builds one object describing the six payment tables by merging two sources:

* the live DuckDB schema (column names, types, row counts) - the source of truth for what exists;
* the data catalog YAML (descriptions, allowed values, metrics, validity rules).

Usage
-----
    from schema_context import load_schema_context

    ctx = load_schema_context()
    prompt_text = ctx.to_prompt()                       # full context for the LLM
    prompt_text = ctx.to_prompt(["transactions", "banks"])  # only the tables a question needs
    ctx.allowed_columns()                               # {table: {columns}} for the SQL validator
    ctx.VALID_TRANSACTIONS_CTE                          # standard cleaning CTE (rules R1-R4)

Run ``python schema_context.py`` to print the prompt and check catalog/DB consistency.

Only analyst-safe sources are read: the database and the catalog. The evaluator-only files in
``output/eval/`` (ground truth, test questions) are never touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import yaml

DATASET_DIR = Path(__file__).resolve().parent.parent / "payments-analyst-dataset" / "output"
DEFAULT_DB_PATH = DATASET_DIR / "payments.duckdb"
DEFAULT_CATALOG_PATH = DATASET_DIR / "catalog" / "data_catalog.yaml"

TABLES = ["transactions", "merchants", "banks", "payment_methods", "failure_codes", "settlements"]


# --------------------------------------------------------------------------- join keys
@dataclass(frozen=True)
class JoinKey:
    left_table: str
    left_columns: tuple[str, ...]
    right_table: str
    right_columns: tuple[str, ...]
    cardinality: str
    note: str = ""

    def on_clause(self, left_alias: str | None = None, right_alias: str | None = None) -> str:
        la, ra = left_alias or self.left_table, right_alias or self.right_table
        return " AND ".join(f"{la}.{l} = {ra}.{r}" for l, r in zip(self.left_columns, self.right_columns))


JOIN_KEYS = [
    JoinKey("transactions", ("merchant_id",), "merchants", ("merchant_id",), "many-to-one",
            "Some merchant_id values are NULL or unknown: an INNER JOIN silently drops them, so say how many were excluded."),
    JoinKey("transactions", ("payment_method_id",), "payment_methods", ("payment_method_id",), "many-to-one",
            "Join to get method_code (UPI, CREDIT_CARD, ...). Filter on method_code, never on payment_method_id."),
    JoinKey("transactions", ("bank_id",), "banks", ("bank_id",), "many-to-one",
            "bank_id is NULL for WALLET by design; some non-wallet rows also have NULL or unknown bank_id."),
    JoinKey("transactions", ("failure_code",), "failure_codes", ("failure_code",), "many-to-one",
            "Only FAILED rows have a failure_code; some FAILED rows are missing it."),
    JoinKey("transactions", ("parent_transaction_id",), "transactions", ("transaction_id",), "many-to-one",
            "Retry chain: a retry points to the attempt it retries. order_id groups all attempts of one checkout."),
    JoinKey("settlements", ("merchant_id",), "merchants", ("merchant_id",), "many-to-one"),
    JoinKey("settlements", ("merchant_id", "transaction_date"), "transactions", ("merchant_id", "CAST(created_at AS DATE)"),
            "one-to-many", "A batch covers that merchant's SUCCESS transactions on that date. Aggregate transactions first, then join."),
    JoinKey("merchants", ("settlement_bank_id",), "banks", ("bank_id",), "many-to-one"),
    JoinKey("settlements", ("settlement_bank_id",), "banks", ("bank_id",), "many-to-one"),
]

# --------------------------------------------------------------------------- standard SQL snippets
# Implements catalog rules R1-R4. Start every transaction metric query from this CTE.
VALID_TRANSACTIONS_CTE = """valid_txn AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT t.*, pm.method_code,
               CAST(t.created_at AS DATE) AS txn_date,
               row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn
        FROM transactions t
        JOIN payment_methods pm USING (payment_method_id)
    )
    WHERE _rn = 1
      AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING')
      AND amount > 0 AND amount <= 1000000
)"""

KEY_RULES = [
    "Always start transaction metrics from the valid_txn CTE (dedup by latest updated_at, valid status, valid amount).",
    "'Success rate' means SUCCESS / (SUCCESS + FAILED + USER_CANCELLED). PENDING is excluded from rate denominators.",
    "TPV = SUM(amount) over SUCCESS rows only.",
    "Weeks run Monday-Sunday: date_trunc('week', created_at). All timestamps are IST; all amounts are INR.",
    "Use LEFT JOIN to merchants/banks when counting totals, and report rows that could not be attributed.",
    "bank_id IS NULL for WALLET is expected, not a data-quality issue.",
    "To rank an entity overall (e.g. the lowest-performing bank), aggregate at that entity's level first (GROUP BY the "
    "entity alone) and rank those rows. A breakdown by a second dimension (e.g. bank x payment method) is a separate "
    "step and never decides the overall ranking; when both are asked, return the entity's overall figure next to it.",
    "Settlement reconciliation: build expected_net per (merchant_id, transaction_date) from valid SUCCESS transactions "
    "and compare it with settlements on both keys. Gap = net_amount - expected_net: negative = merchant under-paid, "
    "positive = over-paid; |gap| <= 1.00 INR is rounding noise. Keep these apart: amount mismatches (a batch exists), "
    "missing batches (expected, no batch) and transactions whose merchant_id is not in merchants (unattributable data, "
    "not an under-payment). Per merchant report out-of-tolerance batches, total gap and date range; rank under-payment "
    "by total gap ascending, and absolute discrepancy by SUM(abs(gap)) only when asked, labelled as such.",
    "If the question leaves the time window or denominator unclear, state the assumption you made.",
    "Read-only: generate a single SELECT (WITH allowed). Never INSERT, UPDATE, DELETE, CREATE, DROP or ATTACH.",
]


# --------------------------------------------------------------------------- data classes
@dataclass
class ColumnInfo:
    name: str
    db_type: str
    description: str = ""
    allowed_values: str = ""
    nullable: bool = True


@dataclass
class TableInfo:
    name: str
    description: str
    grain: str
    primary_key: str
    row_count: int
    columns: list[ColumnInfo] = field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class SchemaContext:
    tables: dict[str, TableInfo]
    join_keys: list[JoinKey]
    metrics: list[dict]
    validity_rules: list[dict]
    conventions: dict[str, str]
    key_rules: list[str]
    warnings: list[str] = field(default_factory=list)
    VALID_TRANSACTIONS_CTE: str = VALID_TRANSACTIONS_CTE

    # ---- lookups for the SQL validator ------------------------------------------------
    def allowed_tables(self) -> set[str]:
        return set(self.tables)

    def allowed_columns(self) -> dict[str, set[str]]:
        return {name: set(t.column_names) for name, t in self.tables.items()}

    def joins_for(self, tables: list[str]) -> list[JoinKey]:
        """Join keys whose both ends are in `tables`."""
        wanted = set(tables)
        return [j for j in self.join_keys if j.left_table in wanted and j.right_table in wanted]

    # ---- prompt rendering ---------------------------------------------------------------
    def to_prompt(self, tables: list[str] | None = None, include_metrics: bool = True) -> str:
        """Compact, LLM-friendly text. Pass `tables` to include only what a question needs."""
        names = [t for t in (tables or TABLES) if t in self.tables]
        out = ["# Database: DuckDB (SQL dialect: DuckDB)", "", "## Conventions"]
        out += [f"- {k}: {v}" for k, v in self.conventions.items()]
        out += ["", "## Tables"]
        for n in names:
            t = self.tables[n]
            out += ["", f"### {t.name} ({t.row_count:,} rows) - {t.description}",
                    f"Grain: {t.grain}. Primary key: {t.primary_key}."]
            for c in t.columns:
                extra = f" [values: {c.allowed_values}]" if c.allowed_values else ""
                out.append(f"- {c.name} {c.db_type}: {c.description}{extra}")
        joins = self.joins_for(names)
        if joins:
            out += ["", "## Join keys"]
            for j in joins:
                note = f" -- {j.note}" if j.note else ""
                out.append(f"- {j.on_clause()} ({j.cardinality}){note}")
        out += ["", "## Data-quality rules"] + [f"- {r['id']}: {r['rule']}" for r in self.validity_rules]
        if include_metrics:
            out += ["", "## Metric definitions (compute on valid_txn)"]
            for m in self.metrics:
                note = f" Note: {m['notes']}" if m.get("notes") else ""
                out.append(f"- {m['name']}: {m['definition']} SQL: {m['sql']}.{note}")
        out += ["", "## Standard CTE for valid transactions", "WITH " + self.VALID_TRANSACTIONS_CTE]
        out += ["", "## Rules for writing SQL"] + [f"- {r}" for r in self.key_rules]
        return "\n".join(out)


# --------------------------------------------------------------------------- loader
def _read_db_schema(db_path: Path) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
                              WHERE table_schema = 'main' ORDER BY table_name, ordinal_position""").fetchall()
        cols: dict[str, list[tuple[str, str]]] = {}
        for table, col, dtype in rows:
            cols.setdefault(table, []).append((col, dtype))
        counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in cols}
        return cols, counts
    finally:
        con.close()


def load_schema_context(db_path: str | Path = DEFAULT_DB_PATH,
                        catalog_path: str | Path = DEFAULT_CATALOG_PATH) -> SchemaContext:
    db_path, catalog_path = Path(db_path), Path(catalog_path)
    db_cols, counts = _read_db_schema(db_path)
    with open(catalog_path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    warnings: list[str] = []
    tables: dict[str, TableInfo] = {}
    for name in TABLES:
        if name not in db_cols:
            raise ValueError(f"Table '{name}' is missing from {db_path.name}")
        cat_table = catalog["tables"].get(name, {})
        cat_cols = {c["name"]: c for c in cat_table.get("columns", [])}
        columns = []
        for col, dtype in db_cols[name]:                     # DB order and types win
            c = cat_cols.get(col)
            if c is None:
                warnings.append(f"{name}.{col} exists in the DB but not in the catalog")
                c = {}
            elif c.get("type") and c["type"] != dtype:
                warnings.append(f"{name}.{col}: catalog type {c['type']} != DB type {dtype}")
            columns.append(ColumnInfo(col, dtype, c.get("description", ""),
                                      str(c.get("allowed_values_or_notes") or ""), c.get("nullable", True)))
        for missing in set(cat_cols) - {c for c, _ in db_cols[name]}:
            warnings.append(f"{name}.{missing} is in the catalog but not in the DB (ignored)")
        tables[name] = TableInfo(name, cat_table.get("description", ""), cat_table.get("grain", ""),
                                 cat_table.get("primary_key", ""), counts[name], columns)

    for j in JOIN_KEYS:                                       # join keys must point at real columns
        for table, cols in ((j.left_table, j.left_columns), (j.right_table, j.right_columns)):
            for col in cols:
                if "(" not in col and col not in tables[table].column_names:
                    warnings.append(f"join key {table}.{col} not found in DB")

    return SchemaContext(tables=tables, join_keys=JOIN_KEYS, metrics=catalog.get("metrics", []),
                         validity_rules=catalog.get("validity_rules", []),
                         conventions=catalog.get("conventions", {}), key_rules=KEY_RULES, warnings=warnings)


if __name__ == "__main__":
    ctx = load_schema_context()
    print(ctx.to_prompt())
    print("\n# ---")
    print(f"# {len(ctx.tables)} tables, {sum(len(t.columns) for t in ctx.tables.values())} columns, "
          f"{len(ctx.join_keys)} join keys, {len(ctx.metrics)} metrics, {len(ctx.validity_rules)} rules")
    print("# consistency:", "OK" if not ctx.warnings else "; ".join(ctx.warnings))
