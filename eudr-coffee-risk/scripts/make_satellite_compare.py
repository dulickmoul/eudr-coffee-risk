# -*- coding: utf-8 -*-
"""Real before/after imagery of the two comparison plots.

Esri World Imagery Wayback: high resolution, public, no authentication, and
releases running to the present. Fetches a release near the EUDR cutoff and
the latest one, crops both to the same frame, and marks the plot.

One caveat is load-bearing and stays in the figure: a Wayback **release**
date is when Esri published the mosaic, not when the pixel was acquired. The
figure therefore says "release" and never "photographed on". For a date-certain
comparison use Sentinel-2 in the Copernicus Browser with explicit dates.

The two cells are machine-generated probe squares on a regular lattice, not
anyone's parcel, and the alerts underneath them are open data that anyone can
check on Global Forest Watch. So the figure says what they are rather than
claiming an anonymity the committed coordinates would not honour. A high
verdict remains a screening signal from a tool whose accuracy has not been
measured against ground truth, never a finding of illegality.

    python scripts/make_satellite_compare.py
"""

import io
import math
import os
import re
import sys

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "out")

CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
UA = {"User-Agent": "eudr-coffee-risk/0.1 (+github)"}
Z = 17
SIZE = 0.002          # plot edge in degrees, about 223 m
TILES = 5             # mosaic size, generous so the crop never runs off it
FRAME_M = 750         # ground width of each panel
PANEL_PX = 620

PLOTS = [
    {"key": "B", "lat": 11.4950, "lon": 108.0510,
     "title": "Ô B  ·  RỦI RO CAO",
     "note": "Rừng nguyên vẹn đến 2024. Mất 2,78 ha sau mốc, "
             "cảnh báo radar tiếp tục sang 2025 và 2026."},
    {"key": "A", "lat": 11.9370, "lon": 107.8890,
     "title": "Ô A  ·  RỦI RO THẤP",
     "note": "Đã bị chuyển đổi từ 2001, 2002 và 2014, trước mốc EUDR. "
             "Mất cây sau 2020 là canh tác lại trên đất đã chuyển đổi."},
]

GREEN, RED, DARK, GREY = (46, 125, 50), (198, 40, 40), (20, 58, 30), (96, 115, 125)

FOOTER = (
    "Nguồn ảnh: Esri World Imagery Wayback. Ngày ghi là ngày phát hành bản đồ, "
    "không phải ngày chụp ảnh. Phán quyết sàng lọc, chưa phải kết luận pháp lý.",
    "Ô vàng là ô lưới do máy sinh, không phải ranh vườn của hộ nào. "
    "Cảnh báo gốc là dữ liệu mở, ai cũng kiểm tra được trên Global Forest Watch.",
)


def deg2num(lat, lon, z):
    n = 2.0 ** z
    return ((lon + 180.0) / 360.0 * n,
            (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)


def releases():
    cfg = requests.get(CONFIG, timeout=120, headers=UA).json()
    out = []
    for item in cfg.values():
        title = item.get("itemTitle", "")
        url = item.get("itemURL") or ""
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", title)
        if m and url:
            out.append({"date": m.group(0), "year": int(m.group(1)), "url": url})
    out.sort(key=lambda r: r["date"])
    if not out:
        raise RuntimeError("No dated Wayback releases found.")
    return out


def tile(url_tmpl, z, x, y):
    url = (url_tmpl.replace("{level}", str(z)).replace("{row}", str(y))
           .replace("{col}", str(x)).replace("{z}", str(z))
           .replace("{y}", str(y)).replace("{x}", str(x)))
    r = requests.get(url, timeout=90, headers=UA)
    if r.status_code != 200 or len(r.content) < 500:
        raise RuntimeError(f"tile {z}/{x}/{y}: HTTP {r.status_code}")
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def sharpness(img):
    """Edge energy. Wayback imagery for a given spot varies wildly in quality,
    and a blurry 'before' panel makes an unchanged plot look transformed."""
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).stddev[0]


def pick_release(candidates, lat, lon, label):
    """Choose the sharpest release from a shortlist, by fetching one tile."""
    xt, yt = deg2num(lat, lon, Z)
    x, y = int(xt), int(yt)
    scored = []
    for rel in candidates:
        try:
            s = sharpness(tile(rel["url"], Z, x, y))
            scored.append((s, rel))
            print(f"    {label} {rel['date']}: sharpness {s:.1f}")
        except Exception as exc:
            print(f"    {label} {rel['date']}: unavailable ({exc})")
    if not scored:
        raise RuntimeError(f"No usable {label} release for this location.")
    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0][1]
    print(f"    -> {label}: {best['date']}")
    return best


