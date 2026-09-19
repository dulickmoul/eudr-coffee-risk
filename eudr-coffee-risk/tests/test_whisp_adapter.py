# -*- coding: utf-8 -*-
"""Tests for the Whisp adapter: response-envelope parsing and column mapping.

No network and no API key: every test feeds a synthetic payload. The point is
to prove the adapter survives the shapes Whisp might return and never invents
a measurement it did not receive.

    pip install pandas
    python tests/test_whisp_adapter.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pandas as pd

from eudr_risk import scoring, whisp

FAILS = []

# A real Whisp 3.0.0a17 response for the three Di Linh sample plots,
# 257 columns, fetched from the API. This is why the column names in
# config.py are facts rather than guesses.
FIXTURE = os.path.join(HERE, "fixtures", "whisp_result_sample.csv")


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def test_token_extraction():
    check("token at top level", whisp._extract_token({"token": "abc"}), "abc")
    check("token as jobId", whisp._extract_token({"jobId": "j1"}), "j1")
    check("token nested in data",
          whisp._extract_token({"data": {"token": "deep"}}), "deep")
    check("no token -> None", whisp._extract_token({"status": "queued"}), None)
    check("empty string ignored", whisp._extract_token({"token": ""}), None)


def test_row_extraction():
    check("bare list", len(whisp._extract_rows([{"a": 1}, {"a": 2}])), 2)
    check("under data", len(whisp._extract_rows({"data": [{"a": 1}]})), 1)
    check("under results", len(whisp._extract_rows({"results": [{"a": 1}]})), 1)
    check("nested dict", len(whisp._extract_rows({"data": {"rows": [{"a": 1}]}})), 1)
    check("no rows", whisp._extract_rows({"status": "running"}), [])

    fc = {
        "features": [
            {"properties": {"plotId": "P1", "Area": 2.0},
             "geometry": {"type": "Point", "coordinates": [108.07, 11.58]}},
        ]
    }
    rows = whisp._extract_rows(fc)
    check("geojson -> 1 row", len(rows), 1)
    check("properties flattened", rows[0]["plotId"], "P1")
    check("geometry preserved", rows[0]["_geometry"]["type"], "Point")


def whisp_like_frame():
    """Column names as seen in the Whisp result schema (plotId, Area, Risk_PCrop)."""
    return pd.DataFrame(
        [
            {"plotId": "DL-001", "Area": 1.6, "Unit": "ha", "Country": "VNM",
             "GFC_TC_2020": 12.0, "Risk_PCrop": "low"},
            {"plotId": "DL-002", "Area": 5.0, "Unit": "ha", "Country": "VNM",
             "GFC_TC_2020": 61.0, "Risk_PCrop": "high"},
        ]
    )


def test_column_mapping():
    mapped = whisp.to_risk_frame(whisp_like_frame())
    check("plotId -> plot_id", "plot_id" in mapped.columns, True)
    check("Area -> area_ha", "area_ha" in mapped.columns, True)
    check("plot_id values", list(mapped["plot_id"]), ["DL-001", "DL-002"])
    check("area numeric", float(mapped["area_ha"].iloc[1]), 5.0)
    check("whisp verdict carried", list(mapped["whisp_risk"]), ["low", "high"])
    # Columns we never measured must not be fabricated.
    check("no invented radd column", "radd_alert_ha" in mapped.columns, False)
    check("loss_pct not invented", "loss_pct" in mapped.columns, False)
    # Whisp's own extra columns survive untouched.
    check("extra column preserved", "GFC_TC_2020" in mapped.columns, True)


def test_missing_plot_id():
    df = pd.DataFrame([{"Area": 1.0}, {"Area": 2.0}])
    mapped = whisp.to_risk_frame(df)
    check("synthesised plot ids", list(mapped["plot_id"]), ["row-0", "row-1"])


def test_loss_aggregation():
    """Loss arrives as hectares per dataset and becomes a percent of area.

    Default is max, not sum: the datasets detect the same clearing events, so
    summing would report one clearing several times over.
    """
    df = whisp_like_frame()  # areas 1.6 and 5.0 ha
    df["GFC_loss_after_2020"] = [0.0, 0.9]
    df["TMF_def_after_2020"] = [0.0, 0.3]
    cols = ["GFC_loss_after_2020", "TMF_def_after_2020"]

    as_max = whisp.to_risk_frame(df, loss_columns=cols, loss_aggregation="max")
    check("max picks the largest estimate", list(as_max["loss_ha"]), [0.0, 0.9])
    check("converted to percent of area",
          [round(v, 1) for v in as_max["loss_pct"]], [0.0, 18.0])

    as_sum = whisp.to_risk_frame(df, loss_columns=cols, loss_aggregation="sum")
    check("sum double-counts", list(as_sum["loss_ha"]), [0.0, 1.2])

    # Zero area must not divide by zero.
    zero = whisp_like_frame()
    zero["Area"] = [0.0, 0.0]
    zero["GFC_loss_after_2020"] = [0.5, 0.5]
    safe = whisp.to_risk_frame(zero, loss_columns=["GFC_loss_after_2020"])
    check("zero area gives 0 percent, not inf", list(safe["loss_pct"]), [0.0, 0.0])


def test_end_to_end_into_scoring():
    """The whole point: Whisp output must flow into the existing scorer."""
    mapped = whisp.to_risk_frame(whisp_like_frame())
    scored = scoring.score_dataframe(mapped)
    check("scored rows", len(scored), 2)
    check("risk_tier present", "risk_tier" in scored.columns, True)
    check("whisp_risk survives scoring", "whisp_risk" in scored.columns, True)
    # Verdicts in the fake frame are low and high, and they drive the tiers.
    check("tiers follow Whisp verdicts",
          dict(zip(scored["plot_id"], scored["risk_tier"])),
          {"DL-001": "low", "DL-002": "high"})
    summary = scoring.summarise(scored)
    check("summary area", round(summary["area_ha"], 1), 6.6)
    return scored


def test_dds_carries_whisp_risk(scored):
    from eudr_risk import dds

    geoms = {
        pid: {"type": "Point", "coordinates": [108.07, 11.58]}
        for pid in scored["plot_id"]
    }
    doc = dds.build_dds(scored, geoms, harvest_year=2026)
    props = doc["features"][0]["properties"]
    check("whisp_risk in DDS props", "whisp_risk" in props, True)
    check("json serialisable", isinstance(json.dumps(doc), str), True)


def test_payload_guards():
    try:
        whisp._check_payload({"features": []})
        check("empty geojson rejected", False, True)
    except whisp.WhispError:
        check("empty geojson rejected", True, True)

    too_many = {"features": [{"x": 1}] * (whisp.WHISP_GEOMETRY_LIMIT_ASYNC + 1)}
    try:
        whisp._check_payload(too_many)
        check("over-limit rejected", False, True)
    except whisp.WhispError:
        check("over-limit rejected", True, True)

    ok = {"features": [{"type": "Feature", "properties": {}, "geometry": None}] * 10}
    check("valid payload counted", whisp._check_payload(ok), 10)


def test_missing_api_key():
    saved = os.environ.pop("WHISP_API_KEY", None)
    try:
        whisp.api_key_from_env()
        check("missing key raises", False, True)
    except whisp.WhispError:
        check("missing key raises", True, True)
    finally:
        if saved is not None:
            os.environ["WHISP_API_KEY"] = saved


def test_real_fixture():
    """End to end over a real Whisp response."""
    if not os.path.exists(FIXTURE):
        print(f"SKIP  fixture missing: {FIXTURE}")
        return None

    raw = pd.read_csv(FIXTURE)
    check("fixture rows", len(raw), 3)
    check("fixture is wide", len(raw.columns) > 250, True)

    mapped = whisp.to_risk_frame(raw)
    check("plotId mapped", sorted(mapped["plot_id"]), ["1", "2", "3"])
    areas = dict(zip(mapped["plot_id"], mapped["area_ha"].round(2)))
    check("areas match Whisp", areas, {"1": 1.59, "2": 5.05, "3": 2.55})
    check("verdicts read", sorted(set(mapped["whisp_risk"])), ["low"])
    check("coffee area mapped", round(float(
        mapped.loc[mapped.plot_id == "2", "coffee_ha"].iloc[0]), 2), 2.41)
    check("radd mapped and zero",
          float(mapped["radd_alert_ha"].sum()), 0.0)

    # Plot 2 really does have post-cutoff GFC loss: 0.076 ha of 5.045 ha.
    row2 = mapped.loc[mapped.plot_id == "2"].iloc[0]
    check("plot 2 loss_ha", round(float(row2["loss_ha"]), 3), 0.076)
    check("plot 2 loss_pct", round(float(row2["loss_pct"]), 2), 1.51)
    check("plot 2 disturbance indicator is no",
          bool(row2["whisp_disturbance_after_2020"]), False)

    scored = scoring.score_dataframe(mapped)
    tiers = dict(zip(scored["plot_id"], scored["risk_tier"]))
    # The whole point: 1.51% loss is over our 0.5% "high" threshold, but the
    # land was not forest in 2020, so the correct answer is low.
    check("plot 2 tier defers to Whisp", tiers["2"], "low")
    check("all plots low", set(tiers.values()), {"low"})
    check("nothing flagged", int(scored["deforestation_flag"].sum()), 0)
    check("summary all clean", scoring.summarise(scored)["pct_clean"], 100.0)
    return mapped


def test_heuristic_would_over_flag(mapped):
    """Negative control: without Whisp's verdict our heuristic over-flags.

    This is the bug the deference in assign_tier exists to prevent, pinned
    down so nobody "simplifies" it away later.
    """
    if mapped is None:
        print("SKIP  no fixture")
        return
    blind = mapped.drop(columns=["whisp_risk", "whisp_disturbance_after_2020"])
    blind_tiers = dict(
        zip(*[scoring.score_dataframe(blind)[c] for c in ("plot_id", "risk_tier")])
    )
    with_verdict = dict(
        zip(*[scoring.score_dataframe(mapped)[c] for c in ("plot_id", "risk_tier")])
    )
    check("without the verdict plot 2 is over-flagged", blind_tiers["2"], "high")
    check("with the verdict plot 2 is correctly low", with_verdict["2"], "low")


def test_tier_vocabulary():
    check("low", scoring.whisp_tier("low"), "low")
    check("high", scoring.whisp_tier("high"), "high")
    check("more_info_needed", scoring.whisp_tier("more_info_needed"), "standard")
    check("hyphen variant", scoring.whisp_tier("more-info-needed"), "standard")
    check("mixed case", scoring.whisp_tier("  HIGH "), "high")
    # An unknown verdict must not be treated as clean.
    check("unknown fails safe to standard",
          scoring.whisp_tier("brand_new_category"), "standard")


def main():
    print("--- envelope parsing ---")
    test_token_extraction()
    test_row_extraction()
    print("\n--- column mapping ---")
    test_column_mapping()
    test_missing_plot_id()
    test_loss_aggregation()
    print("\n--- integration with scoring/dds ---")
    scored = test_end_to_end_into_scoring()
    test_dds_carries_whisp_risk(scored)
    print("\n--- guards ---")
    test_payload_guards()
    test_missing_api_key()
    print("\n--- verdict vocabulary ---")
    test_tier_vocabulary()
    print("\n--- real Whisp response fixture ---")
    mapped = test_real_fixture()
    test_heuristic_would_over_flag(mapped)

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
