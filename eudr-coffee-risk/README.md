# EUDR Coffee Risk Analytics

Plot boundaries in, per-plot deforestation risk and a due-diligence bundle out.

Built for robusta smallholders around **Di Linh, Lâm Đồng** (Vietnam's largest
robusta area, ~44,000 ha, roughly 80% of local agricultural land), but nothing
is region-locked beyond [`eudr_risk/config.py`](eudr_risk/config.py).

The practical problem this solves: a buyer working with ~16,000 farmers cannot
send a field team to every farm before the EUDR deadline. This ranks plots so
the team visits the handful that actually need checking.

**Cost: $0.** Every dataset is an open Earth Engine asset.

---

## What EUDR requires (and what this does about it)

Regulation (EU) 2023/1115. The relevant facts, verified September 2026:

| Requirement | Detail | Handled here |
|---|---|---|
| Deforestation-free | No production on land deforested after **31 Dec 2020** | JRC GFC2020 baseline + Hansen post-cutoff loss + RADD alerts |
| Geolocation | Polygon for plots **over 4 ha**; a single point allowed for **4 ha or less** | `geometry.add_area_and_rule` |
| Legal production | Compliant with the producer country's own law | WDPA overlay, plus a hook for Vietnam's official forest zoning |
| Records | Keep for **5 years** | Timestamped export bundle |
| Application dates | **30 Dec 2026** (large/medium operators), **30 Jun 2027** (small/micro) | — |
| Country benchmark | **Vietnam = low risk**, Reg (EU) 2025/1093 (from 22 May 2025) | Recorded in the DDS metadata |

Two things worth being precise about, because they are easy to get wrong:

- **Low risk is not a pass.** Low risk means simplified due diligence and a 1%
  operator check rate, not exemption. The deforestation-free obligation stands.
  Vietnam also processes material from higher-risk origins, so **circumvention
  risk** still has to be assessed at the supply-chain level, not the plot level.
- **Hansen is not the EUDR baseline.** Hansen answers "did tree cover
  disappear, and when". The EUDR question is "was this forest at the cutoff
  date". That is what the JRC GFC2020 layer is for, which is why the default
  (`strict_jrc=True`) intersects the two.

---

## Quickstart

You need a free (non-commercial) Earth Engine account and a Cloud project:
sign up at <https://earthengine.google.com>.

```bash
pip install -r requirements.txt
earthengine authenticate          # once per machine
export GEE_PROJECT=my-gee-project # Windows: set GEE_PROJECT=...
python scripts/run_pipeline.py
```

That runs the three synthetic Di Linh plots and writes `out/`. To use real data:

```bash
python scripts/run_pipeline.py \
  --plots data/real/lamdong_plots.geojson \
  --harvest-year 2026 \
  --legality projects/my-gee-project/assets/lamdong_protection_forest \
  --out out/2026
```

Prefer a map? Open [`notebooks/phase2_risk.ipynb`](notebooks/phase2_risk.ipynb)
(works in Colab, where auth is one click).

Input requirement: a GeoJSON `FeatureCollection` where every feature has a
`plot_id` property.

## Tests

The scoring and export layer is pure pandas/stdlib, so it runs with no Earth
Engine credentials and no network:

```bash
pip install pandas
python tests/test_scoring.py
```

40 checks covering the tier rules, boolean coercion from `ee_to_df`, missing
columns, empty input, and the whole export bundle. Run it before you touch
`config.DEFAULT_WEIGHTS` or the tier thresholds.

---

## Outputs

| File | Contents |
|---|---|
| `risk_table.csv` | One row per plot: area, EUDR geometry rule, loss %, alert area, legality flags, score, tier |
| `dds.geojson` | Geolocation + risk evidence in a DDS-oriented structure, with data provenance |
| `summary.json` | Portfolio counts: plots, area, tier breakdown, % clean |
| `field_checklist.csv` | Plots to visit, highest risk first, with blank columns for findings |

### Risk tiers

Rule-based and deliberately boring (see `scoring.assign_tier`):

- **high** — intersects restricted land, or loss > 0.5% of the plot, or > 0.05 ha of recent alerts
- **standard** — any post-cutoff loss or alert, or > 25% forest remaining within 1 km
- **low** — no remote-sensing evidence of post-2020 deforestation

`risk_score` (0–100) is a weighted sum used only to *order* the worklist.
Weights live in `config.DEFAULT_WEIGHTS`. Change them if you like, but write
down why: this feeds a compliance decision.

---

## Layout

```
eudr_risk/
  config.py     EUDR constants, asset ids, weights  <- tune here
  geometry.py   area + the >4 ha polygon rule
  forest.py     JRC 2020 baseline, Hansen post-cutoff loss
  alerts.py     RADD radar alerts (near-real-time)
  legality.py   WDPA + hook for Vietnam forest zoning
  pipeline.py   zonal stats -> one row per plot
  scoring.py    score, tier, draft conclusion (pandas, no EE)
  dds.py        CSV / DDS GeoJSON / summary / field checklist
notebooks/
  phase1_mvp.ipynb    original MVP: Hansen loss on a map
  phase2_risk.ipynb   full pipeline + map + export
scripts/run_pipeline.py   CLI
data/plots_sample.geojson synthetic demo plots
```

Earth Engine work is confined to `pipeline.py` and the layer modules; scoring
is plain pandas, so weights can be re-tuned without re-running any zonal
statistics.

## Data sources

| Layer | Asset | Notes |
|---|---|---|
| Forest 2020 baseline | `JRC/GFC2020/V3` | Image, band `Map`, `1` = forest, 10 m. V3 dated 28 Nov 2025 |
| Tree-cover loss | `UMD/hansen/global_forest_change_2024_v1_12` | Annual; `lossyear` is years since 2000 |
| NRT alerts | `projects/radar-wur/raddalert/v1` | Sentinel-1 radar, 10 m; filter `layer`/`geography` |
| Protected areas | `WCMC/WDPA/current/polygons` | Legality proxy only |

Asset versions move. `notebooks/phase2_risk.ipynb` Step 0 prints the live band
names so you can catch a rename before it corrupts a run. RADD band naming in
particular has shifted between releases; if the date band is not `Date`, set
`config.RADD_DATE_BAND`.

---

## Scope and disclaimer

This is **decision-support tooling**, not a compliance determination.

- It does **not** file a Due Diligence Statement. The operator placing product
  on the EU market files the official DDS through the EU Information System
  (TRACES). `dds.geojson` is the evidence you hand to whoever does that.
- Satellite evidence cannot establish legal land tenure. A clean plot here can
  still be non-compliant on legality grounds.
- "No remote-sensing evidence" is not the same as "negligible risk". A human
  closes that out, with the farm visit and the land documents.
- Do not commit real farmer plot boundaries: they are personal data. The
  `.gitignore` blocks `data/real/` and `data/*_plots.geojson`.

## Roadmap

Phase 1 (done) Hansen loss + map · Phase 2 (this release) JRC baseline, RADD
alerts, legality overlay, risk scoring, DDS export.

Phase 3, open: Vietnam 3-loại-rừng zoning layer; join to the national EUDR
cultivation-zone database for official traceability codes; lot-level chain of
custody; ground-truth validation to tune the weights; scheduled monthly runs
that alert on change.

Companion to the Vietnamese agroforestry handbook in the `Agro_Project`
folder, which covers the agronomy this tooling monitors.
