#!/usr/bin/env python
"""Probe cells over a province, filtered to JRC 2020 forest, ready for Whisp.

Cuts a regular lattice over a boundary, keeps the cells carrying JRC 2020
forest plus a ring around them, and writes a GeoJSON that
``run_whisp.py --plots`` takes as is. It prints what the Whisp run will cost
before you spend any quota.

The boundary is yours to supply. For the province as it stands after
1 July 2025, put the pre-merger Lam Dong, Dak Nong and Binh Thuan polygons in
one file (geoBoundaries VNM ADM1 or GADM still publish the old provinces);
the features are unioned.

Forest from Earth Engine, one request per tile of the grid::

    python scripts/make_probe_grid.py --boundary data/real/lamdong_2025.geojson \\
        --forest ee --project my-gee-project --out out/probe/lamdong.geojson

Forest from a local JRC GFC2020 tile (needs rasterio), no Earth Engine::

    python scripts/make_probe_grid.py --boundary data/real/lamdong_2025.geojson \\
        --forest JRC_GFC2020_V3_N20_E100.tif --out out/probe/lamdong.geojson

Add ``--dry-run`` to print the counts without writing the cells.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eudr_risk import probe_grid as pg
from eudr_risk.config import (
    WHISP_GEOMETRY_LIMIT_ASYNC,
    WHISP_MAX_CONCURRENT_JOBS,
    WHISP_TIMEOUT_ASYNC_S,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--boundary", required=True,
                   help="GeoJSON (Multi)Polygon boundary; several features are unioned.")
    p.add_argument("--forest", required=True,
                   help="'ee' for Earth Engine, 'none' for no filter, or a JRC GFC2020 GeoTIFF.")
    p.add_argument("--project", default=os.environ.get("GEE_PROJECT"),
                   help="Earth Engine Cloud project id (or set GEE_PROJECT).")
    p.add_argument("--cell-deg", type=float, default=pg.DEFAULT_CELL_DEG,
                   help="Cell edge in degrees (default %(default)s, about 220 m).")
    p.add_argument("--min-forest", type=float, default=pg.DEFAULT_MIN_FOREST,
                   help="Minimum JRC 2020 forest share for a cell to count (default %(default)s).")
    p.add_argument("--ring", type=int, default=pg.DEFAULT_RING,
                   help="Cells kept around each forest cell (default %(default)s).")
    p.add_argument("--prefix", default="LD", help="plot_id prefix (default %(default)s).")
    p.add_argument("--out", default=os.path.join("out", "probe", "probe_cells.geojson"))
    p.add_argument("--dry-run", action="store_true", help="Print counts, write nothing.")
    return p.parse_args(argv)


def forest_fractions(args, grid):
    import numpy as np

    if args.forest == "none":
        return np.ones(grid.shape)
    if args.forest == "ee":
        if not args.project:
            sys.exit("No Earth Engine project. Pass --project or set GEE_PROJECT.")
        import ee
        ee.Initialize(project=args.project)
        return pg.forest_fraction_ee(grid)
    if not os.path.exists(args.forest):
        sys.exit(f"No such forest raster: {args.forest}")
    try:
        return pg.forest_fraction_raster(args.forest, grid)
    except ImportError:
        sys.exit("Reading a GeoTIFF needs rasterio: pip install rasterio")


def summarise(grid, inside, keep, is_forest):
    n_inside = int(inside.sum())
    n_keep = int(keep.sum())
    n_forest = int(is_forest.sum())
    ha = pg.cell_area_ha(grid)
    jobs = math.ceil(n_keep / WHISP_GEOMETRY_LIMIT_ASYNC) if n_keep else 0
    waves = math.ceil(jobs / WHISP_MAX_CONCURRENT_JOBS)
    return {
        "cell_deg": grid.cell,
        "cell_area_ha": round(ha, 2),
        "grid_shape": list(grid.shape),
        "cells_inside_boundary": n_inside,
        "cells_forest": n_forest,
        "cells_ring": n_keep - n_forest,
        "cells_kept": n_keep,
        "share_dropped": round(1 - n_keep / n_inside, 3) if n_inside else None,
        "area_kept_km2": round(n_keep * ha / 100, 1),
        "whisp_jobs": jobs,
        "whisp_waves": waves,
        "whisp_worst_case_hours": round(waves * WHISP_TIMEOUT_ASYNC_S / 3600, 1),
    }


def main(argv=None):
    args = parse_args(argv)
    polygons = pg.load_boundary(args.boundary)
    grid = pg.Grid(pg.boundary_bbox(polygons), args.cell_deg)
    print(f"grid {grid.nrows} x {grid.ncols} cells of {grid.cell} deg "
          f"over {grid.west:.3f},{grid.south:.3f} .. {grid.east:.3f},{grid.north:.3f}")

    inside = pg.inside_mask(grid, polygons)
    frac = forest_fractions(args, grid)
    keep, is_forest = pg.select_cells(inside, frac, args.min_forest, args.ring)
    s = summarise(grid, inside, keep, is_forest)

    print(f"inside boundary : {s['cells_inside_boundary']:>9,}")
    print(f"forest cells    : {s['cells_forest']:>9,}  (JRC 2020 share >= {args.min_forest})")
    print(f"ring cells      : {s['cells_ring']:>9,}  (within {args.ring} cell of forest)")
    print(f"kept            : {s['cells_kept']:>9,}  ({s['area_kept_km2']:,} km2, "
          f"{s['share_dropped']:.0%} dropped)" if s["share_dropped"] is not None
          else f"kept            : {s['cells_kept']:>9,}")
    print(f"Whisp cost      : {s['whisp_jobs']} job(s) of up to {WHISP_GEOMETRY_LIMIT_ASYNC:,}, "
          f"{s['whisp_waves']} wave(s) at {WHISP_MAX_CONCURRENT_JOBS} concurrent, "
          f"worst case ~{s['whisp_worst_case_hours']} h. Free, but a shared public API.")

    if args.dry_run:
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fc = pg.to_feature_collection(grid, keep, frac, is_forest, args.prefix,
                                  name=os.path.splitext(os.path.basename(args.out))[0])
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(fc, fh, separators=(",", ":"))
    summary_path = os.path.splitext(args.out)[0] + "_summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump({**s, "boundary": args.boundary, "forest": args.forest,
                   "min_forest": args.min_forest, "ring": args.ring}, fh, indent=2)
    print(f"wrote {args.out}\nwrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
