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

# Real Whisp rows from probe plots on the Lam Dong forest frontier, found by
# grid search. Contains the cases that pin both failure modes: plots Whisp
# calls low despite heavy post-2020 clearing, and plots with loss that is
# genuinely not EUDR-relevant.
FRONTIER = os.path.join(HERE, "fixtures", "whisp_frontier_sample.csv")


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

    # The EUDR geolocation rule is ours to apply; Whisp reports area only.
    geoms = dict(zip(mapped["plot_id"], mapped["eudr_geom"]))
    check("5.05 ha needs a polygon", geoms["2"], "polygon")
    check("1.59 ha may use a point", geoms["1"], "point")
    check("2.55 ha may use a point", geoms["3"], "point")

    scored = scoring.score_dataframe(mapped)
    tiers = dict(zip(scored["plot_id"], scored["risk_tier"]))
    # The whole point: 1.51% loss is over our 0.5% "high" threshold, but the
    # land was not forest in 2020, so the correct answer is low.
    check("plot 2 tier defers to Whisp", tiers["2"], "low")
    check("all plots low", set(tiers.values()), {"low"})
    check("nothing flagged", int(scored["deforestation_flag"].sum()), 0)

    # Score must be gated like the tier, or the report says "low risk, 50".
    scores = dict(zip(scored["plot_id"], scored["risk_score"]))
    check("cleared plot scores zero, not 50", scores["2"], 0.0)
    check("no plot scores above zero", float(scored["risk_score"].max()), 0.0)

    summary = scoring.summarise(scored)
    check("summary all clean", summary["pct_clean"], 100.0)
    check("polygon requirement counted", summary["polygon_required"], 1)
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


def test_chunking():
    """One job takes at most 5,000 geometries, so portfolios must be batched."""
    def collection(n):
        return {"type": "FeatureCollection", "name": "big",
                "features": [{"id": i} for i in range(n)]}

    batches = list(whisp.chunk_geojson(collection(12), size=5))
    check("12 features into 5s gives 3 batches", len(batches), 3)
    check("batch sizes", [len(b["features"]) for _, b in batches], [5, 5, 2])
    check("batches numbered from 1", [n for n, _ in batches], [1, 2, 3])
    check("no feature lost",
          sum(len(b["features"]) for _, b in batches), 12)
    check("foreign members preserved", batches[0][1]["name"], "big")
    check("type preserved", batches[0][1]["type"], "FeatureCollection")

    check("exact multiple", len(list(whisp.chunk_geojson(collection(10), size=5))), 2)
    check("single batch under size",
          len(list(whisp.chunk_geojson(collection(3), size=5))), 1)

    # The real case: 16,000 farms at the 5,000 ceiling.
    check("16,000 farms needs 4 jobs", whisp.chunk_count(collection(16000)), 4)

    try:
        list(whisp.chunk_geojson(collection(0), size=5))
        check("empty rejected", False, True)
    except whisp.WhispError:
        check("empty rejected", True, True)
    try:
        list(whisp.chunk_geojson(collection(3), size=0))
        check("zero chunk size rejected", False, True)
    except whisp.WhispError:
        check("zero chunk size rejected", True, True)


def test_analysis_options():
    """Field names must match the OpenAPI AnalysisOptionsInput exactly."""
    opts = whisp.build_analysis_options()
    check("externalIdColumn sent by default",
          opts.get("externalIdColumn"), "plot_id")
    check("unitType asks for hectares", opts.get("unitType"), "ha")
    check("async omitted when unset", "async" in opts, False)
    check("audit trail omitted when off", "geometryAuditTrail" in opts, False)

    full = whisp.build_analysis_options(
        external_id_column="farm_code", unit_type=None, run_async=True,
        national_codes=["co"], geometry_audit_trail=True,
    )
    check("custom id column", full["externalIdColumn"], "farm_code")
    check("unitType dropped when None", "unitType" in full, False)
    check("async passed through", full["async"], True)
    check("nationalCodes is a list", full["nationalCodes"], ["co"])
    check("audit trail flag", full["geometryAuditTrail"], True)

    none_opts = whisp.build_analysis_options(
        external_id_column=None, unit_type=None
    )
    check("nothing sent when all disabled", none_opts, {})


