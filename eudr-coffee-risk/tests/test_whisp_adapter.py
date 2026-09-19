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


def test_configured_loss_columns():
    df = whisp_like_frame()
    df["loss_2021_pct"] = [0.0, 0.9]
    df["loss_2022_pct"] = [0.0, 0.3]
    mapped = whisp.to_risk_frame(
        df, loss_columns=["loss_2021_pct", "loss_2022_pct"]
    )
    check("loss columns summed", list(mapped["loss_pct"]), [0.0, 1.2])


def test_end_to_end_into_scoring():
    """The whole point: Whisp output must flow into the existing scorer."""
    mapped = whisp.to_risk_frame(whisp_like_frame())
    scored = scoring.score_dataframe(mapped)
    check("scored rows", len(scored), 2)
    check("risk_tier present", "risk_tier" in scored.columns, True)
    check("whisp_risk survives scoring", "whisp_risk" in scored.columns, True)
    check("no loss -> low tier", set(scored["risk_tier"]), {"low"})
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


def main():
    print("--- envelope parsing ---")
    test_token_extraction()
    test_row_extraction()
    print("\n--- column mapping ---")
    test_column_mapping()
    test_missing_plot_id()
    test_configured_loss_columns()
    print("\n--- integration with scoring/dds ---")
    scored = test_end_to_end_into_scoring()
    test_dds_carries_whisp_risk(scored)
    print("\n--- guards ---")
    test_payload_guards()
    test_missing_api_key()

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
