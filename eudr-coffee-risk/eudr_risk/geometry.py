"""Plot geometry: area and the EUDR point-vs-polygon geolocation rule."""

import ee

from .config import EUDR_AREA_THRESHOLD_HA


def add_area_and_rule(fc):
    """Add ``area_ha`` and ``eudr_geom`` to every feature.

    EUDR requires a polygon for plots of more than 4 ha. Plots of 4 ha or
    less may be reported as a single point, so the test is ``<= 4``.
    """

    def _f(f):
        area_ha = f.geometry().area(maxError=1).divide(1e4)
        rule = ee.Algorithms.If(
            area_ha.lte(EUDR_AREA_THRESHOLD_HA), "point", "polygon"
        )
        return f.set({"area_ha": area_ha, "eudr_geom": rule})

    return fc.map(_f)


def to_representative_point(f):
    """Collapse a plot to its centroid, for plots allowed to use a point."""
    return f.setGeometry(f.geometry().centroid(maxError=1))


def polygon_required(fc):
    """Subset of plots that legally need a polygon (area > 4 ha)."""
    return fc.filter(ee.Filter.eq("eudr_geom", "polygon"))
