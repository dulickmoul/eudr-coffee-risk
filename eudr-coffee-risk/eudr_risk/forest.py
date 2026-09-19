"""Forest layers: the JRC 2020 EUDR baseline and Hansen post-cutoff loss.

Two different jobs:

* **JRC GFC2020** answers "was this forest at the cutoff date?". It is the
  layer aligned with the EUDR cutoff and is what a DDS-grade assessment
  should lean on.
* **Hansen GFC** answers "did tree cover disappear, and in which year?".
  It supplies the change signal that flags a plot for review.
"""

import ee

from .config import (
    CUTOFF_YEAR,
    FOREST_CANOPY_THRESHOLD,
    HANSEN_ASSET,
    JRC_FOREST_BAND,
    JRC_FOREST_VALUE,
    JRC_GFC2020_ASSET,
)


def jrc_forest_2020():
    """Official EUDR-aligned forest mask for 2020 (1 = forest, 0 = not)."""
    return (
        ee.Image(JRC_GFC2020_ASSET)
        .select(JRC_FOREST_BAND)
        .eq(JRC_FOREST_VALUE)
        .unmask(0)
        .rename("forest2020")
    )


def hansen():
    return ee.Image(HANSEN_ASSET)


def hansen_loss_after_cutoff(
    canopy_threshold=FOREST_CANOPY_THRESHOLD, cutoff_year=CUTOFF_YEAR
):
    """Tree-cover loss after the cutoff, on land that was forest in 2000.

    ``lossyear`` holds years since 2000, so ``> 20`` keeps 2021 onwards.
    Returns a 0/1 image (unmasked, so zonal sums are 0 rather than null).
    """
    gfc = hansen()
    forest2000 = gfc.select("treecover2000").gte(canopy_threshold)
    lossyear = gfc.select("lossyear")
    loss = gfc.select("loss")
    return (
        loss.And(lossyear.gt(cutoff_year - 2000))
        .And(forest2000)
        .unmask(0)
        .rename("loss_after_cutoff")
    )


def loss_after_cutoff_on_jrc_forest(**kwargs):
    """Stricter variant: post-cutoff loss that also fell inside JRC 2020 forest.

    Closer to the EUDR question than Hansen alone, because it requires the
    land to have been forest at the cutoff date, not just in 2000.
    """
    return (
        hansen_loss_after_cutoff(**kwargs)
        .And(jrc_forest_2020())
        .unmask(0)
        .rename("loss_after_cutoff_jrc")
    )
