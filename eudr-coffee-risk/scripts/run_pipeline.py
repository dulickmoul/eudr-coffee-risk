#!/usr/bin/env python
"""End-to-end EUDR risk run: plots GeoJSON -> risk table + DDS bundle.

Examples
--------
Sample plots, default settings::

    python scripts/run_pipeline.py --project my-gee-project

Real plots, a legality layer, and a named harvest year::

    python scripts/run_pipeline.py \
        --plots data/lamdong_plots.geojson \
        --project my-gee-project \
        --legality projects/my-gee-project/assets/lamdong_protection_forest \
        --harvest-year 2026 \
        --out out/2026

Authentication is separate and only needed once per machine::

    earthengine authenticate
"""

import argparse
import json
import os
import sys

# Allow running from the repo root or from scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_PLOTS = os.path.join("data", "plots_sample.geojson")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--plots",
        default=DEFAULT_PLOTS,
        help="GeoJSON FeatureCollection of plot polygons (needs plot_id).",
    )
    p.add_argument(
        "--project",
        default=os.environ.get("GEE_PROJECT"),
        help="Earth Engine Cloud project id (or set GEE_PROJECT).",
    )
    p.add_argument(
        "--legality",
        default=None,
        help="Optional EE FeatureCollection asset id of restricted-land polygons.",
    )
    p.add_argument("--harvest-year", type=int, default=None)
    p.add_argument("--out", default="out", help="Output directory.")
    p.add_argument(
        "--geography",
        default=None,
        help="RADD geography tile set (default: config.RADD_GEOGRAPHY, 'sea').",
    )
    p.add_argument(
        "--loose-hansen",
        action="store_true",
        help="Count Hansen loss even outside JRC 2020 forest (wider net).",
    )
    p.add_argument("--operator-name", default=None)
    p.add_argument("--operator-eori", default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.project:
        sys.exit(
            "No Earth Engine project. Pass --project or set GEE_PROJECT.\n"
            "Register a free project at https://earthengine.google.com"
        )

    import ee
    import geemap

    from eudr_risk import config, dds, pipeline, plots, scoring

    try:
        ee.Initialize(project=args.project)
    except Exception as exc:  # noqa: BLE001 - surface the real cause
        sys.exit(
            f"Earth Engine init failed: {exc}\n"
            "Run 'earthengine authenticate' once, then retry."
        )

    try:
        plots_geojson, geometry_by_id = plots.load_plots(args.plots)
    except plots.PlotLoadError as exc:
        sys.exit(str(exc))
    print(
        f"Loaded {len(geometry_by_id)} plots from {args.plots} "
        f"{plots.geometry_type_counts(plots_geojson)}"
    )

    fc = geemap.geojson_to_ee(plots_geojson)
    legality_fc = ee.FeatureCollection(args.legality) if args.legality else None

    print("Extracting features from Earth Engine (this can take a minute)...")
    features = pipeline.extract_features(
        fc,
        geography=args.geography or config.RADD_GEOGRAPHY,
        legality_fc=legality_fc,
        strict_jrc=not args.loose_hansen,
    )
    table = scoring.score_dataframe(pipeline.to_dataframe(features))
    summary = scoring.summarise(table)

    operator = None
    if args.operator_name or args.operator_eori:
        operator = {
            "name": args.operator_name or "<OPERATOR LEGAL NAME>",
            "eori": args.operator_eori or "<EORI NUMBER>",
        }

    doc = dds.build_dds(
        table,
        geometry_by_id,
        operator=operator,
        harvest_year=args.harvest_year,
    )
    paths = dds.write_outputs(table, doc, summary, args.out)
    paths["checklist"] = dds.write_field_checklist(table, args.out)

    print("\n--- Summary ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n--- Outputs ---")
    for key, path in paths.items():
        print(f"{key:10s} {path}")

    flagged = summary.get("plots_flagged", 0)
    if flagged:
        print(
            f"\n{flagged} plot(s) show post-2020 deforestation evidence. "
            "Verify in the field before concluding negligible risk."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
