# -*- coding: utf-8 -*-
"""Eyeball one plot against the EUDR cutoff: imagery before and after, boundary drawn.

Whisp gives a verdict from layers. This gives the picture a person can argue
with, which is what a land purchase or a farmer conversation actually turns on.

Esri World Imagery Wayback is public and needs no key. The caveat is
load-bearing and stays in the image: a Wayback date is when Esri published the
mosaic, not when the pixel was taken. So this shows what the ground looked like
around the cutoff, and is evidence to bring to a decision, not proof of a date.

The output traces a real household's boundary, so it is written to out/ (which
is gitignored) and is not for publishing.

    python scripts/plot_imagery_check.py data/real/Rsas_LamDong.geojson
"""

import argparse
import io
import json
import math
import os
import re
import sys

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
UA = {"User-Agent": "eudr-coffee-risk/0.1"}
ZOOMS = (18, 17, 16)   # deepest first; older releases often lack the finest level
TILES = 5
PANEL_PX = 660
CUTOFF_YEAR = 2020     # EUDR: no deforestation after 31 Dec 2020

DARK, GREY, YELLOW = (20, 58, 30), (96, 115, 125), (255, 232, 0)


def deg2num(lat, lon, z):
    n = 2.0 ** z
    return ((lon + 180.0) / 360.0 * n,
            (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)


def releases():
    cfg = requests.get(CONFIG, timeout=120, headers=UA).json()
    out = []
    for item in cfg.values():
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", item.get("itemTitle", ""))
        url = item.get("itemURL") or ""
        if m and url:
            out.append({"date": m.group(0), "year": int(m.group(1)), "url": url})
    out.sort(key=lambda r: r["date"])
    if not out:
        sys.exit("No dated Wayback releases found.")
    return out


def tile(url_tmpl, z, x, y):
    url = (url_tmpl.replace("{level}", str(z)).replace("{row}", str(y))
           .replace("{col}", str(x)).replace("{z}", str(z))
           .replace("{y}", str(y)).replace("{x}", str(x)))
    r = requests.get(url, timeout=90, headers=UA)
    if r.status_code != 200 or len(r.content) < 500:
        raise RuntimeError("tile %d/%d/%d: HTTP %s" % (z, x, y, r.status_code))
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def sharpness(img):
    """Wayback quality for one spot swings a lot, and a blurry 'before' panel
    makes unchanged ground look transformed. Pick on edge energy, not on date."""
    return ImageStat.Stat(img.convert("L").filter(ImageFilter.FIND_EDGES)).stddev[0]


def has_tile(rel, z, lat, lon):
    xt, yt = deg2num(lat, lon, z)
    try:
        tile(rel["url"], z, int(xt), int(yt))
        return True
    except Exception:
        return False


def choose_zoom(pre, post, lat, lon):
    """Deepest zoom both shortlists actually carry here.

    A Wayback release only serves the levels it has imagery for, so an older
    release over a rural area 404s at z18 while serving z17 fine. Probing beats
    hardcoding, and using one zoom for both panels keeps them comparable.
    """
    for z in ZOOMS:
        if any(has_tile(r, z, lat, lon) for r in pre) and            any(has_tile(r, z, lat, lon) for r in post):
            print("zoom            : %d" % z)
            return z
    sys.exit("No zoom level serves both a pre-cutoff and a recent release here.")


def pick(cands, z, lat, lon, label):
    xt, yt = deg2num(lat, lon, z)
    scored = []
    for rel in cands:
        try:
            scored.append((sharpness(tile(rel["url"], z, int(xt), int(yt))), rel))
            print("    %-6s %s  sharpness %.1f" % (label, rel["date"], scored[-1][0]))
        except Exception as exc:
            print("    %-6s %s  unavailable (%s)" % (label, rel["date"], exc))
    if not scored:
        sys.exit("No usable %s release here." % label)
    best = max(scored, key=lambda t: t[0])[1]
    print("    -> %s: %s" % (label, best["date"]))
    return best


def panel(rel, ring, z, lat, lon, frame_m):
    xt, yt = deg2num(lat, lon, z)
    x0, y0 = int(xt) - TILES // 2, int(yt) - TILES // 2
    big = Image.new("RGB", (256 * TILES, 256 * TILES))
    for dx in range(TILES):
        for dy in range(TILES):
            big.paste(tile(rel["url"], z, x0 + dx, y0 + dy), (dx * 256, dy * 256))

    def px(la, lo):
        a, b = deg2num(la, lo, z)
        return (a - x0) * 256, (b - y0) * 256

    cx, cy = px(lat, lon)
    mpp = 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)
    half = (frame_m / mpp) / 2
    left, top = int(cx - half), int(cy - half)
    right, bottom = left + int(2 * half), top + int(2 * half)
    if left < 0 or top < 0 or right > big.width or bottom > big.height:
        sys.exit("Crop ran off the mosaic; raise TILES.")

    crop = big.crop((left, top, right, bottom)).resize((PANEL_PX, PANEL_PX), Image.LANCZOS)
    scale = PANEL_PX / float(right - left)
    poly = [((px(la, lo)[0] - left) * scale, (px(la, lo)[1] - top) * scale)
            for la, lo in ring]
    d = ImageDraw.Draw(crop)
    d.line(poly + [poly[0]], fill=(0, 0, 0), width=5)
    d.line(poly + [poly[0]], fill=YELLOW, width=3)
    return crop


