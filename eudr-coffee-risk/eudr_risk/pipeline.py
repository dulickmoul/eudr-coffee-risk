"""Feature extraction: turn plot polygons into one risk row per plot.

Everything that touches Earth Engine lives here. Scoring is deliberately kept
out (see :mod:`eudr_risk.scoring`) so the weighting can be tuned and reviewed
without re-running any zonal statistics.
"""

import ee

from . import alerts, forest, geometry, legality
from .config import (
    CONTEXT_BUFFER_M,
    RADD_GEOGRAPHY,
    SCALE_HANSEN,
    SCALE_JRC,
    SCALE_RADD,
)

MAX_PIXELS = int(1e9)


def extract_features(
    fc,
    geography=RADD_GEOGRAPHY,
    legality_fc=None,
    strict_jrc=True,
):
    """Attach area, loss, alert, context and legality fields to each plot.

    Parameters
    ----------
    fc : ee.FeatureCollection
        Plot polygons. Each feature should carry a ``plot_id`` property.
    strict_jrc : bool
        If True, post-cutoff loss must also fall inside JRC 2020 forest. This
        is the stricter, more EUDR-faithful test. If False, Hansen loss alone
        is used, which flags more plots (useful as a wider net).

    Returns
    -------
    ee.FeatureCollection with the added properties.
    """
    fc = geometry.add_area_and_rule(fc)

    loss_mask = (
        forest.loss_after_cutoff_on_jrc_forest()
        if strict_jrc
        else forest.hansen_loss_after_cutoff()
    )
    forest2020 = forest.jrc_forest_2020()
    radd_mask = alerts.radd_alerts_after_cutoff(geography=geography)

    pixel_area = ee.Image.pixelArea()
    loss_area = loss_mask.multiply(pixel_area).rename("loss_area")
    radd_area = radd_mask.multiply(pixel_area).rename("radd_area")

    def _stats(f):
        geom = f.geometry()
        plot_m2 = geom.area(maxError=1)

        loss_m2 = ee.Number(
            loss_area.reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=geom,
                scale=SCALE_HANSEN,
                maxPixels=MAX_PIXELS,
            ).get("loss_area")
        )
        radd_m2 = ee.Number(
            radd_area.reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=geom,
                scale=SCALE_RADD,
                maxPixels=MAX_PIXELS,
            ).get("radd_area")
        )
        # Forest remaining in a ring around the plot: clearing pressure, and a
        # hint that the plot sits on a frontier rather than in settled farmland.
        forest_frac = ee.Number(
            forest2020.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geom.buffer(CONTEXT_BUFFER_M),
                scale=SCALE_JRC,
                maxPixels=MAX_PIXELS,
            ).get("forest2020")
        )

        return f.set(
            {
                "loss_ha": loss_m2.divide(1e4),
                "loss_pct": loss_m2.divide(plot_m2).multiply(100),
                "radd_alert_ha": radd_m2.divide(1e4),
                "forest_frac_1km": forest_frac,
            }
        )

    fc = fc.map(_stats)
    fc = legality.add_protected_flag(fc)
    if legality_fc is not None:
        fc = legality.add_user_legality_flag(fc, legality_fc)
    return fc


FEATURE_COLUMNS = [
    "plot_id",
    "area_ha",
    "eudr_geom",
    "loss_ha",
    "loss_pct",
    "radd_alert_ha",
    "forest_frac_1km",
    "in_protected_area",
    "in_restricted_forest",
]


def to_dataframe(fc, columns=None):
    """Pull the feature table to pandas. Requires geemap."""
    import geemap

    df = geemap.ee_to_df(fc)
    cols = columns or FEATURE_COLUMNS
    keep = [c for c in cols if c in df.columns]
    rest = [c for c in df.columns if c not in keep]
    return df[keep + rest]
