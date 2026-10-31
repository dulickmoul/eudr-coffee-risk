# -*- coding: utf-8 -*-
"""Tests for the province probe grid.

numpy only, no network, no Earth Engine: the Earth Engine path runs against a
stand-in module, and the GeoTIFF path is skipped unless rasterio is installed.

    python tests/test_probe_grid.py
"""

import io
import json
import os
import sys
import tempfile
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from eudr_risk import probe_grid as pg

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise AssertionError(f"{label}: got={got!r} want={want!r}")


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


# Cell size 0.1 keeps the arithmetic readable; nothing depends on the value.
CELL = 0.1


def test_grid_snapping():
    g = pg.Grid((108.03, 11.52, 108.27, 11.71), CELL)
    check("west snapped down", round(g.west, 6), 108.0)
    check("south snapped down", round(g.south, 6), 11.5)
    check("east snapped up", round(g.east, 6), 108.3)
    check("north snapped up", round(g.north, 6), 11.8)
    check("shape", g.shape, (3, 3))
    # Same cell from a different bbox keeps its id: ids join across reruns.
    h = pg.Grid((107.95, 11.45, 108.25, 11.75), CELL)
    check("id independent of bbox",
          g.cell_id(0, 0, "LD"), h.cell_id(1, 1, "LD"))
    ring = g.cell_ring(0, 0)
    check("ring closed", ring[0], ring[-1])
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
    check("ring counter-clockwise (RFC 7946)", area2 > 0, True)


def test_grid_errors():
    for label, bbox, cell in [("empty bbox", (1, 1, 1, 2), CELL),
                              ("zero cell", (0, 0, 1, 1), 0)]:
        try:
            pg.Grid(bbox, cell)
            check(label, "no error", "GridError")
        except pg.GridError:
            check(label, "GridError", "GridError")


def test_inside_square():
    polys = [[square(0, 0, 0.5, 0.3)]]
    g = pg.Grid(pg.boundary_bbox(polys), CELL)
    m = pg.inside_mask(g, polys)
    check("square shape", m.shape, (3, 5))
    check("square fully inside", int(m.sum()), 15)


def test_inside_hole():
    polys = [[square(0, 0, 0.5, 0.5), square(0.2, 0.2, 0.3, 0.3)]]
    g = pg.Grid(pg.boundary_bbox(polys), CELL)
    m = pg.inside_mask(g, polys)
    check("hole cell excluded", bool(m[2, 2]), False)
    check("hole leaves 24", int(m.sum()), 24)


def test_inside_shared_edge():
    # Two provinces sharing an edge must union, not cancel along the seam.
    polys = [[square(0, 0, 0.3, 0.2)], [square(0.3, 0, 0.6, 0.2)]]
    g = pg.Grid(pg.boundary_bbox(polys), CELL)
    m = pg.inside_mask(g, polys)
    check("adjacent polygons union", int(m.sum()), 12)


def test_inside_triangle():
    # Centre rule: a cell is in when its centre is in.
    # Hypotenuse at 0.45 so no centre lies exactly on it.
    polys = [[[[0, 0], [0.45, 0], [0, 0.45], [0, 0]]]]
    g = pg.Grid(pg.boundary_bbox(polys), CELL)
    m = pg.inside_mask(g, polys)
    # Centres (i+0.5, j+0.5)*0.1 inside x+y<0.45: i+j+1<4.5 -> i+j<=3 -> 10 cells.
    check("triangle by centre", int(m.sum()), 10)
    check("corner cell out", bool(m[4, 4]), False)


def test_boundary_inputs():
    poly = {"type": "Polygon", "coordinates": [square(0, 0, 1, 1)]}
    multi = {"type": "MultiPolygon",
             "coordinates": [[square(0, 0, 1, 1)], [square(2, 2, 3, 3)]]}
    check("bare polygon", len(pg.boundary_polygons(poly)), 1)
    check("feature", len(pg.boundary_polygons({"type": "Feature", "geometry": poly})), 1)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": poly}, {"type": "Feature", "geometry": multi}]}
    check("collection unions all", len(pg.boundary_polygons(fc)), 3)
    try:
        pg.boundary_polygons({"type": "Point", "coordinates": [0, 0]})
        check("point rejected", "no error", "GridError")
    except pg.GridError:
        check("point rejected", "GridError", "GridError")


