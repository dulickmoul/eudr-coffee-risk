"""Risk scoring: plot features -> score, tier, and a due-diligence conclusion.

Deliberately a transparent weighted sum plus explicit rules, not a black box.
Two reasons. A compliance decision has to be explainable to an auditor and to
the farmer it affects, and there is no labelled training set for
"EUDR non-compliance" to fit a real model against.

The score ranks plots for field verification. It does not decide compliance:
a human closes that out, with the farm visit and the land documents.
"""

import pandas as pd

from .config import (
    COUNTRY_EUDR_RISK,
    DEFAULT_WEIGHTS,
    HIGH_LOSS_PCT,
    HIGH_RADD_HA,
    SATURATE_LOSS_PCT,
    SATURATE_RADD_HA,
    WATCH_FOREST_FRAC,
    WHISP_TIER_FALLBACK,
    WHISP_TIER_MAP,
)

NUMERIC_COLS = ["loss_pct", "loss_ha", "radd_alert_ha", "forest_frac_1km", "area_ha"]
BOOL_COLS = ["in_protected_area", "in_restricted_forest"]


def _as_bool(series):
    """ee_to_df can hand back True/False, 1/0 or 'true'/'false'."""
    return series.map(
        lambda v: str(v).strip().lower() in {"true", "1", "1.0", "yes"}
    )


def normalise(df):
    """Fill in missing columns and coerce types so scoring never crashes."""
    d = df.copy()
    for col in NUMERIC_COLS:
        if col not in d.columns:
            d[col] = 0.0
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0.0)
    for col in BOOL_COLS:
        if col not in d.columns:
            d[col] = False
        d[col] = _as_bool(d[col])
    return d


def whisp_tier(verdict):
    """Map a Whisp verdict string onto our tiers, failing safe."""
    key = str(verdict).strip().lower().replace(" ", "_").replace("-", "_")
    return WHISP_TIER_MAP.get(key, WHISP_TIER_FALLBACK)


def _has_verdict(value):
    return (
        value is not None
        and str(value).strip() != ""
        and str(value).strip().lower() not in {"nan", "none", "null"}
    )


def assign_tier(row):
    """Rule-based tier. Whisp's own verdict wins whenever it is present.

    Why defer: our heuristic reads raw post-cutoff loss hectares, but EUDR
    only cares about loss on land that was *forest at the cutoff*. Whisp gates
    on exactly that, so trusting our own arithmetic over its verdict produces
    false positives. A real example from the Di Linh sample: a 5.05 ha coffee
    plot with 0.076 ha of GFC loss after 2020 scores 1.5% here, which our
    thresholds would call "high", while Whisp correctly returns "low" because
    that ground was already tree crop, not forest, in 2020.

    In the Earth Engine backend the equivalent gating is ``strict_jrc=True``
    in :func:`eudr_risk.pipeline.extract_features`.
    """
    verdict = row.get("whisp_risk")
    if _has_verdict(verdict):
        tier = whisp_tier(verdict)
        # Legality is ours to judge, not Whisp's: it does not see land tenure.
        if bool(row.get("in_protected_area")) or bool(row.get("in_restricted_forest")):
            return "high"
        return tier

    has_loss = row["loss_pct"] > 0
    has_alert = row["radd_alert_ha"] > 0
    restricted = bool(row["in_protected_area"]) or bool(row["in_restricted_forest"])

    if restricted:
        return "high"
    if row["loss_pct"] > HIGH_LOSS_PCT or row["radd_alert_ha"] > HIGH_RADD_HA:
        return "high"
    if has_loss or has_alert:
        return "standard"
    if row["forest_frac_1km"] > WATCH_FOREST_FRAC:
        return "standard"
    return "low"


def conclusion(row):
    """Draft due-diligence conclusion, in EUDR language.

    "Negligible risk" is the operator's call. This only says whether the
    remote-sensing evidence is clean enough to support that conclusion.
    """
    if row["risk_tier"] == "low":
        return "no remote-sensing evidence of post-2020 deforestation"
    return "requires investigation before a negligible-risk conclusion"


def score_dataframe(df, weights=None):
    """Add ``risk_score`` (0-100), ``risk_tier``, flags and a conclusion."""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    d = normalise(df)

    loss_comp = (d["loss_pct"] / SATURATE_LOSS_PCT).clip(0, 1)
    radd_comp = (d["radd_alert_ha"] / SATURATE_RADD_HA).clip(0, 1)
    edge_comp = d["forest_frac_1km"].clip(0, 1)
    legal_comp = (d["in_protected_area"] | d["in_restricted_forest"]).astype(float)

    d["risk_score"] = (
        100.0
        * (
            w["loss"] * loss_comp
            + w["radd"] * radd_comp
            + w["legal"] * legal_comp
            + w["edge"] * edge_comp
        )
    ).round(1)

    # Whisp's yes/no indicator is forest-gated, so prefer it over raw hectares.
    if "whisp_disturbance_after_2020" in d.columns:
        d["deforestation_flag"] = _as_bool(d["whisp_disturbance_after_2020"])
    else:
        d["deforestation_flag"] = (d["loss_pct"] > 0) | (d["radd_alert_ha"] > 0)
    d["risk_tier"] = d.apply(assign_tier, axis=1)
    d["dds_conclusion"] = d.apply(conclusion, axis=1)
    d["country_eudr_risk"] = COUNTRY_EUDR_RISK

    for col in ("loss_pct", "loss_ha", "radd_alert_ha", "forest_frac_1km", "area_ha"):
        d[col] = d[col].round(4)

    return d.sort_values("risk_score", ascending=False).reset_index(drop=True)


def summarise(df):
    """Portfolio-level counts, the number a programme manager reports."""
    total = len(df)
    tiers = df["risk_tier"].value_counts().to_dict() if total else {}
    flagged = int(df["deforestation_flag"].sum()) if total else 0
    return {
        "plots": total,
        "area_ha": round(float(df["area_ha"].sum()), 3) if total else 0.0,
        "tier_counts": tiers,
        "plots_flagged": flagged,
        "pct_clean": round(100.0 * (total - flagged) / total, 1) if total else 0.0,
        "polygon_required": int((df["eudr_geom"] == "polygon").sum())
        if "eudr_geom" in df.columns
        else None,
    }
