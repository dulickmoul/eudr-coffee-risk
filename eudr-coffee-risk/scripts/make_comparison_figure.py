# -*- coding: utf-8 -*-
"""Build the side-by-side figure that explains the whole screening problem.

Two plots on the Lam Dong forest frontier each lost about half their area
after the EUDR cutoff. One is compliant for coffee, the other is not. What
separates them is not how much was lost but whether the ground was forest at
the end of 2020.

Locations are deliberately anonymised to "Plot A" and "Plot B". The high-risk
verdict is a screening signal on an unvalidated tool, not a finding of
illegality, so publishing coordinates would imply an accusation the evidence
does not support.

Numbers come from tests/fixtures/whisp_frontier_sample.csv, a real Whisp
response. Run:

    python scripts/make_comparison_figure.py
    # then render to PNG for sharing:
    chrome --headless --screenshot=comparison.png --window-size=1200,820 comparison.html
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

FIXTURE = os.path.join(REPO, "tests", "fixtures", "whisp_frontier_sample.csv")

# (fixture id, anonymous label)
PLOTS = [("TADUNG-r7c3", "Thửa A"), ("SONDIEN-r6c4", "Thửa B")]

GREEN, RED, GREY, DARK = "#2e7d32", "#c62828", "#90a4ae", "#1b3a1e"
AMBER = "#ef6c00"


def load():
    csv.field_size_limit(10 ** 7)
    with open(FIXTURE, encoding="utf-8-sig") as fh:
        rows = {r["external_id"]: r for r in csv.DictReader(fh)}
    out = []
    for pid, label in PLOTS:
        r = rows[pid]
        area = float(r["Area"])
        out.append({
            "label": label,
            "area": area,
            "loss_after": float(r["GFC_loss_after_2020"]),
            "forest_2020": float(r["EUFO_2020"]),
            "forest_fdap": float(r["Forest_FDaP"]),
            "cleared_before": float(r["TMF_def_before_2020"]),
            "coffee": float(r["Coffee_FDaP"]),
            "verdict": r["risk_pcrop"].strip().lower(),
        })
    return out


def bar(x, y, w, h, frac, colour, track="#e0e0e0"):
    """A horizontal proportion bar."""
    fill = max(0.0, min(1.0, frac)) * w
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h/2:.0f}" fill="{track}"/>'
        f'<rect x="{x}" y="{y}" width="{fill:.1f}" height="{h}" rx="{h/2:.0f}" fill="{colour}"/>'
    )


def panel(p, x0):
    """One plot's column."""
    s = []
    w = 430
    verdict_colour = RED if p["verdict"] == "high" else GREEN
    verdict_text = "RỦI RO CAO" if p["verdict"] == "high" else "RỦI RO THẤP"

    s.append(f'<text x="{x0}" y="150" font-size="27" font-weight="bold" fill="{DARK}">'
             f'{p["label"]}</text>')
    s.append(f'<text x="{x0}" y="180" font-size="16" fill="#546e7a">'
             f'{p["area"]:.2f} ha</text>')

    rows = [
        ("Mất cây sau mốc 2020",
         p["loss_after"] / p["area"], RED,
         f'{p["loss_after"]:.2f} ha ({p["loss_after"]/p["area"]*100:.1f}%)'),
        ("Là rừng năm 2020",
         p["forest_fdap"] / p["area"], GREEN,
         f'{p["forest_fdap"]:.2f} ha ({p["forest_fdap"]/p["area"]*100:.0f}%)'),
        ("Đã bị phá TRƯỚC 2020",
         p["cleared_before"] / p["area"], AMBER,
         f'{p["cleared_before"]:.2f} ha ({p["cleared_before"]/p["area"]*100:.0f}%)'),
    ]
    y = 228
    for label, frac, colour, value in rows:
        s.append(f'<text x="{x0}" y="{y}" font-size="16" fill="#37474f">{label}</text>')
        # Value on the label line, not on the bar: a bar at 98% or 100% would
        # otherwise paint over its own number.
        s.append(f'<text x="{x0 + w}" y="{y}" font-size="16" font-weight="bold" '
                 f'fill="{colour}" text-anchor="end">{value}</text>')
        s.append(bar(x0, y + 12, w, 20, frac, colour))
        y += 74

    s.append(f'<rect x="{x0}" y="{y + 10}" width="{w}" height="58" rx="8" '
             f'fill="{verdict_colour}" fill-opacity="0.12" stroke="{verdict_colour}" '
             f'stroke-width="2"/>')
    s.append(f'<text x="{x0 + w/2}" y="{y + 47}" font-size="24" font-weight="bold" '
             f'fill="{verdict_colour}" text-anchor="middle">{verdict_text}</text>')
    return "".join(s)


def build(plots):
    a, b = plots
    H = 700
    svg = [
        f'<svg viewBox="0 0 1200 {H}" xmlns="http://www.w3.org/2000/svg" '
        'font-family="Segoe UI, DejaVu Sans, Arial, sans-serif">',
        f'<rect width="1200" height="{H}" fill="#ffffff"/>',
        f'<text x="60" y="72" font-size="34" font-weight="bold" fill="{DARK}">'
        'Cùng mất khoảng một nửa diện tích sau 2020.</text>',
        f'<text x="60" y="112" font-size="27" fill="{GREY}">'
        'Một thửa tuân thủ EUDR, một thửa không. Vì sao?</text>',
        panel(a, 60),
        panel(b, 680),
        '<line x1="600" y1="140" x2="600" y2="510" stroke="#cfd8dc" stroke-width="2"/>',
    ]
    box_y = 548
    svg.append(f'<rect x="60" y="{box_y}" width="1080" height="98" rx="10" '
               f'fill="#f1f8e9" stroke="{GREEN}" stroke-width="2"/>')
    svg.append(f'<text x="80" y="{box_y + 36}" font-size="21" font-weight="bold" fill="{DARK}">'
               'Điều quyết định không phải mất bao nhiêu, mà là năm 2020 đó có phải rừng hay không.</text>')
    svg.append(f'<text x="80" y="{box_y + 70}" font-size="18" fill="#37474f">'
               'Thửa A đã bị chuyển đổi từ trước mốc, nên mất cây sau 2020 là trồng lại vườn cũ, '
               'không phải phá rừng.</text>')
    svg.append(f'<text x="60" y="{box_y + 128}" font-size="14" fill="{GREY}">'
               'Nguồn: Open Foris Whisp (FAO), dữ liệu mở. Vị trí đã ẩn danh. '
               'Phán quyết sàng lọc, chưa phải kết luận pháp lý.</text>')
    svg.append('</svg>')
    return "".join(svg)


def main():
    plots = load()
    svg = build(plots)
    svg_path = os.path.join(REPO, "out", "comparison.svg")
    html_path = os.path.join(REPO, "out", "comparison.html")
    os.makedirs(os.path.dirname(svg_path), exist_ok=True)

    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<style>html,body{margin:0;padding:0;background:#fff}</style></head>'
            f'<body>{svg}</body></html>'
        )

    for p in plots:
        print(f"{p['label']}: {p['area']:.2f} ha, lost {p['loss_after']:.3f} ha "
              f"({p['loss_after']/p['area']*100:.1f}%), forest2020 "
              f"{p['forest_fdap']:.3f} ha, cleared before "
              f"{p['cleared_before']:.3f} ha -> {p['verdict']}")
    print(f"\nwrote {svg_path}\nwrote {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
