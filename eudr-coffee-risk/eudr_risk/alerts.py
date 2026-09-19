"""Near-real-time deforestation alerts (RADD, Sentinel-1 radar).

Hansen is annual, so it lags. RADD detects disturbance within days and, being
radar, works through cloud. That matters here: the Central Highlands wet season
hides optical sensors for months at a time.

Band naming in this collection has shifted between releases. Call
:func:`radd_band_names` once against your project and confirm before trusting a
production run; ``config.RADD_DATE_BAND`` is the knob to adjust.
"""

import ee

from .config import CUTOFF_YYDOY, RADD_ASSET, RADD_DATE_BAND, RADD_GEOGRAPHY


def radd_collection(geography=RADD_GEOGRAPHY):
    return (
        ee.ImageCollection(RADD_ASSET)
        .filterMetadata("layer", "contains", "alert")
        .filterMetadata("geography", "equals", geography)
    )


def radd_alert_mosaic(geography=RADD_GEOGRAPHY):
    return radd_collection(geography).mosaic()


def radd_band_names(geography=RADD_GEOGRAPHY):
    """Diagnostic: the real band names, so you can verify the date band.

    Usage: ``print(alerts.radd_band_names().getInfo())``
    """
    return radd_alert_mosaic(geography).bandNames()


def radd_alerts_after_cutoff(
    geography=RADD_GEOGRAPHY,
    cutoff_yydoy=CUTOFF_YYDOY,
    date_band=RADD_DATE_BAND,
):
    """0/1 mask of alerts dated after the EUDR cutoff.

    The date band uses YYDOY, so ``>= 21001`` keeps everything from
    2021-01-01 onwards. Unmasked to 0 so zonal sums return 0, not null.
    """
    date = radd_alert_mosaic(geography).select(date_band)
    return date.gte(cutoff_yydoy).unmask(0).rename("radd_alert")


def radd_alerts_in_window(
    start_yydoy,
    end_yydoy,
    geography=RADD_GEOGRAPHY,
    date_band=RADD_DATE_BAND,
):
    """Alerts inside an explicit YYDOY window, e.g. the last harvest season."""
    date = radd_alert_mosaic(geography).select(date_band)
    return (
        date.gte(start_yydoy)
        .And(date.lte(end_yydoy))
        .unmask(0)
        .rename("radd_alert_window")
    )
