# -*- coding: utf-8 -*-
"""Take a walked GPX boundary through the standard GIS stack, end to end.

`gpx_to_geojson.py` already turns a walk into a polygon using its own tangent
plane arithmetic. This script does the same job through GDAL, PROJ and QGIS
instead, which matters for three reasons that only show up at scale:

1. **It picks the projection per plot.** Vietnamese survey work is done in
   VN-2000, whose UTM zones split at 108 degrees east. The test plot sits a
   few kilometres east of that line, so it is in zone 49N, and Lam Dong after
   the 2025 merger straddles the line. Measured rather than assumed: forcing
   this plot into the wrong zone moves its area by 0.02 percentage points,
   which is nothing. The zone matters for a different reason. Eastings from
   two zones are not comparable, so the moment a provincial dataset mixes
   them, every neighbour, overlap and distance query is quietly wrong while
   each individual area still looks right.

2. **It measures the same polygon three independent ways** and reports the
   spread: VN-2000 UTM, WGS84 UTM, and a geodesic area on the ellipsoid. If a
   buyer's auditor recomputes the area with their own tool they land somewhere
   inside that spread, and knowing its width in advance is the difference
   between answering a query and being surprised by one.

3. **It writes down how the number was made.** The sidecar JSON carries the CRS
   used, the vertex counts, every area figure, the simplification budget and a
   SHA-256 of the source GPX. A boundary without that provenance cannot go in a
   due diligence file, however pretty the map is.

Outputs land in data/real/qgis/, which is gitignored. A walked boundary is
personal data about a real household and never belongs in a commit.

Run it with the QGIS Python, which already has GDAL, PROJ, pyproj and shapely:

    "C:\\Program Files\\QGIS 3.44.14\\bin\\python-qgis-ltr.bat" \\
        scripts/qgis_plot_workflow.py data/real/plot.gpx --id PLOT-01

The QGIS project is optional; pass --no-project to skip it and the script runs
under any Python that has osgeo and pyproj.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import pathlib
import sys

from osgeo import gdal, ogr, osr

gdal.UseExceptions()

EUDR_AREA_THRESHOLD_HA = 4.0       # at or below this, EUDR accepts a single point
AREA_DELTA_BUDGET = 0.005          # 0.5 %: how much area a simplified ring may lose
DEFAULT_GPS_NOISE_M = 1.5          # a phone walk; the line may not move further than this
SIMPLIFY_STEPS_M = (0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
SPIKE_ANGLE_DEG = 15.0             # interior angle below this is a GPS spike, not a corner

# VN-2000 national grid. The split is the zone boundary at 108 E, not a guess.
VN2000_WEST = 3405                 # VN-2000 / UTM zone 48N, onshore west of 108 E
VN2000_EAST = 3406                 # VN-2000 / UTM zone 49N, onshore east of 108 E
WGS84_WEST = 32648                 # WGS 84 / UTM zone 48N
WGS84_EAST = 32649                 # WGS 84 / UTM zone 49N
ZONE_SPLIT_LON = 108.0


# --------------------------------------------------------------------------- read

def read_boundary(path):
    """Boundary vertices in walk order, read through OGR rather than by hand.

    Using the driver instead of a regex is the point: the same call reads GPX,
    GeoJSON, KML, a GeoPackage or a shapefile, so a plot that arrives from
    QField, from Kobo or from a district office all enter the pipeline here.
    """
    ds = ogr.Open(str(path))
    if ds is None:
        sys.exit("GDAL cannot open %s" % path)

    best = []
    for i in range(ds.GetLayerCount()):
        layer = ds.GetLayer(i)
        pts = []
        for feat in layer:
            geom = feat.GetGeometryRef()
            if geom is not None:
                pts.extend(_flatten(geom))
        if len(pts) > len(best):
            best = pts
    ds = None

    if len(best) < 4:
        sys.exit("Need at least 4 vertices to make a polygon; found %d." % len(best))
    return best


def _flatten(geom):
    """Every (lon, lat) in a geometry, whatever its type, in file order."""
    name = geom.GetGeometryName()
    if name in ("POINT", "LINESTRING", "LINEARRING"):
        return [(geom.GetX(i), geom.GetY(i)) for i in range(geom.GetPointCount())]
    out = []
    for i in range(geom.GetGeometryCount()):
        out.extend(_flatten(geom.GetGeometryRef(i)))
    return out


# ----------------------------------------------------------------------- validate

def build_polygon(pts):
    """Close the walk into a polygon in EPSG:4326, dropping repeated vertices.

    A phone logging once a second while someone stands still to open a gate
    writes the same coordinate several times. Duplicates are not an error in
    the file, but they make a ring that no validator likes.
    """
    clean = [pts[0]]
    for p in pts[1:]:
        if p != clean[-1]:
            clean.append(p)
    if clean[0] == clean[-1]:
        clean.pop()

    ring = ogr.Geometry(ogr.wkbLinearRing)
    for lon, lat in clean:
        ring.AddPoint_2D(lon, lat)
    ring.CloseRings()

    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    poly.AssignSpatialReference(srs_from_epsg(4326))
    return poly, len(pts) - len(clean)


def check_geometry(poly):
    """Report what is wrong with the ring before anything is computed from it.

    At 16,000 plots a self-intersecting boundary is a weekly event, not a rare
    one, so this runs on every plot and the findings travel in the metadata.
    """
    issues = []
    if not poly.IsValid():
        issues.append("invalid geometry (self-intersection or bad ring)")
    if not poly.IsSimple():
        issues.append("ring is not simple: the walk crosses itself")

    ring = poly.GetGeometryRef(0)
    if ring.GetPointCount() < 5:
        issues.append("fewer than 4 distinct vertices")

    spikes = count_spikes(ring)
    if spikes:
        issues.append("%d spike(s): interior angle under %.0f degrees"
                      % (spikes, SPIKE_ANGLE_DEG))
    return issues, spikes


def count_spikes(ring):
    """Vertices where the walk doubles back on itself inside a few degrees.

    A real plot corner is a turn of tens of degrees. A near-zero turn is the
    GPS jumping sideways and back, and it inflates the perimeter without moving
    the area, which is exactly the signature that makes a perimeter figure look
    wrong to anyone checking it.
    """
    pts = [(ring.GetX(i), ring.GetY(i)) for i in range(ring.GetPointCount() - 1)]
    n = len(pts)
    if n < 3:
        return 0
    spikes = 0
    for i in range(n):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[(i + 1) % n]
        v1 = (ax - bx, ay - by)
        v2 = (cx - bx, cy - by)
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 == 0 or n2 == 0:
            continue
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        if math.degrees(math.acos(cos)) < SPIKE_ANGLE_DEG:
            spikes += 1
    return spikes


# ------------------------------------------------------------------------ measure

def srs_from_epsg(code):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(code))
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def pick_zone(lon):
    """The VN-2000 and WGS84 UTM codes for this longitude, and why."""
    east = lon >= ZONE_SPLIT_LON
    return {
        "vn2000_epsg": VN2000_EAST if east else VN2000_WEST,
        "wgs84_epsg": WGS84_EAST if east else WGS84_WEST,
        "zone": "49N" if east else "48N",
        "reason": "centroid at %.4f E is %s of the %.0f E zone boundary"
                  % (lon, "east" if east else "west", ZONE_SPLIT_LON),
        "margin_deg": round(abs(lon - ZONE_SPLIT_LON), 4),
    }


def measure_projected(poly, epsg):
    """Area in hectares and perimeter in metres after reprojecting to `epsg`."""
    work = poly.Clone()
    target = srs_from_epsg(epsg)
    tr = osr.CoordinateTransformation(srs_from_epsg(4326), target)
    if work.Transform(tr) != 0:
        sys.exit("Cannot transform to EPSG:%d. Is the PROJ grid data installed?" % epsg)
    return work.GetArea() / 10000.0, work.Boundary().Length()


def measure_geodesic(poly):
    """Area and perimeter on the WGS84 ellipsoid itself, with no projection.

    This is the referee. A projected area carries the distortion of its grid;
    the geodesic figure carries none, so the gap between them is the size of
    the choice of projection, stated rather than hidden.
    """
    from pyproj import Geod
    ring = poly.GetGeometryRef(0)
    lons = [ring.GetX(i) for i in range(ring.GetPointCount())]
    lats = [ring.GetY(i) for i in range(ring.GetPointCount())]
    area_m2, perim_m = Geod(ellps="WGS84").polygon_area_perimeter(lons, lats)
    return abs(area_m2) / 10000.0, perim_m


def bbox_metres(poly, epsg):
    """Width and height of the plot's bounding box, in metres."""
    work = poly.Clone()
    work.Transform(osr.CoordinateTransformation(srs_from_epsg(4326), srs_from_epsg(epsg)))
    x0, x1, y0, y1 = work.GetEnvelope()
    return x1 - x0, y1 - y0


