"""Usage: python ai_analyst/tests/grade_m0137_live.py ai_analyst/reports/<investigation>.json

Grade an investigation JSON against the independently computed M0137 ground truth.

Each check needs BOTH: the agent said it (in a finding, not only a hypothesis), AND a successful
query the agent itself ran shows it in the result rows. Prose alone does not count.
"""
import json, re, sys

inv = json.load(open(sys.argv[1], encoding="utf-8"))[0]
ok_q = {q["id"]: q for q in inv["queries"] if q["status"] == "ok"}


def rows_text(q):
    return json.dumps([q["columns"]] + q["rows"], default=str).upper()


def finding_mentions(pat):
    return [f for f in inv["findings"] if re.search(pat, f["statement"], re.I)]


def evidence_shows(pat):
    return [qid for qid, q in ok_q.items() if re.search(pat, rows_text(q), re.I)]


checks = {
    "merchant M0137 identified":         (r"M0137|Zestora", r"M0137|ZESTORA"),
    "ANDROID identified as platform":    (r"android", r"ANDROID"),
    "gateway failure spike identified":  (r"GATEWAY", r"GATEWAY_(ERROR|TIMEOUT)"),
    "timing ~2026-10-12 identified":     (r"2026-10-1[2-9]|mid-October|12 Oct|Oct(ober)? 12", r"2026-10-1[2-9]"),
}
print(f"Question: {inv['question']}\nModel: {inv['model']} | success={inv['success']} | rounds={inv['rounds']} | "
      f"queries ok {len(ok_q)}/{len(inv['queries'])} | {inv['elapsed_ms']/1000:.0f}s\n")
passed = 0
for name, (say, show) in checks.items():
    said, shown = finding_mentions(say), evidence_shows(show)
    cited_ok = any(set(f["evidence"]) & set(shown) for f in said)
    status = "PASS" if said and cited_ok else ("PARTIAL" if said or shown else "FAIL")
    passed += status == "PASS"
    print(f"[{status}] {name}: in findings={len(said)}, queries showing it={shown}, finding cites such a query={cited_ok}")
print(f"\n{passed}/{len(checks)} checks passed")
unver = [(f['statement'][:80], f['numbers_unverified']) for f in inv['findings'] if f['numbers_unverified']]
print("Findings with untraceable numbers:", unver or "none")
