#!/usr/bin/env python
"""EUDR risk run via the Whisp hosted API. No Earth Engine project needed.

Same output bundle as ``run_pipeline.py``, different data source: FAO's Whisp
service does the geospatial analysis server-side and returns per-plot
indicators plus its own EUDR-oriented risk verdict.

Get an API key from https://whisp.openforis.org, then::

    set WHISP_API_KEY=...            # Windows
    export WHISP_API_KEY=...         # macOS / Linux

First run on new data, look at the real column names before trusting numbers::

    python scripts/run_whisp.py --list-columns

Then a full run::

    python scripts/run_whisp.py --plots data/plots_sample.geojson --out out/whisp

Limits (Whisp 3.0.0a17): 250 geometries return inline, up to 5,000 per job,
request body up to 10 MB.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_PLOTS = os.path.join("data", "plots_sample.geojson")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--plots", default=DEFAULT_PLOTS,
                   help="GeoJSON FeatureCollection (features need plot_id).")
    p.add_argument("--api-key", default=None,
                   help="Whisp API key (or set WHISP_API_KEY).")
    p.add_argument("--out", default=os.path.join("out", "whisp"))
    p.add_argument("--harvest-year", type=int, default=None)
    p.add_argument("--list-columns", action="store_true",
                   help="Print the raw Whisp column names and exit.")
    p.add_argument("--save-raw", action="store_true",
                   help="Also write the unmodified Whisp table as whisp_raw.csv.")
    p.add_argument("--check", action="store_true",
                   help="Print service config/health and exit. No key needed.")
    p.add_argument("--operator-name", default=None)
    p.add_argument("--operator-eori", default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    from eudr_risk import config, dds, plots, scoring, whisp

    if args.check:
        print("config:", json.dumps(whisp.get_config(), indent=2))
        try:
            print("health:", json.dumps(whisp.health(), indent=2))
        except Exception as exc:  # noqa: BLE001
            print("health check failed:", exc)
        return 0

    try:
        plots_geojson, geometry_by_id = plots.load_plots(args.plots)
    except plots.PlotLoadError as exc:
        sys.exit(str(exc))
    print(
        f"Loaded {len(geometry_by_id)} plots from {args.plots} "
        f"{plots.geometry_type_counts(plots_geojson)}"
    )

    try:
        raw = whisp.analyse(plots_geojson, api_key=args.api_key)
    except whisp.WhispError as exc:
        sys.exit(f"Whisp request failed: {exc}")

    if args.list_columns:
        print(f"\nWhisp returned {len(raw)} rows and these columns:")
        for name in whisp.describe_columns(raw):
            print("  ", name)
        print(
            "\nPut the post-2020 loss columns into config.WHISP_LOSS_COLUMNS, "
            "then re-run without --list-columns."
        )
        return 0

    os.makedirs(args.out, exist_ok=True)
    if args.save_raw:
        raw_path = os.path.join(args.out, "whisp_raw.csv")
        raw.to_csv(raw_path, index=False, encoding="utf-8")
        print("wrote", raw_path)

    table = scoring.score_dataframe(whisp.to_risk_frame(raw))
    summary = scoring.summarise(table)

    operator = None
    if args.operator_name or args.operator_eori:
        operator = {
            "name": args.operator_name or "<OPERATOR LEGAL NAME>",
            "eori": args.operator_eori or "<EORI NUMBER>",
        }

    doc = dds.build_dds(table, geometry_by_id, operator=operator,
                        harvest_year=args.harvest_year)
    doc["dds_metadata"]["data_sources"] = {
        "backend": "Open Foris Whisp hosted API (convergence of evidence)",
        "whisp_version_expected": config.WHISP_VERSION_SEEN,
        "whisp_risk_column": config.WHISP_RISK_COLUMN,
    }

    paths = dds.write_outputs(table, doc, summary, args.out)
    paths["checklist"] = dds.write_field_checklist(table, args.out)

    print("\n--- Summary ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n--- Outputs ---")
    for key, path in paths.items():
        print(f"{key:10s} {path}")

    if "whisp_risk" in table.columns and table["whisp_risk"].notna().any():
        print("\nWhisp's own verdict per plot (authoritative for EUDR):")
        print(table[["plot_id", "whisp_risk", "risk_tier"]].to_string(index=False))
    else:
        print(
            f"\nNOTE: no '{whisp.WHISP_RISK_COLUMN}' column in the response. "
            "Run --list-columns and set config.WHISP_RISK_COLUMN accordingly."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
