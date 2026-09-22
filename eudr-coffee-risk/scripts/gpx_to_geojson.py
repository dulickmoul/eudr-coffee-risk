# -*- coding: utf-8 -*-
"""Turn a walked GPX boundary into an EUDR-ready GeoJSON polygon.

Smallholders map plots by walking the edge with a phone app, which exports GPX,
while every EUDR tool in this repo wants GeoJSON. This bridges the two and
reports the numbers that decide what the regulation requires of the plot.

Phone GPS logs a point every step, so a boundary arrives with hundreds of
vertices carrying roughly a metre of noise each. The simplified ring is written
alongside the full one: same shape within the tolerance, small enough to submit.

Area is computed on a local tangent plane using the WGS84 degree lengths at the
plot's own latitude. Over a few hundred metres that is accurate to well under
the GPS noise, so the binding uncertainty is the walk, not the projection.

Output goes to data/real/, which is gitignored. A walked boundary is personal
data about a real household and never belongs in a commit.

    python scripts/gpx_to_geojson.py data/real/Rsas_LamDong.gpx --id RSAS-01
"""

import argparse
import json
import math
import os
import re
import sys

EUDR_AREA_THRESHOLD_HA = 4.0          # above this a polygon is mandatory
DEFAULT_TOLERANCE_M = 1.5             # ~ phone GPS noise; below it we'd keep noise


def read_gpx(path):
    """Every <trkpt>/<rtept>/<wpt> lat-lon in file order."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    pts = [(float(la), float(lo)) for la, lo in re.findall(
        r'<(?:trkpt|rtept|wpt)[^>]*?lat="([-\d.]+)"[^>]*?lon="([-\d.]+)"', text)]
    if len(pts) < 4:
        sys.exit("Need at least 4 points to make a polygon; found %d." % len(pts))
    return pts


def degree_lengths(lat):
    """Metres per degree of latitude and longitude at this latitude (WGS84)."""
    p = math.radians(lat)
    m = (111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
         - 0.0023 * math.cos(6 * p))
    q = (111412.84 * math.cos(p) - 93.5 * math.cos(3 * p)
         + 0.118 * math.cos(5 * p))
    return m, q


def project(pts):
    """Local tangent plane in metres, origin at the centre of the bounding box."""
    lat0 = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0
    lon0 = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0
    m, q = degree_lengths(lat0)
    return [((lo - lon0) * q, (la - lat0) * m) for la, lo in pts], (lat0, lon0, m, q)


def ring_metrics(xy):
    """Shoelace area (m2), perimeter (m), and centroid, on a closed ring."""
    a = cx = cy = 0.0
    per = 0.0
    for i in range(len(xy) - 1):
        x0, y0 = xy[i]
        x1, y1 = xy[i + 1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        per += math.hypot(x1 - x0, y1 - y0)
    a /= 2.0
    if abs(a) < 1e-9:
        sys.exit("Degenerate ring: zero area.")
    return abs(a), per, (cx / (6 * a), cy / (6 * a))


def rdp(xy, tol):
    """Ramer-Douglas-Peucker on projected metres."""
    if len(xy) < 3:
        return list(xy)
    x0, y0 = xy[0]
    x1, y1 = xy[-1]
    dx, dy = x1 - x0, y1 - y0
    span = math.hypot(dx, dy)
    worst, idx = -1.0, 0
    for i in range(1, len(xy) - 1):
        px, py = xy[i]
        if span == 0:
            d = math.hypot(px - x0, py - y0)
        else:
            d = abs(dy * px - dx * py + x1 * y0 - y1 * x0) / span
        if d > worst:
            worst, idx = d, i
    if worst <= tol:
        return [xy[0], xy[-1]]
    return rdp(xy[:idx + 1], tol)[:-1] + rdp(xy[idx:], tol)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gpx")
    ap.add_argument("--id", default=None, help="plot_id written into the feature")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_M,
                    help="simplification tolerance in metres (default %.1f)"
                         % DEFAULT_TOLERANCE_M)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    pts = read_gpx(args.gpx)
    plot_id = args.id or os.path.splitext(os.path.basename(args.gpx))[0]

    closed = pts[0] == pts[-1]
    ring = list(pts) if closed else list(pts) + [pts[0]]

    xy, (lat0, lon0, mdeg, qdeg) = project(ring)
    area_m2, per_m, (ccx, ccy) = ring_metrics(xy)
    clat, clon = lat0 + ccy / mdeg, lon0 + ccx / qdeg
    area_ha = area_m2 / 10000.0

    gap_m = math.hypot(xy[0][0] - xy[-2][0], xy[0][1] - xy[-2][1])

    simp_xy = rdp(xy, args.tolerance)
    keep = set()
    for sx, sy in simp_xy:
        for i, (x, y) in enumerate(xy):
            if x == sx and y == sy:
                keep.add(i)
                break
    simp = [ring[i] for i in sorted(keep)]
    if simp[0] != simp[-1]:
        simp.append(simp[0])
    s_area, s_per, _ = ring_metrics(project(simp)[0])

    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    width = (max(lons) - min(lons)) * qdeg
    height = (max(lats) - min(lats)) * mdeg

    print("plot            : %s" % plot_id)
    print("points          : %d walked%s" % (len(pts), "" if closed else ", ring auto-closed"))
    print("closure gap     : %.1f m between first and last walked point" % gap_m)
    print("area            : %.4f ha  (%.0f m2)" % (area_ha, area_m2))
    print("perimeter       : %.0f m" % per_m)
    print("bounding box    : %.0f m x %.0f m" % (width, height))
    print("centroid        : %.6f, %.6f" % (clat, clon))
    print("simplified      : %d points at %.1f m, area %.4f ha (%+.2f%%), perimeter %.0f m"
          % (len(simp) - 1, args.tolerance, s_area / 10000.0,
             (s_area - area_m2) / area_m2 * 100.0, s_per))
    print("EUDR            : %.4f ha is %s the 4 ha threshold, so %s"
          % (area_ha,
             "over" if area_ha > EUDR_AREA_THRESHOLD_HA else "under",
             "a polygon is mandatory" if area_ha > EUDR_AREA_THRESHOLD_HA
             else "a single point would satisfy the regulation; the polygon is "
                  "better evidence and is kept"))

    out_dir = args.out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "real")
    os.makedirs(out_dir, exist_ok=True)

    written = []
    for suffix, coords in (("", ring), ("_simplified", simp)):
        fc = {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "properties": {"plot_id": plot_id,
                           "area_ha": round(area_ha, 4),
                           "source": "walked GPS boundary, %s" % os.path.basename(args.gpx),
                           "vertices": len(coords) - 1},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[lo, la] for la, lo in coords]]}}]}
        path = os.path.join(out_dir, "%s%s.geojson" % (plot_id, suffix))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fc, fh, ensure_ascii=False)
        written.append(path)
        print("wrote", path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
