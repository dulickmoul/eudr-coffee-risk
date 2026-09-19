# -*- coding: utf-8 -*-
"""Document-based legality assessment: the half of EUDR satellites cannot see.

EUDR requires products to be deforestation-free **and produced legally**.
Article 2(40) of Regulation (EU) 2023/1115 defines "relevant legislation" as
the law of the producing country covering eight areas. Remote sensing answers
none of (a), (d), (e), (f), (g) or (h) and only gestures at (b) and (c).
Those need documents: land certificates, contracts, labour records,
consultation minutes.

So this module is deliberately not clever. It produces an editable checklist,
one row per plot per area, that a compliance officer fills in from paper
records, and it rolls the result up per plot. The point is to make the gap
visible and trackable rather than let a clean satellite result masquerade as
compliance.

Scale of the problem in Vietnam, worth stating plainly: around 2.25 million
smallholders sit in the wood, coffee and rubber chains, and most either lack
a land use right certificate or have incomplete legal paperwork, held on
paper. Digitising this checklist is itself most of the work.

Not legal advice. The evidence hints below are the documents commonly used in
Vietnam, gathered from public guidance; a competent authority or the buyer's
legal team decides what actually satisfies each area.
"""

import csv
import os

REGULATION = "Regulation (EU) 2023/1115, Article 2(40)"

# Vietnamese legal frame the evidence hints refer to.
VN_LEGAL_FRAME = (
    "Luat Dat dai 2024 (Land Law), Luat Lam nghiep 2017 (Forestry Law), "
    "Bo luat Lao dong 2019 (Labour Code)"
)

STATUSES = (
    "not_started",       # nobody has looked yet
    "evidence_pending",  # requested from the farmer or cooperative
    "on_file",           # document held, not yet checked against the rule
    "verified",          # checked and satisfies the requirement
    "non_compliant",     # checked and does not satisfy it
    "not_applicable",    # area genuinely does not apply, with a reason
)

TERMINAL_OK = {"verified", "not_applicable"}


class Area:
    """One of the eight Article 2(40) areas."""

    __slots__ = ("code", "article", "title_en", "title_vi", "evidence", "note")

    def __init__(self, code, article, title_en, title_vi, evidence, note=""):
        self.code = code
        self.article = article
        self.title_en = title_en
        self.title_vi = title_vi
        self.evidence = evidence
        self.note = note

    def as_row(self):
        return {
            "area_code": self.code,
            "article": self.article,
            "requirement_en": self.title_en,
            "requirement_vi": self.title_vi,
            "typical_evidence_vn": "; ".join(self.evidence),
            "note": self.note,
        }


AREAS = [
    Area(
        "a_land_use_rights", "2(40)(a)",
        "Land use rights",
        "Quyen su dung dat",
        ["Giay chung nhan quyen su dung dat (so do)",
         "Hop dong giao khoan dat lam nghiep",
         "Hop dong thue dat",
         "Xac nhan cua UBND xa ve qua trinh su dung dat"],
        "The core document. Most smallholders lack a full certificate, so "
        "expect allocation contracts or commune confirmation instead.",
    ),
    Area(
        "b_environmental_protection", "2(40)(b)",
        "Environmental protection",
        "Bao ve moi truong",
        ["Nhat ky thuoc BVTV: chi dung thuoc trong danh muc, dung thoi gian cach ly",
         "Xu ly phu pham che bien (vo ca phe, nuoc thai)",
         "Khong xa thai ra nguon nuoc"],
        "The pesticide log this tool already produces is the main evidence.",
    ),
    Area(
        "c_forest_rules", "2(40)(c)",
        "Forest-related rules, including management and biodiversity",
        "Quy dinh ve rung, quan ly rung va da dang sinh hoc",
        ["Xac nhan lo dat khong thuoc rung dac dung / rung phong ho (3 loai rung)",
         "Quyet dinh chuyen muc dich su dung dat neu dat co nguon goc lam nghiep"],
        "The WDPA overlay in eudr_risk.legality is only a proxy. The official "
        "3-loai-rung zoning is the authority.",
    ),
    Area(
        "d_third_party_rights", "2(40)(d)",
        "Third parties' rights",
        "Quyen cua ben thu ba",
        ["Khong co tranh chap ranh gioi dang giai quyet",
         "Bien ban thong nhat ranh gioi voi ho lien ke",
         "Khong tranh chap voi cong dong"],
    ),
    Area(
        "e_labour_rights", "2(40)(e)",
        "Labour rights",
        "Quyen lao dong",
        ["Hop dong hoac thoa thuan voi nguoi lam thue, nguoi hai",
         "Khong su dung lao dong duoi 15 tuoi",
         "Tra cong dung thoa thuan, co bang ke",
         "Bao ho lao dong khi phun thuoc"],
        "Harvest labour is seasonal and often informal, so this is usually the "
        "weakest area in practice.",
    ),
    Area(
        "f_human_rights", "2(40)(f)",
        "Human rights protected under international law",
        "Quyen con nguoi theo luat quoc te",
        ["Co kenh tiep nhan va xu ly khieu nai",
         "Khong lao dong cuong buc"],
    ),
    Area(
        "g_fpic", "2(40)(g)",
        "Free, prior and informed consent (UNDRIP)",
        "Dong thuan tu nguyen, bao truoc, duoc thong tin (FPIC)",
        ["Bien ban tham van cong dong truoc khi thay doi su dung dat",
         "Ho so the hien su dong thuan cua cong dong lien quan"],
        "Live issue here, not theoretical: K'Ho, Ma and E De households farm "
        "coffee in Di Linh. Mark not_applicable only with a stated reason.",
    ),
    Area(
        "h_tax_trade_customs", "2(40)(h)",
        "Tax, anti-corruption, trade and customs regulations",
        "Thue, chong tham nhung, thuong mai va hai quan",
        ["Dang ky kinh doanh cua HTX hoac don vi thu mua neu thuoc dien",
         "Hoa don, chung tu mua ban",
         "Ke khai thue cua don vi thu mua"],
        "Mostly sits with the cooperative or collector, not the farmer.",
    ),
]

