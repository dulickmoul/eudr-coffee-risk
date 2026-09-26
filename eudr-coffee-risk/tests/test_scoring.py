# -*- coding: utf-8 -*-
"""Tests for the scoring and export layer.

Deliberately free of Earth Engine: this half is pure pandas/stdlib, so it runs
anywhere with no credentials and no network. Run it before changing weights or
tier thresholds.

    pip install pandas
    python tests/test_scoring.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pandas as pd

from eudr_risk import dds, scoring

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise AssertionError(f"{label}: got={got!r} want={want!r}")


def sample_frame():
    """Six plots, one per behaviour we care about.

    'in_restricted_forest' is deliberately absent: the scorer must default it.
    'in_protected_area' on D is the string "true", which is what
    geemap.ee_to_df can hand back instead of a real bool.
    """
    return pd.DataFrame(
        [
            dict(plot_id="A-clean", area_ha=1.6, eudr_geom="point", loss_ha=0.0,
                 loss_pct=0.0, radd_alert_ha=0.0, forest_frac_1km=0.05,
                 in_protected_area=False),
            dict(plot_id="B-smallloss", area_ha=2.5, eudr_geom="point",
                 loss_ha=0.005, loss_pct=0.2, radd_alert_ha=0.0,
                 forest_frac_1km=0.10, in_protected_area=False),
            dict(plot_id="C-bigloss", area_ha=5.0, eudr_geom="polygon",
                 loss_ha=0.04, loss_pct=0.8, radd_alert_ha=0.0,
                 forest_frac_1km=0.10, in_protected_area=False),
            dict(plot_id="D-protected", area_ha=3.0, eudr_geom="point",
                 loss_ha=0.0, loss_pct=0.0, radd_alert_ha=0.0,
                 forest_frac_1km=0.10, in_protected_area="true"),
            dict(plot_id="E-frontier", area_ha=2.0, eudr_geom="point",
                 loss_ha=0.0, loss_pct=0.0, radd_alert_ha=0.0,
                 forest_frac_1km=0.40, in_protected_area=False),
            dict(plot_id="F-alerts", area_ha=4.0, eudr_geom="point", loss_ha=0.0,
                 loss_pct=0.0, radd_alert_ha=0.07, forest_frac_1km=0.10,
                 in_protected_area=False),
        ]
    )


def check_tiers(scored):
    tiers = dict(zip(scored["plot_id"], scored["risk_tier"]))
    check("A-clean -> low", tiers["A-clean"], "low")
    check("B small loss -> standard", tiers["B-smallloss"], "standard")
    check("C big loss -> high", tiers["C-bigloss"], "high")
    check("D protected -> high", tiers["D-protected"], "high")
    check("E forest nearby -> standard", tiers["E-frontier"], "standard")
    check("F fresh alerts -> high", tiers["F-alerts"], "high")


def check_coercion(scored):
    check("missing column defaults False",
          bool(scored["in_restricted_forest"].any()), False)
    check("string 'true' coerced to True",
          bool(scored.loc[scored.plot_id == "D-protected",
                          "in_protected_area"].iloc[0]), True)


def check_flags(scored):
    flags = dict(zip(scored["plot_id"], scored["deforestation_flag"]))
    check("clean not flagged", bool(flags["A-clean"]), False)
    check("loss flagged", bool(flags["C-bigloss"]), True)
    check("alerts flagged", bool(flags["F-alerts"]), True)
    # Legality risk is real risk, but it is not deforestation evidence.
    check("protected-only is not a deforestation flag",
          bool(flags["D-protected"]), False)


def check_score(scored):
    check("sorted by score desc",
          list(scored["risk_score"]) == sorted(scored["risk_score"], reverse=True),
          True)
    check("score within 0..100",
          bool(scored["risk_score"].between(0, 100).all()), True)
    check("clean plot scores near zero",
          bool(scored.loc[scored.plot_id == "A-clean", "risk_score"].iloc[0] < 5),
          True)
    concl = dict(zip(scored["plot_id"], scored["dds_conclusion"]))
    check("clean conclusion", concl["A-clean"],
          "no remote-sensing evidence of post-2020 deforestation")
    check("flagged conclusion", concl["C-bigloss"],
          "requires investigation before a negligible-risk conclusion")
    check("country benchmark recorded", scored["country_eudr_risk"].iloc[0], "low")


def check_summary(scored):
    summary = scoring.summarise(scored)
    print(json.dumps(summary, indent=2))
    check("plot count", summary["plots"], 6)
    check("flagged count", summary["plots_flagged"], 3)
    check("polygon required (>4 ha)", summary["polygon_required"], 1)
    check("total area", round(summary["area_ha"], 1), 18.1)
    check("pct clean", summary["pct_clean"], 50.0)
    return summary


def test_empty():
    empty = scoring.score_dataframe(pd.DataFrame(columns=["plot_id", "eudr_geom"]))
    check("empty frame survives", len(empty), 0)
    check("empty summary survives", scoring.summarise(empty)["plots"], 0)


def check_export(scored, summary):
    geoms = {
        pid: {
            "type": "Polygon",
            "coordinates": [[[108.07, 11.58], [108.071, 11.58],
                             [108.071, 11.581], [108.07, 11.581],
                             [108.07, 11.58]]],
        }
        for pid in scored["plot_id"]
    }
    doc = dds.build_dds(scored, geoms,
                        operator={"name": "Test Co", "eori": "VN123"},
                        harvest_year=2026)
    check("feature count", len(doc["features"]), 6)
    meta = doc["dds_metadata"]
    check("schema", meta["schema"], "eudr-dds-oriented/0.1")
    check("cutoff date", meta["cutoff_date"], "2020-12-31")
    check("forest baseline provenance",
          meta["data_sources"]["forest_baseline_2020"], "JRC/GFC2020/V3")
    check("operator passthrough", meta["operator"]["eori"], "VN123")
    check("country benchmark", meta["country_eudr_benchmark"], "low")
    props = doc["features"][0]["properties"]
    check("HS heading", props["hs_heading"], "0901")
    check("harvest year", props["harvest_year"], 2026)
    check("geometry attached", doc["features"][0]["geometry"]["type"], "Polygon")
    check("json serialisable", isinstance(json.dumps(doc), str), True)

    out = tempfile.mkdtemp(prefix="eudr_out_")
    paths = dds.write_outputs(scored, doc, summary, out)
    paths["checklist"] = dds.write_field_checklist(scored, out)
    for key, path in paths.items():
        check(f"wrote {key}",
              os.path.exists(path) and os.path.getsize(path) > 0, True)

    with open(paths["dds"], encoding="utf-8") as fh:
        check("dds.geojson reloads", len(json.load(fh)["features"]), 6)

    with open(paths["checklist"], encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    check("checklist is header + 5 rows", len(lines), 6)
    check("clean plot excluded from checklist",
          "A-clean" in "".join(lines), False)


def main():
    print("--- scoring ---")
    scored = scoring.score_dataframe(sample_frame())
    check_tiers(scored)
    check_coercion(scored)
    check_flags(scored)
    check_score(scored)
    print("\n--- summary ---")
    summary = check_summary(scored)
    print("\n--- edge cases ---")
    test_empty()
    print("\n--- export bundle ---")
    check_export(scored, summary)

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