def font(size, bold=False):
    names = (("seguisb.ttf", "segoeuib.ttf") if bold else ("segoeui.ttf",)) + \
            ("arial.ttf", "DejaVuSans.ttf")
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--frame", type=float, default=260.0,
                    help="ground width of each panel in metres")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.geojson, encoding="utf-8") as fh:
        fc = json.load(fh)
    feat = fc["features"][0] if fc.get("type") == "FeatureCollection" else fc
    ring = [(la, lo) for lo, la in feat["geometry"]["coordinates"][0]]
    props = feat.get("properties", {})
    plot_id = props.get("plot_id", os.path.basename(args.geojson))
    area_ha = props.get("area_ha")
    lat = sum(p[0] for p in ring) / len(ring)
    lon = sum(p[1] for p in ring) / len(ring)

    rels = releases()
    print("%d releases, %s .. %s" % (len(rels), rels[0]["date"], rels[-1]["date"]))
    pre = [r for r in rels if r["year"] <= CUTOFF_YEAR][-8:]
    post = rels[-6:]
    z = choose_zoom(pre, post, lat, lon)
    before = pick(pre, z, lat, lon, "before")
    after = pick(post, z, lat, lon, "after")

    panels = [(before, panel(before, ring, z, lat, lon, args.frame)),
              (after, panel(after, ring, z, lat, lon, args.frame))]

    pad, gap, head, foot = 20, 16, 104, 66
    W = pad * 2 + PANEL_PX * 2 + gap
    canvas = Image.new("RGB", (W, head + PANEL_PX + foot), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    d.text((pad, 18), "Lô %s: hiện trạng quanh mốc EUDR 31/12/2020" % plot_id,
           fill=DARK, font=font(32, True))
    sub = "Ranh giới đi bộ đo bằng GPS%s. Khung vàng là ranh thật, không phải ô ước lượng." % (
        ", %.4f ha" % area_ha if area_ha else "")
    d.text((pad, 62), sub, fill=GREY, font=font(19))

    for i, (rel, img) in enumerate(panels):
        x = pad + i * (PANEL_PX + gap)
        canvas.paste(img, (x, head))
        d.rectangle([x, head, x + PANEL_PX - 1, head + PANEL_PX - 1],
                    outline=(205, 210, 212), width=1)
        d.text((x + 6, head + PANEL_PX + 8),
               "%s bản phát hành %s" % ("TRƯỚC MỐC" if i == 0 else "GẦN NHẤT", rel["date"]),
               fill=(40, 40, 40), font=font(21, True))

    d.text((pad, head + PANEL_PX + 40),
           "Nguồn: Esri World Imagery Wayback. Ngày ghi là ngày phát hành bản đồ, "
           "không phải ngày chụp ảnh. Đây là bằng chứng để đối chiếu, chưa phải kết luận.",
           fill=GREY, font=font(15))

    out = args.out or os.path.join(REPO, "out", "%s_cutoff_check.jpg" % plot_id)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    canvas.save(out, quality=93)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