def test_external_id_preferred():
    """Our id wins when Whisp echoes it back; otherwise fall back per row."""
    df = pd.DataFrame([
        {"plotId": 1, "external_id": "DL-001", "Area": 1.6},
        {"plotId": 2, "external_id": "DL-002", "Area": 5.0},
    ])
    mapped = whisp.to_risk_frame(df)
    check("external_id used as plot_id",
          list(mapped["plot_id"]), ["DL-001", "DL-002"])
    check("whisp plotId kept for traceability",
          list(mapped["whisp_plot_id"]), ["1", "2"])


def test_external_id_blank_falls_back():
    """A blank external_id must not become the literal string 'null'."""
    df = pd.DataFrame([
        {"plotId": 1, "external_id": "null", "Area": 1.6},
        {"plotId": 2, "external_id": "", "Area": 5.0},
        {"plotId": 3, "external_id": "DL-003", "Area": 2.5},
    ])
    mapped = whisp.to_risk_frame(df)
    check("blank rows fall back to plotId, mixed rows keep theirs",
          list(mapped["plot_id"]), ["1", "2", "DL-003"])


def test_severity_helpers():
    check("worst of two", scoring.most_severe("low", "high"), "high")
    check("standard beats low", scoring.most_severe("low", "standard"), "standard")
    check("high beats standard", scoring.most_severe("standard", "high"), "high")
    check("None ignored", scoring.most_severe(None, "standard"), "standard")
    check("all None -> low", scoring.most_severe(None, None), "low")
    check("unknown counts as standard",
          scoring.most_severe("low", "weird"), "weird")


def test_frontier_fixture():
    """Both failure modes, pinned against real forest-frontier data.

    Deferring to Whisp's verdict alone under-flags; trusting our own
    hectares alone over-flags. assign_tier must take the worse of the two.
    """
    if not os.path.exists(FRONTIER):
        print(f"SKIP  frontier fixture missing: {FRONTIER}")
        return

    raw = pd.read_csv(FRONTIER)
    scored = scoring.score_dataframe(whisp.to_risk_frame(raw))
    by_id = scored.set_index("plot_id")

    # Under-flagging case: heavy clearing, radar alerts, Whisp's own Ind_04
    # says yes, but risk_pcrop came back low.
    r7c3 = by_id.loc["TADUNG-r7c3"]
    check("r7c3 verdict really is low", r7c3["whisp_risk"], "low")
    check("r7c3 disturbance confirmed",
          bool(r7c3["whisp_disturbance_after_2020"]), True)
    check("r7c3 lost over 40% of the plot", round(r7c3["loss_pct"], 0) > 40, True)
    check("r7c3 must NOT be low", r7c3["risk_tier"], "high")

    # Whisp agreeing with us stays high.
    check("verdict high stays high", by_id.loc["SONDIEN-r6c4"]["risk_tier"], "high")

    # Over-flagging guard: real loss hectares, but Ind_04 says it was not
    # post-cutoff forest disturbance, so it must not raise the tier.
    r7c5 = by_id.loc["TADUNG-r7c5"]
    check("r7c5 has loss on the books", r7c5["loss_ha"] > 0, True)
    check("r7c5 disturbance not confirmed",
          bool(r7c5["whisp_disturbance_after_2020"]), False)
    check("r7c5 stays low", r7c5["risk_tier"], "low")
    check("r7c5 score stays zero", r7c5["risk_score"], 0.0)

    # And the counterfactual: blind deference loses the dangerous plot.
    blind = whisp.to_risk_frame(raw).copy()
    blind_tier = scoring.whisp_tier(
        blind.set_index("plot_id").loc["TADUNG-r7c3", "whisp_risk"]
    )
    check("blind deference would have said low", blind_tier, "low")


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
    print("\n--- batching ---")
    test_chunking()
    print("\n--- analysis options and identity ---")
    test_analysis_options()
    test_external_id_preferred()
    test_external_id_blank_falls_back()
    print("\n--- verdict vocabulary ---")
    test_tier_vocabulary()
    print("\n--- severity combination ---")
    test_severity_helpers()
    print("\n--- real Whisp response fixture ---")
    mapped = test_real_fixture()
    test_heuristic_would_over_flag(mapped)
    print("\n--- forest frontier fixture (both failure modes) ---")
    test_frontier_fixture()

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
