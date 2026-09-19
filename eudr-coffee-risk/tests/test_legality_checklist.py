# -*- coding: utf-8 -*-
"""Tests for the Article 2(40) legality checklist.

No network, no credentials. Legality is conjunctive, and an unassessed plot
must never read as compliant: those two properties are what these tests pin.

    python tests/test_legality_checklist.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pandas as pd

from eudr_risk import legality_checklist as lc

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def test_areas():
    check("eight areas", len(lc.AREAS), 8)
    codes = [a.code for a in lc.AREAS]
    check("codes unique", len(set(codes)), 8)
    letters = sorted(a.article[-2] for a in lc.AREAS)
    check("articles cover a..h", letters, list("abcdefgh"))
    check("every area has evidence hints",
          all(a.evidence for a in lc.AREAS), True)
    check("every area has a Vietnamese title",
          all(a.title_vi for a in lc.AREAS), True)
    # The areas satellites cannot touch must all be present.
    for code in ("a_land_use_rights", "d_third_party_rights",
                 "e_labour_rights", "f_human_rights", "g_fpic",
                 "h_tax_trade_customs"):
        check(f"area present: {code}", code in lc.AREA_BY_CODE, True)


def test_blank_rows():
    rows = lc.blank_rows(["A", "B"])
    check("rows = plots x areas", len(rows), 16)
    check("status defaults to not_started",
          {r["status"] for r in rows}, {"not_started"})
    check("plot ids stringified", sorted({r["plot_id"] for r in rows}), ["A", "B"])
    check("columns complete",
          set(rows[0].keys()) >= set(lc.CHECKLIST_COLUMNS), True)
    try:
        lc.blank_rows(["A"], default_status="bogus")
        check("bad default rejected", False, True)
    except ValueError:
        check("bad default rejected", True, True)


def test_csv_round_trip():
    path = os.path.join(tempfile.mkdtemp(), "legality.csv")
    lc.write_checklist(["DL-001", "DL-002"], path)
    check("file written", os.path.exists(path), True)
    rows = lc.read_checklist(path)
    check("round trip row count", len(rows), 16)
    check("evidence hints survive",
          any("so do" in r["typical_evidence_vn"] for r in rows), True)

    # A typo in the status column must fail loudly, not be treated as passing.
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    bad = path.replace(".csv", "_bad.csv")
    with open(bad, "w", encoding="utf-8-sig") as fh:
        fh.write(text.replace("not_started", "probably_fine", 1))
    try:
        lc.read_checklist(bad)
        check("unknown status rejected", False, True)
    except ValueError:
        check("unknown status rejected", True, True)


def filled(statuses):
    """Rows for one plot with the given per-area statuses."""
    rows = lc.blank_rows(["P"])
    for row in rows:
        row["status"] = statuses.get(row["area_code"], "verified")
    return rows


def test_summarise_plot():
    all_verified = lc.summarise_plot(filled({}))
    check("all verified -> complete", all_verified["legality_status"], "complete")
    check("nothing outstanding", all_verified["legality_outstanding"], 0)

    one_bad = lc.summarise_plot(filled({"e_labour_rights": "non_compliant"}))
    check("one non_compliant blocks the plot",
          one_bad["legality_status"], "blocked")
    check("blocking area named", one_bad["legality_blocking"], "e_labour_rights")

    on_file = lc.summarise_plot(filled({"a_land_use_rights": "on_file"}))
    check("documents held but unchecked -> documented",
          on_file["legality_status"], "documented")
    check("one outstanding", on_file["legality_outstanding"], 1)

    fresh = lc.summarise_plot(lc.blank_rows(["P"]))
    check("untouched -> incomplete", fresh["legality_status"], "incomplete")
    check("all eight outstanding", fresh["legality_outstanding"], 8)

    na = lc.summarise_plot(filled({"g_fpic": "not_applicable"}))
    check("not_applicable counts as resolved", na["legality_status"], "complete")


def test_readiness():
    check("clean and legal -> ready",
          lc.eudr_readiness("low", "complete")[0], "ready")
    check("ready has no reason", lc.eudr_readiness("low", "complete")[1], "")

    tier_bad = lc.eudr_readiness("high", "complete")
    check("deforestation risk blocks readiness", tier_bad[0], "not_ready")
    check("reason names the tier", "deforestation risk high" in tier_bad[1], True)

    legal_bad = lc.eudr_readiness("low", "incomplete")
    check("legality blocks readiness", legal_bad[0], "not_ready")
    check("reason names legality", "legality incomplete" in legal_bad[1], True)

    both = lc.eudr_readiness("standard", "blocked")
    check("both reasons reported", both[1].count(";"), 1)


def test_merge_into():
    table = pd.DataFrame([
        {"plot_id": "DL-001", "risk_tier": "low"},
        {"plot_id": "DL-002", "risk_tier": "low"},
        {"plot_id": "DL-003", "risk_tier": "high"},
    ])

    # No checklist at all: nothing may read as compliant.
    empty = lc.merge_into(table, [])
    check("unassessed -> not_started",
          set(empty["legality_status"]), {"not_started"})
    check("unassessed counts all areas outstanding",
          set(empty["legality_outstanding"]), {8})
    check("unassessed is never ready",
          set(empty["eudr_readiness"]), {"not_ready"})

    # One plot fully verified, one blocked, one absent from the checklist.
    rows = filled({})
    for row in rows:
        row["plot_id"] = "DL-001"
    blocked = filled({"a_land_use_rights": "non_compliant"})
    for row in blocked:
        row["plot_id"] = "DL-002"

    merged = lc.merge_into(table, rows + blocked)
    by_id = merged.set_index("plot_id")
    check("verified plot complete",
          by_id.loc["DL-001", "legality_status"], "complete")
    check("verified and clean is ready",
          by_id.loc["DL-001", "eudr_readiness"], "ready")
    check("blocked plot blocked",
          by_id.loc["DL-002", "legality_status"], "blocked")
    check("blocked plot not ready",
          by_id.loc["DL-002", "eudr_readiness"], "not_ready")
    check("plot absent from checklist stays not_started",
          by_id.loc["DL-003", "legality_status"], "not_started")
    # DL-003 fails on both halves.
    check("high risk plus unassessed names both",
          by_id.loc["DL-003", "not_ready_reason"].count(";"), 1)


def main():
    print("--- areas ---")
    test_areas()
    print("\n--- blank checklist ---")
    test_blank_rows()
    print("\n--- csv round trip ---")
    test_csv_round_trip()
    print("\n--- roll-up ---")
    test_summarise_plot()
    print("\n--- combined readiness ---")
    test_readiness()
    print("\n--- merge into risk table ---")
    test_merge_into()

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