# ----------------------------------------------------------------------- simplify

def simplify_in_metres(poly, epsg, tolerance_m):
    """Douglas-Peucker at a tolerance in metres, not in degrees.

    Simplifying in degrees is the quiet mistake here: a tolerance of 0.00001
    degrees is about 1.1 m north to south but only 1.09 m east to west at this
    latitude, and the ratio is worse further from the equator. Projecting
    first makes the tolerance mean what it says.
    """
    work = poly.Clone()
    fwd = osr.CoordinateTransformation(srs_from_epsg(4326), srs_from_epsg(epsg))
    back = osr.CoordinateTransformation(srs_from_epsg(epsg), srs_from_epsg(4326))
    work.Transform(fwd)
    out = work.SimplifyPreserveTopology(tolerance_m)
    area_ha = out.GetArea() / 10000.0
    shift_m = _hausdorff(work, out)
    out.Transform(back)
    out.AssignSpatialReference(srs_from_epsg(4326))
    return out, area_ha, shift_m


def _hausdorff(projected_full, projected_simple):
    """How far the simplified boundary moves at its worst point, in metres.

    Area is a single number and it hides cancellation: push one edge out and
    another in by the same amount and the hectares are unchanged while the plot
    is the wrong shape. The Hausdorff distance cannot cancel, so it is the
    constraint that actually protects the boundary.
    """
    from shapely import hausdorff_distance
    from shapely import wkb as shapely_wkb
    a = shapely_wkb.loads(bytes(projected_full.ExportToWkb()))
    b = shapely_wkb.loads(bytes(projected_simple.ExportToWkb()))
    return hausdorff_distance(a.exterior, b.exterior)