def test_dilate_and_select():
    inside = np.ones((5, 5), dtype=bool)
    frac = np.zeros((5, 5))
    frac[2, 2] = 0.5
    keep, forest = pg.select_cells(inside, frac, min_forest=0.01, ring=1)
    check("one forest cell", int(forest.sum()), 1)
    check("ring of 1 gives 3x3", int(keep.sum()), 9)
    keep0, _ = pg.select_cells(inside, frac, ring=0)
    check("ring 0 keeps forest only", int(keep0.sum()), 1)
    keep2, _ = pg.select_cells(inside, frac, ring=2)
    check("ring 2 gives 5x5", int(keep2.sum()), 25)

    # Below threshold is dropped.
    frac[2, 2] = 0.005
    keep, _ = pg.select_cells(inside, frac, min_forest=0.01)
    check("below threshold dropped", int(keep.sum()), 0)

    # Ring never leaks outside the boundary.
    inside2 = np.zeros((5, 5), dtype=bool)
    inside2[:, :3] = True
    frac2 = np.zeros((5, 5))
    frac2[2, 2] = 1.0
    keep, _ = pg.select_cells(inside2, frac2, ring=1)
    check("ring clipped to boundary", int(keep.sum()), 6)

    # Unknown forest is not clean forest-free land.
    frac3 = np.full((3, 3), np.nan)
    keep, forest = pg.select_cells(np.ones((3, 3), bool), frac3, ring=0)
    check("NaN fraction kept", int(keep.sum()), 9)

    try:
        pg.select_cells(inside, np.zeros((4, 4)))
        check("shape mismatch", "no error", "GridError")
    except pg.GridError:
        check("shape mismatch", "GridError", "GridError")


def test_aggregate_misaligned():
    # Grid 2x2 cells of 0.1; pixels of 0.04 that do not line up with cells.
    g = pg.Grid((0, 0, 0.2, 0.2), CELL)
    px = (np.arange(5) + 0.5) * 0.04          # centres 0.02 .. 0.18
    py = (np.arange(5) + 0.5) * 0.04
    forest = np.zeros((5, 5))
    forest[:, :2] = 1                          # pixels with lon centre < 0.08
    s, c = pg.aggregate_to_cells(forest, px, py, g)
    frac = s / c
    # Pixel centres 0.02,0.06 fall in col 0; 0.10,0.14,0.18 in col 1.
    check("pixel counts per cell", c.tolist(), [[4, 6], [6, 9]])
    check("west cells all forest", frac[:, 0].tolist(), [1.0, 1.0])
    check("east cells no forest", frac[:, 1].tolist(), [0.0, 0.0])
    # Pixels off the grid are ignored rather than wrapped.
    s, c = pg.aggregate_to_cells(np.ones((1, 1)), np.array([5.0]), np.array([5.0]), g)
    check("off-grid pixels ignored", float(c.sum()), 0.0)


def test_feature_collection():
    g = pg.Grid((0, 0, 0.3, 0.1), CELL)
    keep = np.array([[True, True, False]])
    forest = np.array([[True, False, False]])
    frac = np.array([[0.4, np.nan, 0.0]])
    fc = pg.to_feature_collection(g, keep, frac, forest, "LD", name="t")
    feats = fc["features"]
    check("two features", len(feats), 2)
    check("ids", [f["properties"]["plot_id"] for f in feats], ["LD-0-0", "LD-0-1"])
    check("roles", [f["properties"]["probe_role"] for f in feats], ["forest", "ring"])
    check("NaN written as null", feats[1]["properties"]["forest_frac_2020"], None)
    json.dumps(fc, allow_nan=False)  # raises if a NaN slipped through
    check("strict JSON", True, True)
    # Output is something run_whisp.py accepts.
    from eudr_risk import plots
    fd, path = tempfile.mkstemp(suffix=".geojson")
    with os.fdopen(fd, "w") as fh:
        json.dump(fc, fh)
    check("loads as plots", len(plots.load_geojson(path)["features"]), 2)
    os.remove(path)


def test_cell_area():
    g = pg.Grid((108.0, 11.5, 108.2, 11.7), 0.002)
    check("0.002 deg about 4.9 ha at 11.6 N", round(pg.cell_area_ha(g), 1), 4.9)


class _FakeImage:
    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        if name == "getDownloadURL":
            def url(params):
                self.log.append(params)
                return f"fake://{len(self.log) - 1}"
            return url
        return lambda *a, **k: self