def panel(rel, lat, lon):
    xt, yt = deg2num(lat, lon, Z)
    # Centre the mosaic on the plot so the crop always has margin.
    x0, y0 = int(xt) - TILES // 2, int(yt) - TILES // 2
    big = Image.new("RGB", (256 * TILES, 256 * TILES))
    for dx in range(TILES):
        for dy in range(TILES):
            big.paste(tile(rel["url"], Z, x0 + dx, y0 + dy), (dx * 256, dy * 256))

    def px(la, lo):
        a, b = deg2num(la, lo, Z)
        return (a - x0) * 256, (b - y0) * 256

    sw, ne = px(lat, lon), px(lat + SIZE, lon + SIZE)
    cx, cy = (sw[0] + ne[0]) / 2, (sw[1] + ne[1]) / 2
    mpp = 156543.03392 * math.cos(math.radians(lat)) / (2 ** Z)
    half = (FRAME_M / mpp) / 2

    left, top = int(cx - half), int(cy - half)
    right, bottom = left + int(2 * half), top + int(2 * half)
    if left < 0 or top < 0 or right > big.width or bottom > big.height:
        raise RuntimeError("Crop ran off the mosaic; raise TILES.")

    crop = big.crop((left, top, right, bottom)).resize(
        (PANEL_PX, PANEL_PX), Image.LANCZOS)
    scale = PANEL_PX / (right - left)
    ImageDraw.Draw(crop).rectangle(
        [(sw[0] - left) * scale, (ne[1] - top) * scale,
         (ne[0] - left) * scale, (sw[1] - top) * scale],
        outline=(255, 232, 0), width=3)
    return crop


def font(size, bold=False):
    for name in (("seguisb.ttf", "segoeuib.ttf") if bold else ("segoeui.ttf",)) + \
                ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    rels = releases()
    print(f"{len(rels)} releases, {rels[0]['date']} .. {rels[-1]['date']}")

    # Shortlists: releases published before the cutoff year ends, and the
    # most recent ones. Sharpness picks within each, per plot.
    pre = [r for r in rels if r["year"] <= 2020][-8:]
    post = rels[-6:]

    rows = []
    for spec in PLOTS:
        print(f"plot {spec['key']}:")
        try:
            before = pick_release(pre, spec["lat"], spec["lon"], "before")
            after = pick_release(post, spec["lat"], spec["lon"], "after")
            panels = [(before, panel(before, spec["lat"], spec["lon"])),
                      (after, panel(after, spec["lat"], spec["lon"]))]
        except Exception as exc:
            sys.exit(f"plot {spec['key']} failed: {exc}")
        rows.append((spec, panels))

    pad, gap = 20, 16
    head, rowhead, rownote, rowfoot = 104, 34, 26, 30
    W = pad * 2 + PANEL_PX * 2 + gap
    H = head + len(rows) * (rowhead + rownote + PANEL_PX + rowfoot + 18) + 60
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(canvas)

    d.text((pad, 18), "Cùng mất khoảng một nửa diện tích sau 2020",
           fill=DARK, font=font(34, True))
    d.text((pad, 62), "Một ô tuân thủ EUDR, một ô không. Khung vàng là ô thăm "
                      "dò khoảng 5 ha, không phải ranh vườn của hộ nào.",
           fill=GREY, font=font(20))

    y = head
    for spec, panels in rows:
        colour = RED if spec["key"] == "B" else GREEN
        d.text((pad, y), spec["title"], fill=colour, font=font(25, True))
        y += rowhead
        d.text((pad, y), spec["note"], fill=(55, 71, 79), font=font(18))
        y += rownote
        for i, (rel, img) in enumerate(panels):
            x = pad + i * (PANEL_PX + gap)
            canvas.paste(img, (x, y))
            d.rectangle([x, y, x + PANEL_PX - 1, y + PANEL_PX - 1],
                        outline=(205, 210, 212), width=1)
            d.text((x + 6, y + PANEL_PX + 7),
                   f"bản phát hành {rel['date']}", fill=(40, 40, 40),
                   font=font(20, True))
        y += PANEL_PX + rowfoot + 18

    # Two lines, and measured: a footer that runs off the canvas silently
    # deletes exactly the caveat it exists to carry.
    small = font(15)
    for i, line in enumerate(FOOTER):
        if d.textlength(line, font=small) > W - 2 * pad:
            raise RuntimeError("Footer line %d does not fit the canvas." % (i + 1))
        d.text((pad, H - 52 + i * 21), line, fill=GREY, font=small)

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "satellite_before_after.jpg")
    canvas.save(path, quality=92)
    print("wrote", path)


if __name__ == "__main__":
    main()