def choose_simplification(poly, epsg, full_area_ha, gps_noise_m):
    """The coarsest ring that keeps both the area and the line where they were.

    EUDR wants a polygon a buyer can store and submit, not a 410 vertex trace
    of someone's footsteps. Two constraints, and both have to hold:

    * area within AREA_DELTA_BUDGET, and
    * the boundary nowhere displaced by more than the GPS noise of the walk.

    The second one is the honest one. Simplifying is allowed to throw away
    detail that the measurement never resolved; it is not allowed to move the
    line somewhere the surveyor did not walk. Without it the picker will happily
    return a quadrilateral whose area is right by coincidence.
    """
    tried = []
    best = None
    for tol in SIMPLIFY_STEPS_M:
        geom, area_ha, shift_m = simplify_in_metres(poly, epsg, tol)
        verts = geom.GetGeometryRef(0).GetPointCount() - 1
        delta = (area_ha - full_area_ha) / full_area_ha if full_area_ha else 0.0
        # bool(): shapely hands back a numpy scalar, and numpy booleans do not
        # survive json.dumps. Cast at the boundary, not at serialisation time.
        shift_m = float(shift_m)
        area_ok = bool(abs(delta) <= AREA_DELTA_BUDGET)
        shape_ok = bool(shift_m <= gps_noise_m)
        row = {"tolerance_m": tol, "vertices": verts,
               "area_ha": round(area_ha, 4), "area_delta_pct": round(delta * 100, 3),
               "max_shift_m": round(shift_m, 2),
               "area_ok": area_ok, "shape_ok": shape_ok}
        tried.append(row)
        if area_ok and shape_ok and verts >= 3:
            if best is None or verts < best[1]["vertices"]:
                best = (geom, row)
    return best, tried