def test_forest_fraction_ee():
    log = []
    fake = types.ModuleType("ee")
    fake.Image = lambda *a: _FakeImage(log)
    fake.Reducer = types.SimpleNamespace(mean=lambda: None)
    fake.Geometry = types.SimpleNamespace(Rectangle=lambda *a: a)
    saved = sys.modules.get("ee")
    sys.modules["ee"] = fake
    # forest.py imports ee at module level; reload it against the stand-in.
    sys.modules.pop("eudr_risk.forest", None)
    try:
        g = pg.Grid((0, 0, 0.3, 0.5), CELL)    # 5 rows x 3 cols
        # Truth, south-up like the grid: value = row index.
        truth = np.repeat(np.arange(5, dtype=float)[:, None], 3, axis=1)

        def fetch(url):
            params = log[int(url.split("//")[1])]
            w, h = map(int, params["dimensions"].split("x"))
            north = params["crsTransform"][5]
            top_row = round(north / CELL) - g.iy0
            tile = truth[top_row - h:top_row][::-1]         # north-up, as EE sends
            arr = np.zeros((h, w), dtype=[("forest2020", "<f4")])
            arr["forest2020"] = tile
            buf = io.BytesIO()
            np.save(buf, arr)
            return buf.getvalue()

        out = pg.forest_fraction_ee(g, max_pixels=6, fetch=fetch)   # 2 rows a tile
        check("EE tiles requested", len(log), 3)
        check("EE result flipped to south-up", out.tolist(), truth.tolist())
        check("EE request at grid transform", log[0]["crsTransform"][:3], [CELL, 0, g.west])
    finally:
        sys.modules.pop("eudr_risk.forest", None)
        if saved is None:
            sys.modules.pop("ee", None)
        else:
            sys.modules["ee"] = saved


def test_forest_fraction_raster():
    try:
        import rasterio
        from rasterio.transform import from_origin
    except ImportError:
        print("SKIP  rasterio not installed, GeoTIFF path untested")
        return
    # 10x10 raster of 0.02 deg pixels over (0,0)-(0.2,0.2): forest in the
    # north-west quarter, 255 nodata-style value elsewhere in the south-east.
    data = np.zeros((10, 10), dtype=np.uint8)
    data[:5, :5] = 1
    data[5:, 5:] = 255
    fd, path = tempfile.mkstemp(suffix=".tif")
    os.close(fd)
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1,
                       dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(0, 0.2, 0.02, 0.02)) as dst:
        dst.write(data, 1)
    try:
        g = pg.Grid((0, 0, 0.3, 0.2), CELL)    # one column beyond the raster
        frac = pg.forest_fraction_raster(path, g, strip_rows=3)
        check("raster NW cell forest", float(frac[1, 0]), 1.0)
        check("raster SW cell not forest", float(frac[0, 0]), 0.0)
        check("raster non-1 values not forest", float(frac[0, 1]), 0.0)
        check("uncovered column is NaN", bool(np.isnan(frac[:, 2]).all()), True)
    finally:
        os.remove(path)


def test_script_end_to_end():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import make_probe_grid
    tmp = tempfile.mkdtemp()
    boundary = os.path.join(tmp, "b.geojson")
    with open(boundary, "w") as fh:
        json.dump({"type": "Polygon", "coordinates": [square(108.0, 11.5, 108.02, 11.51)]}, fh)
    out = os.path.join(tmp, "cells.geojson")
    rc = make_probe_grid.main(["--boundary", boundary, "--forest", "none", "--out", out])
    check("script exit code", rc, 0)
    with open(out) as fh:
        n = len(json.load(fh)["features"])
    check("script cells (10 x 5)", n, 50)
    with open(os.path.splitext(out)[0] + "_summary.json") as fh:
        check("script whisp jobs", json.load(fh)["whisp_jobs"], 1)


def main():
    print("--- grid ---")
    test_grid_snapping()
    test_grid_errors()
    test_cell_area()
    print("\n--- boundary ---")
    test_boundary_inputs()
    test_inside_square()
    test_inside_hole()
    test_inside_shared_edge()
    test_inside_triangle()
    print("\n--- selection ---")
    test_dilate_and_select()
    test_feature_collection()
    print("\n--- forest backends ---")
    test_aggregate_misaligned()
    test_forest_fraction_ee()
    test_forest_fraction_raster()
    print("\n--- script ---")
    test_script_end_to_end()

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
