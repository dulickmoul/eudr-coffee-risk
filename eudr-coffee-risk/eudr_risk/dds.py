"""Export a due-diligence bundle: CSV, DDS-oriented GeoJSON, and a summary.

Scope check, because this matters legally: the official Due Diligence
Statement is filed by the **operator** placing the product on the EU market,
through the EU Information System (TRACES). This module does not file
anything. It produces the geolocation and risk-assessment evidence that sits
behind such a statement, in a structure close enough to hand to whoever does.
"""

import csv
import datetime
import json
import os

from .config import (
    COMMODITY,
    COUNTRY,
    COUNTRY_BENCHMARK_SOURCE,
    COUNTRY_EUDR_RISK,
    COUNTRY_NAME,
    CUTOFF_DATE,
    HS_HEADING,
    JRC_GFC2020_ASSET,
    RADD_ASSET,
    RECORD_RETENTION_YEARS,
    HANSEN_ASSET,
)

SCHEMA = "eudr-dds-oriented/0.1"

DDS_PROPERTY_ORDER = [
    "plot_id",
    "production_country",
    "commodity",
    "hs_heading",
    "area_ha",
    "eudr_geometry_type",
    "harvest_year",
    "post2020_loss_pct",
    "post2020_loss_ha",
    "radd_alert_ha",
    "in_protected_area",
    "in_restricted_forest",
    "forest_frac_1km",
    "risk_score",
    "risk_tier",
    "deforestation_flag",
    "whisp_risk",
    "verdict_context",
    "dds_conclusion",
]


def _placeholder_operator():
    return {
        "name": "<OPERATOR LEGAL NAME>",
        "eori": "<EORI NUMBER>",
        "address": "<REGISTERED ADDRESS>",
        "contact": "<EMAIL>",
    }


def build_dds(df, geometry_by_id, operator=None, harvest_year=None):
    """Assemble the DDS-oriented GeoJSON document.

    Parameters
    ----------
    df : pandas.DataFrame
        Output of :func:`eudr_risk.scoring.score_dataframe`.
    geometry_by_id : dict
        ``{plot_id: geojson_geometry}`` from the source plot file.
    """
    features = []
    for _, row in df.iterrows():
        plot_id = row.get("plot_id")
        props = {
            "plot_id": plot_id,
            "production_country": COUNTRY,
            "commodity": COMMODITY,
            "hs_heading": HS_HEADING,
            "area_ha": float(row.get("area_ha", 0) or 0),
            "eudr_geometry_type": row.get("eudr_geom"),
            "harvest_year": harvest_year,
            "post2020_loss_pct": float(row.get("loss_pct", 0) or 0),
            "post2020_loss_ha": float(row.get("loss_ha", 0) or 0),
            "radd_alert_ha": float(row.get("radd_alert_ha", 0) or 0),
            "in_protected_area": bool(row.get("in_protected_area", False)),
            "in_restricted_forest": bool(row.get("in_restricted_forest", False)),
            "forest_frac_1km": float(row.get("forest_frac_1km", 0) or 0),
            "risk_score": float(row.get("risk_score", 0) or 0),
            "risk_tier": row.get("risk_tier"),
            "deforestation_flag": bool(row.get("deforestation_flag", False)),
            "dds_conclusion": row.get("dds_conclusion"),
        }
        # Present only when the Whisp backend was used. Its own EUDR-oriented
        # verdict is the authoritative one; keep it verbatim next to ours.
        whisp_risk = row.get("whisp_risk")
        if whisp_risk is not None and str(whisp_risk) != "nan":
            props["whisp_risk"] = str(whisp_risk)
        # Why a verdict and the raw numbers appear to disagree. Kept in the
        # record so a reviewer is never left guessing.
        context = row.get("verdict_context")
        if context:
            props["verdict_context"] = str(context)
        ordered = {k: props[k] for k in DDS_PROPERTY_ORDER if k in props}
        features.append(
            {
                "type": "Feature",
                "properties": ordered,
                "geometry": geometry_by_id.get(plot_id),
            }
        )

    return {
        "type": "FeatureCollection",
        "dds_metadata": {
            "schema": SCHEMA,
            "disclaimer": (
                "Decision-support evidence, not a filed Due Diligence "
                "Statement. The operator submits the official DDS via the EU "
                "Information System (TRACES)."
            ),
            "generated_utc": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "operator": operator or _placeholder_operator(),
            "commodity": COMMODITY,
            "hs_heading": HS_HEADING,
            "production_country": COUNTRY,
            "production_country_name": COUNTRY_NAME,
            "country_eudr_benchmark": COUNTRY_EUDR_RISK,
            "country_benchmark_source": COUNTRY_BENCHMARK_SOURCE,
            "cutoff_date": CUTOFF_DATE,
            "record_retention_years": RECORD_RETENTION_YEARS,
            "data_sources": {
                "forest_baseline_2020": JRC_GFC2020_ASSET,
                "tree_cover_loss": HANSEN_ASSET,
                "nrt_alerts": RADD_ASSET,
            },
        },
        "features": features,
    }


def write_outputs(df, dds_doc, summary, out_dir):
    """Write risk_table.csv, dds.geojson and summary.json. Returns paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "csv": os.path.join(out_dir, "risk_table.csv"),
        "dds": os.path.join(out_dir, "dds.geojson"),
        "summary": os.path.join(out_dir, "summary.json"),
    }

    df.to_csv(paths["csv"], index=False, encoding="utf-8")

    with open(paths["dds"], "w", encoding="utf-8") as fh:
        json.dump(dds_doc, fh, ensure_ascii=False, indent=2)

    with open(paths["summary"], "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    return paths


def write_field_checklist(df, out_dir, tiers=("high", "standard")):
    """A printable list of plots to visit, highest risk first.

    The point of the whole pipeline: a field team gets a short, ordered list
    instead of 16,000 farms.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "field_checklist.csv")
    subset = df[df["risk_tier"].isin(tiers)]
    cols = [
        c
        for c in [
            "plot_id",
            "risk_tier",
            "risk_score",
            "area_ha",
            "eudr_geom",
            "loss_pct",
            "radd_alert_ha",
            "in_protected_area",
            "verdict_context",
            "dds_conclusion",
        ]
        if c in subset.columns
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols + ["visited_date", "field_finding", "resolved"])
        for _, row in subset[cols].iterrows():
            writer.writerow(list(row.values) + ["", "", ""])
    return path