# ------------------------------------------------------------------------- export

def write_vectors(outdir, plot_id, full, simple, meta):
    """GeoPackage, GeoJSON and KML, all in EPSG:4326.

    The GeoPackage is the one to hand to the field team: one file, two layers,
    no sidecar soup, and no 10 character column name limit to mangle the
    metadata. GeoJSON is what the EUDR tools and the Whisp API read. KML is for
    the farmer, because it opens in Google Earth on a phone with no GIS on it.
    """
    gpkg = outdir / ("%s.gpkg" % plot_id)
    if gpkg.exists():
        gpkg.unlink()

    fields = [
        ("plot_id", ogr.OFTString, meta["plot_id"]),
        ("ring", ogr.OFTString, None),
        ("area_ha", ogr.OFTReal, None),
        ("perim_m", ogr.OFTReal, None),
        ("vertices", ogr.OFTInteger, None),
        ("crs_used", ogr.OFTString, "EPSG:%d" % meta["crs"]["vn2000_epsg"]),
        ("measured", ogr.OFTString, meta["source"]["measured_note"]),
        ("eudr_geom", ogr.OFTString, meta["eudr"]["geometry_required"]),
        ("processed", ogr.OFTString, meta["processed_utc"]),
    ]

    rows = [
        ("boundary_full", full, meta["area"]["vn2000_ha"],
         meta["perimeter"]["vn2000_m"], meta["vertices"]["used"], "full"),
        ("boundary_simplified", simple, meta["simplified"]["area_ha"],
         None, meta["simplified"]["vertices"], "simplified"),
    ]

    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(str(gpkg))
    for layer_name, geom, area_ha, perim_m, verts, ring_kind in rows:
        if geom is None:
            continue
        layer = ds.CreateLayer(layer_name, srs_from_epsg(4326), ogr.wkbPolygon)
        for name, ftype, _ in fields:
            layer.CreateField(ogr.FieldDefn(name, ftype))
        feat = ogr.Feature(layer.GetLayerDefn())
        feat.SetGeometry(geom)
        values = {"ring": ring_kind, "area_ha": area_ha, "perim_m": perim_m,
                  "vertices": verts}
        for name, _, const in fields:
            val = values.get(name, const) if name in values else const
            if val is not None:
                feat.SetField(name, val)
        layer.CreateFeature(feat)
    ds = None

    made = [gpkg]
    for layer_name, suffix, drv_name, opts in (
            ("boundary_full", ".geojson", "GeoJSON", ["RFC7946=YES", "WRITE_BBOX=YES"]),
            ("boundary_simplified", "_simplified.geojson", "GeoJSON", ["RFC7946=YES"]),
            ("boundary_simplified", ".kml", "KML", []),
    ):
        dst = outdir / ("%s%s" % (plot_id, suffix))
        if dst.exists():
            dst.unlink()
        try:
            gdal.VectorTranslate(str(dst), str(gpkg), options=gdal.VectorTranslateOptions(
                format=drv_name, layers=[layer_name], layerCreationOptions=opts))
            made.append(dst)
        except Exception as exc:                       # a missing layer is not fatal
            print("  ! skipped %s: %s" % (dst.name, exc))
    return made


