"""Stress tests for automatic change-date detection (driver_scan.build_change_point_query).

All experiments run on throwaway COPIES of payments.duckdb in a temp folder; the dataset itself is
never modified. No LLM is involved: this tests the deterministic SQL only.

    python -m ai_analyst.tests.stress_change_detection            (about 1-2 minutes)

1. False positives / multiple comparisons: outcomes are shuffled across time inside each
   merchant x platform x method x bank cell, which keeps every segment's overall success rate but
   destroys any time pattern. Any break found there is a false positive. Repeating with many
   shuffles gives the null distribution of the strongest z-score over all segments x days.
2. Low-volume segments: the same null copies show whether small segments crowd the top; known
   breaks are then injected into one segment at a time (different volumes and sizes) to measure
   what the detector can and cannot see.
3. Recovered incidents: the real data is cut at different "as of" dates around the planted SBI UPI
   outage (week of 2026-09-14) to see what is reported while it is ongoing and after it recovered.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import pandas as pd

from ai_analyst.driver_scan import DEFAULT_Z_THRESHOLD, build_change_point_query
from ai_analyst.executor import QueryExecutor
from ai_analyst.schema_context import DEFAULT_DB_PATH, load_schema_context
from ai_analyst.sql_validator import SQLValidator

SCHEMA = load_schema_context()
VALIDATOR = SQLValidator(SCHEMA.allowed_tables())
CELL = "merchant_id, device_platform, payment_method_id, bank_id"


# ----------------------------------------------------------------------------- copies of the database
def copy_db(tmp: Path, name: str) -> Path:
    dst = tmp / f"{name}.duckdb"
    shutil.copyfile(DEFAULT_DB_PATH, dst)
    return dst


def shuffle_outcomes_in_time(path: Path, seed: float) -> None:
    """Null data: permute (status, failure_code, response_time_ms) across rows within each cell."""
    con = duckdb.connect(str(path))
    con.execute("SET threads = 1")
    con.execute(f"SELECT setseed({seed})")
    con.execute(f"""
        CREATE TABLE tx_null AS
        WITH a AS (SELECT *, row_number() OVER (PARTITION BY {CELL} ORDER BY transaction_id, updated_at) AS _k
                   FROM transactions),
             r AS (SELECT {CELL}, status, failure_code, response_time_ms, random() AS _r FROM transactions),
             b AS (SELECT * EXCLUDE (_r), row_number() OVER (PARTITION BY {CELL} ORDER BY _r) AS _k FROM r)
        SELECT a.* EXCLUDE (_k) REPLACE (b.status AS status, b.failure_code AS failure_code,
                                          b.response_time_ms AS response_time_ms)
        FROM a JOIN b ON a._k = b._k
          AND a.merchant_id IS NOT DISTINCT FROM b.merchant_id AND a.device_platform IS NOT DISTINCT FROM b.device_platform
          AND a.payment_method_id IS NOT DISTINCT FROM b.payment_method_id AND a.bank_id IS NOT DISTINCT FROM b.bank_id""")
    n_before = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    con.execute("DELETE FROM transactions")
    con.execute("INSERT INTO transactions SELECT * FROM tx_null")
    con.execute("DROP TABLE tx_null")
    assert con.execute("SELECT count(*) FROM transactions").fetchone()[0] == n_before
    con.close()


def truncate_at(path: Path, as_of: str) -> None:
    con = duckdb.connect(str(path))
    con.execute(f"DELETE FROM transactions WHERE created_at >= TIMESTAMP '{as_of}'")
    con.close()


def inject_break(path: Path, merchant_id: str, platform: str, start: str, fail_prob: float, seed: float) -> None:
    """Turn a share of SUCCESS attempts into GATEWAY_ERROR failures for one segment from `start`."""
    con = duckdb.connect(str(path))
    con.execute("SET threads = 1")
    con.execute(f"SELECT setseed({seed})")
    con.execute(f"""UPDATE transactions SET status = 'FAILED', failure_code = 'GATEWAY_ERROR'
                    WHERE merchant_id = '{merchant_id}' AND device_platform = '{platform}'
                      AND created_at >= TIMESTAMP '{start}' AND status = 'SUCCESS' AND random() < {fail_prob}""")
    con.close()


def detect(path: Path, **kw) -> pd.DataFrame:
    """Raw ranking (no significance filter) so experiments can see what the threshold removes."""
    kw.setdefault("z_threshold", None)
    q = build_change_point_query(SCHEMA, top_k=kw.pop("top_k", 50), **kw)
    check = VALIDATOR.validate(q.sql)
    assert check.ok, check.errors
    df = QueryExecutor(db_path=path, max_rows=500).run(check.sql).to_dataframe()
    df["change_date"] = df["change_date"].astype(str).str[:10]
    return df


# ----------------------------------------------------------------------------- experiments
def null_runs(tmp: Path, n_runs: int) -> pd.DataFrame:
    rows = []
    for i in range(n_runs):
        p = copy_db(tmp, f"null{i}")
        shuffle_outcomes_in_time(p, seed=(i + 1) / (n_runs + 1))
        df = detect(p)
        top = df.iloc[0]
        rows.append(dict(run=i, top_segment=top["segment"], dimension=top["dimension"], change_date=top["change_date"],
                         n_before=int(top["n_before"]), n_after=int(top["n_after"]), sr_change=top["sr_change"],
                         min_z=top["z_score"], n_below_4=int((df["z_score"] <= -4).sum()),
                         n_below_5=int((df["z_score"] <= -5).sum())))
        p.unlink()
    return pd.DataFrame(rows)


def injected_breaks(tmp: Path, targets: list[tuple[str, str, float]], seed_base: float = 0.37) -> pd.DataFrame:
    """Each target: (merchant_id, platform, fail_prob). Injected into a NULL copy so it is the only real signal."""
    rows = []
    for j, (mid, plat, prob) in enumerate(targets):
        p = copy_db(tmp, f"inject{j}")
        shuffle_outcomes_in_time(p, seed=seed_base)
        inject_break(p, mid, plat, "2026-10-12", prob, seed=0.5)
        df = detect(p)
        seg = f"{mid} / {plat}"
        hit = df[df["segment"] == seg]
        con = duckdb.connect(str(p), read_only=True)
        vol = con.execute(f"""SELECT count(*) FROM transactions WHERE merchant_id = '{mid}' AND device_platform = '{plat}'
                              AND created_at >= TIMESTAMP '2026-10-12'""").fetchone()[0]
        con.close()
        merch = df[(df["dimension"] == "merchant") & df["segment"].str.startswith(mid)]
        rows.append(dict(segment=seg, attempts_after=vol, injected_fail_prob=prob,
                         merchant_rank=int(merch.index[0]) + 1 if not merch.empty else None,
                         merchant_z=merch.iloc[0]["z_score"] if not merch.empty else None,
                         found=not hit.empty,
                         rank=int(hit.index[0]) + 1 if not hit.empty else None,
                         change_date=hit.iloc[0]["change_date"] if not hit.empty else None,
                         sr_change=hit.iloc[0]["sr_change"] if not hit.empty else None,
                         z=hit.iloc[0]["z_score"] if not hit.empty else None,
                         reported=bool(not hit.empty and hit.iloc[0]["z_score"] <= DEFAULT_Z_THRESHOLD
                                       and hit.iloc[0]["change_date"] in ("2026-10-11", "2026-10-12", "2026-10-13")),
                         top_segment=df.iloc[0]["segment"], top_z=df.iloc[0]["z_score"]))
        p.unlink()
    return pd.DataFrame(rows)


def recovered_incident(tmp: Path, as_of_dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in as_of_dates:
        p = copy_db(tmp, f"asof_{d}")
        truncate_at(p, d)
        df = detect(p)
        sbi = df[df["segment"] == "SBI / UPI"]
        rows.append(dict(as_of=d, top_segment=df.iloc[0]["segment"], top_change_date=df.iloc[0]["change_date"],
                         top_z=df.iloc[0]["z_score"],
                         sbi_upi_rank=int(sbi.index[0]) + 1 if not sbi.empty else None,
                         sbi_upi_change_date=sbi.iloc[0]["change_date"] if not sbi.empty else None,
                         sbi_upi_sr_before=sbi.iloc[0]["sr_before"] if not sbi.empty else None,
                         sbi_upi_sr_after=sbi.iloc[0]["sr_after"] if not sbi.empty else None,
                         sbi_upi_z=sbi.iloc[0]["z_score"] if not sbi.empty else None,
                         sbi_upi_sr_first_7d=sbi.iloc[0]["sr_first_7d"] if not sbi.empty else None,
                         sbi_upi_sr_last_7d=sbi.iloc[0]["sr_last_7d"] if not sbi.empty else None,
                         sbi_upi_status=sbi.iloc[0]["status"] if not sbi.empty else None,
                         reported_breaks=int((df["z_score"] <= DEFAULT_Z_THRESHOLD).sum())))
        p.unlink()
    return pd.DataFrame(rows)


def main(n_null: int = 20) -> int:
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", None)
    tmp = Path(tempfile.mkdtemp(prefix="cp_stress_"))
    t0 = time.perf_counter()
    checks: list[tuple[str, bool, str]] = []
    try:
        real = detect(DEFAULT_DB_PATH)
        reported = real[real["z_score"] <= DEFAULT_Z_THRESHOLD]
        print(f"== Real data: breaks passing z <= {DEFAULT_Z_THRESHOLD}\n",
              reported[["dimension", "segment", "change_date", "n_before", "n_after", "sr_before", "sr_first_7d",
                        "sr_last_7d", "z_score", "status"]].to_string(index=False))
        print("   next strongest (suppressed):",
              ", ".join(f"{r.segment} {r.z_score}" for r in real[real["z_score"] > DEFAULT_Z_THRESHOLD].head(4).itertuples()), "\n")
        segs = set(reported["segment"])
        planted = {"M0137 / ANDROID", "M0137 Zestora Online Mart", "SBI / UPI", "SBI"}
        checks.append(("real data: only planted incidents reported", segs <= planted and "M0137 / ANDROID" in segs,
                       ", ".join(sorted(segs))))
        top = reported.iloc[0]
        checks.append(("real data: M0137 / ANDROID first, 2026-10-12, ongoing",
                       top["segment"] == "M0137 / ANDROID" and top["change_date"] == "2026-10-12" and top["status"] == "ongoing",
                       f"{top['segment']} {top['change_date']} {top['status']}"))

        nulls = null_runs(tmp, n_null)
        print(f"== Null data ({n_null} time-shuffled copies): strongest (spurious) break per copy\n", nulls.to_string(index=False))
        fwer = (nulls["min_z"] <= DEFAULT_Z_THRESHOLD).mean()
        print(f"\nNull strongest z: median {nulls['min_z'].median():.2f}, 5th pct {nulls['min_z'].quantile(0.05):.2f}, "
              f"worst {nulls['min_z'].min():.2f}")
        for thr in (-4, -4.5, -5):
            print(f"  share of null copies with a spurious break at z <= {thr}: {(nulls['min_z'] <= thr).mean():.0%}")
        print(f"  top spurious hit volume after change: median {nulls['n_after'].median():.0f}, min {nulls['n_after'].min()}\n")
        checks.append((f"null data: family-wise false-positive rate at z <= {DEFAULT_Z_THRESHOLD} <= 5%", fwer <= 0.05,
                       f"{fwer:.0%} of {n_null} copies"))

        # volumes after 2026-10-12: M0027/IOS 48 (below the 100-attempt floor), M0280/ANDROID 109,
        # M0172/POS_TERMINAL 185, M0045/ANDROID 342
        inj = injected_breaks(tmp, [
            ("M0027", "IOS", 0.30),
            ("M0280", "ANDROID", 0.30), ("M0280", "ANDROID", 0.10),
            ("M0172", "POS_TERMINAL", 0.30), ("M0172", "POS_TERMINAL", 0.10),
            ("M0045", "ANDROID", 0.30), ("M0045", "ANDROID", 0.10), ("M0045", "ANDROID", 0.05),
        ])
        print("== Breaks injected on 2026-10-12 into a null copy (the only real signal)\n", inj.to_string(index=False), "\n")
        big = inj[(inj["injected_fail_prob"] >= 0.30) & (inj["attempts_after"] >= 100)]
        checks.append(("injected: ~30% failure jump on >=100-attempt segment reported with correct date",
                       bool(big["reported"].all()), f"{int(big['reported'].sum())}/{len(big)}"))
        low = inj[inj["attempts_after"] < 100]
        checks.append(("injected: segment below the 100-attempt floor is not reported", not bool(low["reported"].any()),
                       f"{int(low['reported'].sum())}/{len(low)} reported"))

        rec = recovered_incident(tmp, ["2026-09-21", "2026-09-28", "2026-10-05", "2026-10-26"])
        print("== Recovered incident (SBI UPI outage, week of 2026-09-14), data cut at different dates\n",
              rec.to_string(index=False))
        status = dict(zip(rec["as_of"], rec["sbi_upi_status"]))
        checks.append(("recovered: SBI / UPI 'ongoing' while the outage is the latest week",
                       status.get("2026-09-21") == "ongoing", str(status.get("2026-09-21"))))
        checks.append(("recovered: SBI / UPI 'recovered' once the outage has ended",
                       all(status.get(d) == "recovered" for d in ("2026-09-28", "2026-10-05", "2026-10-26")),
                       ", ".join(f"{d}: {status.get(d)}" for d in ("2026-09-28", "2026-10-05", "2026-10-26"))))
        first7 = rec.set_index("as_of")["sbi_upi_sr_first_7d"]
        checks.append(("recovered: incident size kept (first-7-day SR within 1pp of the outage-week 62.61%)",
                       bool(((first7 - 0.6261).abs() <= 0.01).all()), ", ".join(f"{v:.4f}" for v in first7)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== Checks")
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
    print(f"\n{sum(ok for _, ok, _ in checks)}/{len(checks)} checks passed ({time.perf_counter() - t0:.0f}s)")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20))
