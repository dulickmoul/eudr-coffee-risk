"""Probe grids: regular lattice cells over a region, kept only near 2020 forest.

Province-wide screening has no farm boundaries to send, so it sends probe
cells instead: squares on a fixed lattice that the screening backend treats
as plots. Most of a coffee province is farmland that carried no forest at the
end of 2020, and Whisp's perennial-crop rule returns ``low`` for land with no
tree cover at the cutoff, so sending those cells spends quota to learn nothing.
This module keeps the cells that carry JRC 2020 forest, plus a ring around
them, and drops the rest before anything reaches the API.

The ring is a hedge, not decoration. Whisp's ``Ind_01_treecover`` reads
several tree-cover datasets, not JRC alone, so a cell with zero JRC forest can
still clear Whisp's tree-cover gate and come back ``high``. Those cells sit
next to JRC forest far more often than in the middle of settled farmland.

Probe cells are nobody's parcel. A province run is a hotspot map that tells a
field team where to look first, not a per-farm EUDR finding.

Only numpy is required here. Forest fractions come from a backend:
``forest_fraction_ee`` (Earth Engine, one request per tile of the grid) or
``forest_fraction_raster`` (a local JRC GFC2020 GeoTIFF, needs rasterio).
"""

import io
import json
import math

import numpy as np

from .config import JRC_FOREST_VALUE

# 0.002 degrees is about 220 m at Lam Dong's latitude, about 4.9 ha a cell:
# the size of the probe cells behind the frontier sweeps in the README.
DEFAULT_CELL_DEG = 0.002

# Keep a cell when at least this share of it was JRC forest in 2020. 1% of
# a 4.9 ha cell is about 5 pixels at 10 m, enough to be more than noise.
DEFAULT_MIN_FOREST = 0.01

# Cells of ring kept around every forest cell (Chebyshev distance).
DEFAULT_RING = 1

# Earth Engine's getDownloadURL refuses requests over 48 MB. Stay far below.
EE_MAX_PIXELS_PER_REQUEST = 2_000_000


class GridError(ValueError):
    pass


# --- grid -----------------------------------------------------------------

class Grid:
    """A lattice snapped to multiples of the cell size.

    Snapping makes a cell's id depend only on where it is, never on the
    boundary it was cut from: rerun with a corrected boundary and every cell
    that survives keeps its id, so results join across runs.

    Row 0 is the southernmost row. ``iy0``/``ix0`` are the absolute lattice
    indices of row 0 and column 0.
    """

    def __init__(self, bbox, cell_deg=DEFAULT_CELL_DEG):
        if cell_deg <= 0:
            raise GridError("cell size must be positive")
        west, south, east, north = bbox
        if not (west < east and south < north):
            raise GridError(f"empty bounding box: {bbox}")
        self.cell = float(cell_deg)
        self.ix0 = math.floor(west / self.cell)
        self.iy0 = math.floor(south / self.cell)
        self.ncols = math.ceil(east / self.cell) - self.ix0
        self.nrows = math.ceil(north / self.cell) - self.iy0

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    @property
    def west(self):
        return self.ix0 * self.cell

    @property
    def south(self):
        return self.iy0 * self.cell

    @property
    def east(self):
        return (self.ix0 + self.ncols) * self.cell

    @property
    def north(self):
        return (self.iy0 + self.nrows) * self.cell

    def centres_x(self):
        return (self.ix0 + np.arange(self.ncols) + 0.5) * self.cell

    def centres_y(self):
        return (self.iy0 + np.arange(self.nrows) + 0.5) * self.cell

    def cell_id(self, row, col, prefix):
        return f"{prefix}-{self.iy0 + row}-{self.ix0 + col}"

    def cell_ring(self, row, col):
        x0 = (self.ix0 + col) * self.cell
        y0 = (self.iy0 + row) * self.cell
        x1, y1 = x0 + self.cell, y0 + self.cell
        r = lambda v: round(v, 7)
        # Counter-clockwise exterior, as RFC 7946 asks.
        return [[r(x0), r(y0)], [r(x1), r(y0)], [r(x1), r(y1)],
                [r(x0), r(y1)], [r(x0), r(y0)]]


# --- boundary -------------------------------------------------------------