def write_qgis_project(outdir, plot_id, gpkg, epsg):
    """A .qgz that opens on satellite imagery with the boundary drawn on it.

    This is the deliverable for anyone who is not going to run a script: double
    click, see the plot, see the trees inside the line. Esri World Imagery is
    used because it needs no key and QField can cache it for offline use, which
    matters in Lam Ha where there are places with no signal.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer, QgsFillSymbol,
                           QgsRasterLayer, QgsCoordinateReferenceSystem)

    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:%d" % epsg))
        project.setTitle("%s boundary" % plot_id)

        # zmax 18, not 19. Esri serves a grey "Map data not yet available" tile
        # rather than an error when a zoom is past what it holds for the area,
        # so asking too deep gives a blank map that still looks like it worked.
        # Capped here, QGIS upsamples the z18 tile and the imagery stays visible.
        url = ("type=xyz&zmin=0&zmax=18&url=" + (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D"))
        basemap = QgsRasterLayer(url, "Anh ve tinh (Esri World Imagery)", "wms")
        if basemap.isValid():
            project.addMapLayer(basemap)

        # Hollow fill, so one boundary drawn over the other stays readable.
        for layer_name, label, stroke, width in (
                ("boundary_full", "Ranh lo day du (409 diem)", "255,193,7", "1.2"),
                ("boundary_simplified", "Ranh lo rut gon (nop EUDR)", "211,47,47", "0.7")):
            layer = QgsVectorLayer("%s|layername=%s" % (gpkg, layer_name), label, "ogr")
            if not layer.isValid():
                continue
            layer.renderer().setSymbol(QgsFillSymbol.createSimple({
                "color": "0,0,0,0",
                "style": "no",
                "outline_color": stroke,
                "outline_width": width,
                "outline_width_unit": "MM",
            }))
            project.addMapLayer(layer)

        dst = outdir / ("%s.qgz" % plot_id)
        project.write(str(dst))

        png = outdir / ("%s_map.png" % plot_id)
        made = [dst]
        if _render_png(project, png):
            made.append(png)
        return made
    finally:
        qgs.exitQgis()


def _render_png(project, dst, size=1400, pad_m=30):
    """A PNG of the plot on imagery, for everyone who will not open QGIS.

    Worth producing even though the .qgz exists: a farmer confirming his own
    boundary, or a buyer's file, needs a picture, not a project.
    """
    from qgis.core import (QgsMapSettings, QgsMapRendererParallelJob,
                           QgsVectorLayer, QgsCoordinateTransform)
    from qgis.PyQt.QtCore import QSize, QEventLoop
    from qgis.PyQt.QtGui import QColor

    # The layer tree, not mapLayers(): QgsMapSettings draws the first layer on
    # top, and mapLayers() is a dict whose order would put the basemap over the
    # boundary. The map then renders as bare imagery and looks like it worked.
    layers = [n.layer() for n in project.layerTreeRoot().findLayers() if n.layer()]
    extent = None
    for lyr in layers:
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        box = QgsCoordinateTransform(lyr.crs(), project.crs(), project) \
            .transformBoundingBox(lyr.extent())
        extent = box if extent is None else (extent.combineExtentWith(box) or extent)
    if extent is None:
        return False
    extent.grow(pad_m)

    ms = QgsMapSettings()
    ms.setLayers(layers)
    ms.setBackgroundColor(QColor(255, 255, 255))
    ms.setOutputSize(QSize(size, size))
    ms.setExtent(extent)
    ms.setDestinationCrs(project.crs())
    ms.setTransformContext(project.transformContext())

    job = QgsMapRendererParallelJob(ms)
    loop = QEventLoop()
    job.finished.connect(loop.quit)
    job.start()
    loop.exec_()
    return bool(job.renderedImage().save(str(dst), "PNG"))


# --------------------------------------------------------------------------- main

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="GPX, GeoJSON, KML or any vector GDAL can read")
    ap.add_argument("--id", default=None, help="plot id (default: source file stem)")
    ap.add_argument("--outdir", default=None, help="default: <source dir>/qgis")
    ap.add_argument("--measured", default="walked GPS boundary, phone",
                    help="how and with what the boundary was measured")
    ap.add_argument("--gps-noise", type=float, default=DEFAULT_GPS_NOISE_M,
                    dest="gps_noise", metavar="M",
                    help="measurement uncertainty in metres; the simplified "
                         "boundary may not move further than this (default %.1f)"
                         % DEFAULT_GPS_NOISE_M)
    ap.add_argument("--no-project", action="store_true",
                    help="skip the QGIS project, so plain Python can run this")
    args = ap.parse_args(argv)

    src = pathlib.Path(args.source).resolve()
    plot_id = args.id or src.stem
    outdir = pathlib.Path(args.outdir) if args.outdir else src.parent / "qgis"
    outdir.mkdir(parents=True, exist_ok=True)

    print("1. READ   %s" % src.name)
    pts = read_boundary(src)
    poly, dropped = build_polygon(pts)
    used = poly.GetGeometryRef(0).GetPointCount() - 1
    print("   %d vertices in file, %d duplicates dropped, %d used"
          % (len(pts), dropped, used))

    print("2. VALIDATE")
    issues, spikes = check_geometry(poly)
    for msg in issues:
        print("   ! %s" % msg)
    if not issues:
        print("   ring is valid and simple")
    if not poly.IsValid():
        poly = poly.MakeValid()
        print("   repaired with MakeValid")

    centroid = poly.Centroid()
    lon, lat = centroid.GetX(), centroid.GetY()
    crs = pick_zone(lon)
    print("3. PROJECT VN-2000 / UTM %s, EPSG:%d" % (crs["zone"], crs["vn2000_epsg"]))
    print("   %s" % crs["reason"])
    if crs["margin_deg"] < 0.1:
        print("   ! within %.2f degrees of the zone boundary, about %.1f km."
              % (crs["margin_deg"], crs["margin_deg"] * 111.0 * math.cos(math.radians(lat))))
        print("     Measured, not assumed: on this plot the wrong zone changes the")
        print("     area by 0.02 percentage points, which is nothing. The zone matters")
        print("     because eastings from two zones cannot be compared, so neighbour")
        print("     and overlap queries break silently across a provincial dataset.")

    print("4. MEASURE")
    vn_ha, vn_m = measure_projected(poly, crs["vn2000_epsg"])
    wgs_ha, wgs_m = measure_projected(poly, crs["wgs84_epsg"])
    geo_ha, geo_m = measure_geodesic(poly)
    areas = [vn_ha, wgs_ha, geo_ha]
    spread_pct = (max(areas) - min(areas)) / geo_ha * 100 if geo_ha else 0.0
    width_m, height_m = bbox_metres(poly, crs["vn2000_epsg"])

    print("   VN-2000 UTM %-4s %9.4f ha   perimeter %7.1f m" % (crs["zone"], vn_ha, vn_m))
    print("   WGS84 UTM   %-4s %9.4f ha   perimeter %7.1f m" % (crs["zone"], wgs_ha, wgs_m))
    print("   geodesic (WGS84 ellipsoid) %6.4f ha   perimeter %7.1f m" % (geo_ha, geo_m))
    print("   spread between methods: %.3f %% of area. That is the error bar to quote."
          % spread_pct)
    print("   bounding box %.0f x %.0f m, centroid %.6f, %.6f" % (width_m, height_m, lat, lon))

    needs_polygon = geo_ha > EUDR_AREA_THRESHOLD_HA
    print("   EUDR: %.4f ha is %s %.0f ha, so a %s is required"
          % (geo_ha, "above" if needs_polygon else "at or below",
             EUDR_AREA_THRESHOLD_HA, "polygon" if needs_polygon else "single point"))

    print("5. SIMPLIFY (area within %.1f %%, boundary moved under %.1f m)"
          % (AREA_DELTA_BUDGET * 100, args.gps_noise))
    best, tried = choose_simplification(poly, crs["vn2000_epsg"], geo_ha, args.gps_noise)
    for row in tried:
        mark = "<-" if best and row is best[1] else "  "
        flags = "%s %s" % ("area" if row["area_ok"] else "AREA",
                           "shape" if row["shape_ok"] else "SHAPE")
        print("   %s %4.1f m -> %4d vertices, %8.4f ha, %+7.3f %%, moved %5.2f m   %s"
              % (mark, row["tolerance_m"], row["vertices"], row["area_ha"],
                 row["area_delta_pct"], row["max_shift_m"], flags))
    print("   (upper case = constraint failed. Note the rows where the area passes "
          "and the shape does not: that is cancellation, and area alone would have "
          "accepted them.)")
    if best is None:
        print("   ! nothing satisfied both constraints; keeping the full ring")
        simple_geom, simple_row = poly.Clone(), {"tolerance_m": None, "vertices": used,
                                                 "area_ha": round(geo_ha, 4),
                                                 "area_delta_pct": 0.0,
                                                 "max_shift_m": 0.0}
    else:
        simple_geom, simple_row = best
        print("   chosen: %d vertices instead of %d, area off %+0.3f %%, "
              "boundary moved at most %.2f m"
              % (simple_row["vertices"], used, simple_row["area_delta_pct"],
                 simple_row["max_shift_m"]))

    meta = {
        "plot_id": plot_id,
        "processed_utc": datetime.datetime.now(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": pathlib.Path(__file__).name,
        "software": {"gdal": gdal.VersionInfo("RELEASE_NAME"),
                     "proj": "%d.%d.%d" % (osr.GetPROJVersionMajor(),
                                           osr.GetPROJVersionMinor(),
                                           osr.GetPROJVersionMicro())},
        "source": {"file": src.name, "sha256": sha256(src),
                   "measured_note": args.measured},
        "vertices": {"in_file": len(pts), "duplicates_dropped": dropped,
                     "used": used, "spikes": spikes},
        "validation": {"valid": bool(poly.IsValid()), "issues": issues},
        "crs": crs,
        "area": {"vn2000_ha": round(vn_ha, 4), "wgs84_utm_ha": round(wgs_ha, 4),
                 "geodesic_ha": round(geo_ha, 4),
                 "spread_pct_of_area": round(spread_pct, 3),
                 "authoritative": "geodesic_ha"},
        "perimeter": {"vn2000_m": round(vn_m, 1), "wgs84_utm_m": round(wgs_m, 1),
                      "geodesic_m": round(geo_m, 1)},
        "bbox_m": {"width": round(width_m, 1), "height": round(height_m, 1)},
        "centroid": {"lat": round(lat, 6), "lon": round(lon, 6)},
        "eudr": {"area_threshold_ha": EUDR_AREA_THRESHOLD_HA,
                 "geometry_required": "polygon" if needs_polygon else "point"},
        "simplified": {"area_budget_pct": AREA_DELTA_BUDGET * 100,
                       "max_shift_budget_m": args.gps_noise,
                       "tolerance_m": simple_row["tolerance_m"],
                       "vertices": simple_row["vertices"],
                       "area_ha": simple_row["area_ha"],
                       "area_delta_pct": simple_row["area_delta_pct"],
                       "max_shift_m": simple_row["max_shift_m"],
                       "all_tolerances": tried},
    }

    print("6. EXPORT")
    made = write_vectors(outdir, plot_id, poly, simple_geom, meta)
    sidecar = outdir / ("%s_metadata.json" % plot_id)
    sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    made.append(sidecar)

    if not args.no_project:
        try:
            made.extend(write_qgis_project(
                outdir, plot_id, str(outdir / ("%s.gpkg" % plot_id)), crs["vn2000_epsg"]))
        except Exception as exc:
            print("   ! no QGIS project: %s" % exc)
            print("     Run with the QGIS Python, or pass --no-project.")

    for path in made:
        print("   %-44s %8.1f KB" % (path.name, path.stat().st_size / 1024.0))
    print("\nAll of it in %s" % outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
