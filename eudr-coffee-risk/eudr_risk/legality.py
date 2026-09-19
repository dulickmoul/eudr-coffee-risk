"""Land-legality overlays.

EUDR asks for two things, not one: the commodity must be deforestation-free
**and** produced legally under the country's own law. Satellite loss data only
answers the first half.

WDPA (protected areas) is the openly available global proxy and is wired up
here. The authoritative Vietnamese layer is the official forest zoning, the
"3 loai rung" classification (rung dac dung / phong ho / san xuat) plus
provincial land-use plans. That is not a public Earth Engine asset, so
:func:`add_user_legality_flag` takes whatever FeatureCollection you can obtain
(shapefile uploaded as an EE asset, or the national EUDR cultivation-zone
database) and flags plots that intersect it.
"""

import ee

from .config import WDPA_ASSET


def wdpa():
    return ee.FeatureCollection(WDPA_ASSET)


def add_protected_flag(fc, field_name="in_protected_area"):
    """Flag plots intersecting a WDPA protected area."""
    protected = wdpa()

    def _f(f):
        hit = protected.filterBounds(f.geometry()).size().gt(0)
        return f.set(field_name, hit)

    return fc.map(_f)


def add_user_legality_flag(fc, legality_fc, field_name="in_restricted_forest"):
    """Flag plots intersecting a user-supplied restricted-land layer.

    Parameters
    ----------
    legality_fc : ee.FeatureCollection
        E.g. protection or special-use forest polygons for Lam Dong.
    """

    def _f(f):
        hit = legality_fc.filterBounds(f.geometry()).size().gt(0)
        return f.set(field_name, hit)

    return fc.map(_f)
