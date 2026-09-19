"""Loading and normalising plot GeoJSON. Pure stdlib, no Earth Engine.

Real plot files come from QGIS exports, GPS units, national databases and
other people's examples, so the identifier column is never the one you
expect. Whisp's own example file, for instance, carries only ``user_id``.
Rather than refusing those files, resolve an id and say what was done.
"""

import json
import os

# Identifier properties we accept, best first.
ID_FIELDS = ("plot_id", "user_id", "plotId", "id", "ID", "fid", "OBJECTID", "geoid")

GEOMETRY_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
}


class PlotLoadError(ValueError):
    pass


def load_geojson(path):
    if not os.path.exists(path):
        raise PlotLoadError(f"No such file: {path}")
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise PlotLoadError(f"{path} is not valid JSON: {exc}") from exc
    if data.get("type") != "FeatureCollection":
        raise PlotLoadError(
            f"Expected a GeoJSON FeatureCollection, got {data.get('type')!r}."
        )
    if not data.get("features"):
        raise PlotLoadError(f"{path} has no features.")
    return data


def resolve_plot_ids(geojson, id_fields=ID_FIELDS, verbose=True):
    """Ensure every feature has a unique ``plot_id``. Mutates and returns it.

    Returns ``(geojson, geometry_by_id)``. Ids are stringified. Duplicates get
    a numeric suffix rather than silently overwriting each other, because two
    plots collapsing into one row is the kind of bug that quietly corrupts a
    compliance report.
    """
    features = geojson.get("features", [])

    source_field = None
    for field in id_fields:
        if all((f.get("properties") or {}).get(field) is not None for f in features):
            source_field = field
            break

    if source_field is None and verbose:
        print(
            "NOTE: no usable id property found "
            f"(looked for {', '.join(id_fields)}); assigning plot-0001 style ids."
        )
    elif source_field != "plot_id" and verbose:
        print(f"NOTE: using '{source_field}' as plot_id.")

    geometry_by_id = {}
    seen = {}
    for index, feature in enumerate(features):
        props = feature.setdefault("properties", {}) or {}
        feature["properties"] = props

        if source_field is None:
            raw = f"plot-{index + 1:04d}"
        else:
            raw = str(props[source_field])

        if raw in seen:
            seen[raw] += 1
            plot_id = f"{raw}-{seen[raw]}"
            if verbose:
                print(f"WARNING: duplicate id {raw!r}, renamed to {plot_id!r}.")
        else:
            seen[raw] = 0
            plot_id = raw

        props["plot_id"] = plot_id

        geometry = feature.get("geometry")
        gtype = (geometry or {}).get("type")
        if gtype not in GEOMETRY_TYPES:
            raise PlotLoadError(
                f"Feature {plot_id} has unsupported geometry type {gtype!r}."
            )
        geometry_by_id[plot_id] = geometry

    return geojson, geometry_by_id


def load_plots(path, verbose=True):
    """Convenience wrapper: read a file and normalise its ids."""
    return resolve_plot_ids(load_geojson(path), verbose=verbose)


def geometry_type_counts(geojson):
    counts = {}
    for feature in geojson.get("features", []):
        gtype = (feature.get("geometry") or {}).get("type", "None")
        counts[gtype] = counts.get(gtype, 0) + 1
    return counts