def load_boundary(path):
    """Read a boundary GeoJSON into a list of polygons (lists of rings).

    Accepts a FeatureCollection, a Feature or a bare geometry, with Polygon
    or MultiPolygon geometries. Several features are unioned, which is how
    the post-July-2025 Lam Dong is built: pass the old Lam Dong, Dak Nong
    and Binh Thuan boundaries in one file.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return boundary_polygons(data)


def boundary_polygons(data):
    kind = data.get("type")
    if kind == "FeatureCollection":
        geoms = [f.get("geometry") for f in data.get("features") or []]
    elif kind == "Feature":
        geoms = [data.get("geometry")]
    else:
        geoms = [data]
    polygons = []
    for g in geoms:
        if not g:
            continue
        if g["type"] == "Polygon":
            polygons.append(g["coordinates"])
        elif g["type"] == "MultiPolygon":
            polygons.extend(g["coordinates"])
        else:
            raise GridError(f"boundary geometry must be (Multi)Polygon, got {g['type']}")
    if not polygons:
        raise GridError("boundary has no polygons")
    return polygons


def boundary_bbox(polygons):
    xs = [p[0] for poly in polygons for ring in poly for p in ring]
    ys = [p[1] for poly in polygons for ring in poly for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def inside_mask(grid, polygons):
    """Boolean (rows, cols): is the cell centre inside the boundary?

    Scanline even-odd per polygon, so holes work, then OR across polygons.
    Per polygon rather than over all rings at once because adjacent
    provinces share edges, and a shared edge counted from both sides would
    cancel itself out.
    """
    cx = grid.centres_x()
    cy = grid.centres_y()
    mask = np.zeros(grid.shape, dtype=bool)
    for poly in polygons:
        edges = []
        for ring in poly:
            pts = np.asarray(ring, dtype=float)[:, :2]
            if len(pts) < 3:
                continue
            if not np.array_equal(pts[0], pts[-1]):
                pts = np.vstack([pts, pts[:1]])
            edges.append(np.hstack([pts[:-1], pts[1:]]))
        if not edges:
            continue
        e = np.vstack(edges)
        x1, y1, x2, y2 = e[:, 0], e[:, 1], e[:, 2], e[:, 3]
        lo, hi = np.minimum(y1, y2), np.maximum(y1, y2)
        for row in np.nonzero((cy >= lo.min()) & (cy <= hi.max()))[0]:
            y = cy[row]
            hit = (y1 > y) != (y2 > y)
            if not hit.any():
                continue
            xs = x1[hit] + (y - y1[hit]) * (x2[hit] - x1[hit]) / (y2[hit] - y1[hit])
            xs.sort()
            mask[row] |= (np.searchsorted(xs, cx) % 2) == 1
    return mask


# --- selection ------------------------------------------------------------

def dilate(mask, ring):
    """Grow a boolean grid by ``ring`` cells in every direction (8-connected)."""
    if ring <= 0:
        return mask.copy()
    rows, cols = mask.shape
    padded = np.pad(mask, ring)
    out = np.zeros_like(mask)
    for dy in range(2 * ring + 1):
        for dx in range(2 * ring + 1):
            out |= padded[dy:dy + rows, dx:dx + cols]
    return out


def select_cells(inside, forest_frac, min_forest=DEFAULT_MIN_FOREST, ring=DEFAULT_RING):
    """Cells to send: inside the boundary and at or near 2020 forest.

    Returns ``(keep, is_forest)``. ``is_forest`` marks the cells that pass
    the forest threshold themselves, so ring cells can be told apart later.
    A NaN fraction (no data) counts as forest: unknown is not clean.
    """
    if forest_frac.shape != inside.shape:
        raise GridError(f"forest grid {forest_frac.shape} != boundary grid {inside.shape}")
    frac = np.where(np.isnan(forest_frac), 1.0, forest_frac)
    is_forest = (frac >= min_forest) & inside
    keep = dilate(is_forest, ring) & inside
    return keep, is_forest


def to_feature_collection(grid, keep, forest_frac, is_forest, prefix, name=None):
    features = []
    rows, cols = np.nonzero(keep)
    for row, col in zip(rows.tolist(), cols.tolist()):
        f = forest_frac[row, col]
        features.append({
            "type": "Feature",
            "properties": {
                "plot_id": grid.cell_id(row, col, prefix),
                "forest_frac_2020": None if np.isnan(f) else round(float(f), 4),
                "probe_role": "forest" if is_forest[row, col] else "ring",
            },
            "geometry": {"type": "Polygon", "coordinates": [grid.cell_ring(row, col)]},
        })
    fc = {"type": "FeatureCollection"}
    if name:
        fc["name"] = name
    fc["note"] = ("Machine-generated probe cells on a regular lattice, not farm "
                  "boundaries. Lon/lat WGS84 (EPSG:4326).")
    fc["features"] = features
    return fc


def cell_area_ha(grid):
    """Approximate area of one cell at the grid's middle latitude."""
    lat = math.radians((grid.south + grid.north) / 2)
    side_y = grid.cell * 111_320.0
    side_x = grid.cell * 111_320.0 * math.cos(lat)
    return side_x * side_y / 1e4


# --- forest backends ------------------------------------------------------

