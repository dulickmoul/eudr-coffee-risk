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


TIER_SEVERITY = {"low": 0, "standard": 1, "high": 2}


def most_severe(*tiers):
    """The worst of several tiers. Unknown labels count as standard."""
    present = [t for t in tiers if t]
    if not present:
        return "low"
    return max(present, key=lambda t: TIER_SEVERITY.get(t, 1))


def evidence_tier(row):
    """Tier from our own measurements alone.

    Raw post-cutoff loss only counts when the land was forest at the cutoff.
    When Whisp's forest-gated indicator says there was no post-cutoff
    disturbance, the hectares are real but not EUDR-relevant, so they must
    not raise the tier. The Earth Engine backend gets the same gating from
    ``strict_jrc=True`` in :func:`eudr_risk.pipeline.extract_features`.
    """
    gated = (
        "whisp_disturbance_after_2020" in row
        and not bool(row.get("whisp_disturbance_after_2020"))
    )
    if not gated:
        if row["loss_pct"] > HIGH_LOSS_PCT or row["radd_alert_ha"] > HIGH_RADD_HA:
            return "high"
        if row["loss_pct"] > 0 or row["radd_alert_ha"] > 0:
            return "standard"
    if row["forest_frac_1km"] > WATCH_FOREST_FRAC:
        return "standard"
    return "low"


def assign_tier(row):
    """Take the more severe of Whisp's verdict and our own evidence.

    Neither source may be trusted alone, and the two failure modes point in
    opposite directions. Both are real cases from Lam Dong:

    * Trusting our arithmetic alone **over-flags**. Plot DL-002, 5.05 ha of
      coffee with 0.076 ha of post-2020 GFC loss, reads 1.51% and would be
      called high, but every forest-2020 layer is zero: it was already tree
      crop. Whisp says low, correctly.
    * Trusting Whisp's verdict alone **under-flags**, which is worse. Probe
      TADUNG-r7c3 lost 2.16 ha of 4.82 ha after 2020, 44.8%, with RADD radar
      alerts and Whisp's own Ind_04 indicator set to yes, yet ``risk_pcrop``
      came back low. Deferring would have hidden a plot that plainly needs a
      visit.

    So combine them and keep the worst. A verdict can add severity but never
    subtract it from confirmed disturbance, and legality overrides both
    because Whisp cannot see land tenure.
    """
    if bool(row.get("in_protected_area")) or bool(row.get("in_restricted_forest")):
        return "high"

    verdict = row.get("whisp_risk")
    from_verdict = whisp_tier(verdict) if _has_verdict(verdict) else None
    return most_severe(from_verdict, evidence_tier(row))


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

    # Gate the disturbance signals the same way the tier is gated. Otherwise a
    # plot Whisp has cleared still scores high on loss hectares that are not
    # EUDR-relevant, and the report reads "low risk, score 50", which invites
    # exactly the misreading this tool exists to prevent.
    if "whisp_disturbance_after_2020" in d.columns:
        relevant = _as_bool(d["whisp_disturbance_after_2020"]).astype(float)
        loss_comp = loss_comp * relevant
        radd_comp = radd_comp * relevant

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