AREA_BY_CODE = {a.code: a for a in AREAS}

CHECKLIST_COLUMNS = [
    "plot_id", "area_code", "article", "requirement_vi", "requirement_en",
    "typical_evidence_vn", "status", "evidence_ref", "checked_date",
    "checked_by", "notes",
]


# --------------------------------------------------------------------------
# Building and reading the checklist
# --------------------------------------------------------------------------

def blank_rows(plot_ids, default_status="not_started"):
    """One row per plot per area, ready to be filled in."""
    if default_status not in STATUSES:
        raise ValueError(f"Unknown status {default_status!r}; use one of {STATUSES}")
    rows = []
    for plot_id in plot_ids:
        for area in AREAS:
            row = {c: "" for c in CHECKLIST_COLUMNS}
            row.update(area.as_row())
            row["plot_id"] = str(plot_id)
            row["status"] = default_status
            row["notes"] = area.note
            rows.append(row)
    return rows


def write_checklist(plot_ids, path, default_status="not_started"):
    """Write an editable CSV. Meant to be opened in Excel by a human."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = blank_rows(plot_ids, default_status=default_status)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CHECKLIST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CHECKLIST_COLUMNS})
    return path


def read_checklist(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    unknown = {r.get("status") for r in rows} - set(STATUSES) - {""}
    if unknown:
        raise ValueError(
            f"Unknown status value(s) {sorted(unknown)} in {path}; "
            f"allowed: {', '.join(STATUSES)}"
        )
    missing = {r.get("area_code") for r in rows} - set(AREA_BY_CODE)
    if missing - {""}:
        raise ValueError(f"Unknown area_code(s) {sorted(missing - {''})} in {path}")
    return rows


# --------------------------------------------------------------------------
# Rolling up
# --------------------------------------------------------------------------

def summarise_plot(rows):
    """Roll one plot's rows into a status, plus what is outstanding.

    A single non_compliant area blocks the plot: EUDR legality is
    conjunctive, not a score to average.
    """
    by_area = {r["area_code"]: (r.get("status") or "not_started") for r in rows}
    for area in AREAS:
        by_area.setdefault(area.code, "not_started")

    blocking = sorted(c for c, s in by_area.items() if s == "non_compliant")
    outstanding = sorted(c for c, s in by_area.items() if s not in TERMINAL_OK)

    if blocking:
        status = "blocked"
    elif not outstanding:
        status = "complete"
    elif all(by_area[c] in TERMINAL_OK | {"on_file"} for c in by_area):
        status = "documented"
    else:
        status = "incomplete"

    return {
        "legality_status": status,
        "legality_outstanding": len(outstanding),
        "legality_blocking": ";".join(blocking),
        "legality_areas_total": len(AREAS),
    }


def summarise(rows):
    """``{plot_id: summary}`` for every plot present in the rows."""
    grouped = {}
    for row in rows:
        grouped.setdefault(row["plot_id"], []).append(row)
    return {pid: summarise_plot(rs) for pid, rs in grouped.items()}


def eudr_readiness(deforestation_tier, legality_status):
    """Combine both halves. Ready requires both, which is the whole point.

    Returns ``(readiness, reason)``.
    """
    defo_ok = deforestation_tier == "low"
    legal_ok = legality_status == "complete"

    if defo_ok and legal_ok:
        return "ready", ""
    reasons = []
    if not defo_ok:
        reasons.append(f"deforestation risk {deforestation_tier}")
    if not legal_ok:
        reasons.append(f"legality {legality_status}")
    return "not_ready", "; ".join(reasons)


def merge_into(table, rows):
    """Attach legality columns and EUDR readiness to a scored risk table.

    Plots with no checklist rows are reported as ``not_started`` rather than
    silently passing, because an unassessed plot is not a compliant plot.
    """
    summaries = summarise(rows) if rows else {}
    out = table.copy()

    def pick(plot_id, key, default):
        return summaries.get(str(plot_id), {}).get(key, default)

    out["legality_status"] = [
        pick(p, "legality_status", "not_started") for p in out["plot_id"]
    ]
    out["legality_outstanding"] = [
        pick(p, "legality_outstanding", len(AREAS)) for p in out["plot_id"]
    ]
    out["legality_blocking"] = [
        pick(p, "legality_blocking", "") for p in out["plot_id"]
    ]

    combined = [
        eudr_readiness(tier, status)
        for tier, status in zip(out["risk_tier"], out["legality_status"])
    ]
    out["eudr_readiness"] = [c[0] for c in combined]
    out["not_ready_reason"] = [c[1] for c in combined]
    return out