def aggregate_to_cells(forest, px_lon, px_lat, grid):
    """Mean of a 0/1 pixel block per grid cell.

    ``forest`` is (h, w) of 0/1 (or bool), ``px_lon``/``px_lat`` the pixel
    centre coordinates (length w and h). Pixels are assigned to the cell
    holding their centre, so pixel and cell edges need not line up. Returns
    ``(sums, counts)`` over the full grid shape so blocks can be accumulated.
    """
    sums = np.zeros(grid.shape, dtype=np.float64)
    counts = np.zeros(grid.shape, dtype=np.float64)
    col = np.floor(px_lon / grid.cell).astype(np.int64) - grid.ix0
    row = np.floor(px_lat / grid.cell).astype(np.int64) - grid.iy0
    okc = (col >= 0) & (col < grid.ncols)
    okr = (row >= 0) & (row < grid.nrows)
    if not okc.any() or not okr.any():
        return sums, counts
    f = np.asarray(forest, dtype=np.float64)[np.ix_(okr, okc)]
    r = np.repeat(row[okr], okc.sum())
    c = np.tile(col[okc], okr.sum())
    flat = r * grid.ncols + c
    n = grid.nrows * grid.ncols
    sums += np.bincount(flat, weights=f.ravel(), minlength=n).reshape(grid.shape)
    counts += np.bincount(flat, minlength=n).reshape(grid.shape)
    return sums, counts


def forest_fraction_raster(path, grid, forest_value=JRC_FOREST_VALUE, strip_rows=2048):
    """JRC forest share per cell from a local GFC2020 GeoTIFF (EPSG:4326).

    Reads in horizontal strips so a full 10 m tile never sits in memory.
    Cells the raster does not cover come back NaN, which ``select_cells``
    treats as forest rather than silently clearing them. Clip the JRC tile
    to the province in QGIS first if disk is tight; the result is the same.
    """
    import rasterio
    from rasterio.windows import Window, from_bounds

    sums = np.zeros(grid.shape)
    counts = np.zeros(grid.shape)
    with rasterio.open(path) as src:
        if src.crs is not None and src.crs.to_epsg() != 4326:
            raise GridError(f"{path} is {src.crs}, expected EPSG:4326 like the JRC tiles")
        win = from_bounds(grid.west, grid.south, grid.east, grid.north, src.transform)
        win = win.round_offsets().round_lengths().intersection(
            Window(0, 0, src.width, src.height))
        t = src.transform
        cols = np.arange(win.col_off, win.col_off + win.width)
        px_lon = t.c + (cols + 0.5) * t.a
        for r0 in range(int(win.row_off), int(win.row_off + win.height), strip_rows):
            h = min(strip_rows, int(win.row_off + win.height) - r0)
            strip = src.read(1, window=Window(win.col_off, r0, win.width, h))
            px_lat = t.f + (np.arange(r0, r0 + h) + 0.5) * t.e
            s, c = aggregate_to_cells(strip == forest_value, px_lon, px_lat, grid)
            sums += s
            counts += c
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / counts, np.nan)


def forest_fraction_ee(grid, max_pixels=EE_MAX_PIXELS_PER_REQUEST, fetch=None):
    """JRC forest share per cell, aggregated server-side by Earth Engine.

    The 10 m mask is averaged onto exactly this lattice (reduceResolution
    then reproject to the grid's own transform) and downloaded as NPY, one
    request per horizontal tile, so a province costs a handful of requests
    rather than one per cell. Needs ``ee.Initialize`` to have been called.
    ``fetch(url) -> bytes`` is injectable for testing.
    """
    import ee

    from .config import JRC_GFC2020_ASSET
    from .forest import jrc_forest_2020

    if fetch is None:
        import requests

        def fetch(url):
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            return r.content

    native = ee.Image(JRC_GFC2020_ASSET).projection()
    img = (jrc_forest_2020()
           .setDefaultProjection(native)
           .reduceResolution(ee.Reducer.mean(), maxPixels=65535)
           .toFloat())

    out = np.full(grid.shape, np.nan)
    rows_per_tile = max(1, max_pixels // grid.ncols)
    for r0 in range(0, grid.nrows, rows_per_tile):
        r1 = min(grid.nrows, r0 + rows_per_tile)
        south = grid.south + r0 * grid.cell
        north = grid.south + r1 * grid.cell
        transform = [grid.cell, 0, grid.west, 0, -grid.cell, north]
        url = img.getDownloadURL({
            "format": "NPY",
            "crs": "EPSG:4326",
            "crsTransform": transform,
            "dimensions": f"{grid.ncols}x{r1 - r0}",
            "region": ee.Geometry.Rectangle(
                [grid.west, south, grid.east, north], "EPSG:4326", False),
        })
        tile = np.load(io.BytesIO(fetch(url)), allow_pickle=False)
        tile = tile["forest2020"] if tile.dtype.names else tile
        if tile.shape != (r1 - r0, grid.ncols):
            raise GridError(f"Earth Engine returned {tile.shape}, "
                            f"expected {(r1 - r0, grid.ncols)}")
        # NPY comes north-up; the grid's row 0 is south.
        out[r0:r1] = np.asarray(tile, dtype=float)[::-1]
    return out
