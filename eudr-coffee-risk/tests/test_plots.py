# -*- coding: utf-8 -*-
"""Tests for plot loading and id resolution.

Pure stdlib, no network. If Whisp's own example file is present locally it is
also exercised, because that file is the reason this module exists: it carries
only ``user_id`` and MultiPolygon geometries.

    python tests/test_plots.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from eudr_risk import plots

FAILS = []

# Optional real-world fixture: Whisp's downloadable example polygons.
WHISP_EXAMPLE = os.path.join(
    os.path.expanduser("~"), "Downloads", "whisp_example_polys.geojson"
)


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def write_tmp(obj):
    fd, path = tempfile.mkstemp(suffix=".geojson")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


def fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def poly(props=None):
    return {
        "type": "Feature",
        "properties": props if props is not None else {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[108.07, 11.58], [108.071, 11.58],
                             [108.071, 11.581], [108.07, 11.581],
                             [108.07, 11.58]]],
        },
    }


def expect_error(label, fn):
    try:
        fn()
        check(label, False, True)
    except plots.PlotLoadError:
        check(label, True, True)


def test_load_errors():
    expect_error("missing file rejected",
                 lambda: plots.load_geojson("does-not-exist.geojson"))

    fd, bad = tempfile.mkstemp(suffix=".geojson")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    expect_error("invalid JSON rejected", lambda: plots.load_geojson(bad))

    wrong = write_tmp({"type": "Feature", "properties": {}})
    expect_error("non-FeatureCollection rejected",
                 lambda: plots.load_geojson(wrong))

    empty = write_tmp(fc())
    expect_error("empty collection rejected", lambda: plots.load_geojson(empty))


def test_plot_id_present():
    data = fc(poly({"plot_id": "DL-001"}), poly({"plot_id": "DL-002"}))
    _, geoms = plots.resolve_plot_ids(data, verbose=False)
    check("plot_id kept", sorted(geoms), ["DL-001", "DL-002"])


def test_user_id_fallback():
    """Whisp's example file case: only user_id."""
    data = fc(poly({"user_id": 1}), poly({"user_id": 2}))
    out, geoms = plots.resolve_plot_ids(data, verbose=False)
    check("user_id becomes plot_id", sorted(geoms), ["1", "2"])
    check("written back into properties",
          out["features"][0]["properties"]["plot_id"], "1")


def test_synthesised_ids():
    data = fc(poly({"area": 1}), poly({"area": 2}))
    _, geoms = plots.resolve_plot_ids(data, verbose=False)
    check("ids synthesised", sorted(geoms), ["plot-0001", "plot-0002"])


def test_partial_id_coverage():
    """One feature missing the id means the field is unusable: fall through."""
    data = fc(poly({"plot_id": "A"}), poly({}))
    _, geoms = plots.resolve_plot_ids(data, verbose=False)
    check("falls back to synthesised", sorted(geoms), ["plot-0001", "plot-0002"])


def test_duplicates_not_collapsed():
    data = fc(poly({"plot_id": "same"}), poly({"plot_id": "same"}),
              poly({"plot_id": "same"}))
    _, geoms = plots.resolve_plot_ids(data, verbose=False)
    check("3 plots stay 3 rows", len(geoms), 3)
    check("duplicates suffixed", sorted(geoms), ["same", "same-1", "same-2"])


def test_bad_geometry():
    feature = poly({"plot_id": "X"})
    feature["geometry"] = {"type": "Circle", "coordinates": []}
    expect_error("unsupported geometry rejected",
                 lambda: plots.resolve_plot_ids(fc(feature), verbose=False))


def test_geometry_counts():
    data = fc(poly({"plot_id": "A"}), poly({"plot_id": "B"}))
    check("geometry counts", plots.geometry_type_counts(data), {"Polygon": 2})


def test_real_whisp_example():
    if not os.path.exists(WHISP_EXAMPLE):
        print(f"SKIP  real Whisp example not found at {WHISP_EXAMPLE}")
        return
    data, geoms = plots.load_plots(WHISP_EXAMPLE, verbose=False)
    print(f"      loaded {len(geoms)} plots, "
          f"geometries={plots.geometry_type_counts(data)}")
    check("50 features loaded", len(geoms), 50)
    check("all ids unique", len(set(geoms)), len(geoms))
    check("ids came from user_id", "1" in geoms, True)
    check("multipolygon accepted",
          plots.geometry_type_counts(data).get("MultiPolygon"), 50)
    check("plot_id written into properties",
          "plot_id" in data["features"][0]["properties"], True)


def main():
    print("--- load errors ---")
    test_load_errors()
    print("\n--- id resolution ---")
    test_plot_id_present()
    test_user_id_fallback()
    test_synthesised_ids()
    test_partial_id_coverage()
    test_duplicates_not_collapsed()
    print("\n--- geometry ---")
    test_bad_geometry()
    test_geometry_counts()
    print("\n--- real Whisp example file ---")
    test_real_whisp_example()

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
