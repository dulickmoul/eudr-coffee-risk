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

## Two data backends, one output

The geospatial analysis can come from either source. Scoring and the export
bundle are identical downstream, so you can start with Whisp and move to
Earth Engine later without touching anything else.

| | **A. Whisp (recommended to start)** | **B. Your own Earth Engine** |
|---|---|---|
| Needs | An API key | A registered GEE Cloud project |
| Analysis runs | On FAO's servers | In your EE project |
| Method | "Convergence of evidence" over many open datasets | JRC GFC2020 + Hansen + RADD, computed here |
| Good for | Getting answers today; the sector-standard tool | Full control, custom layers, your own weights |
| Entry point | `scripts/run_whisp.py` | `scripts/run_pipeline.py` |

Note: the `openforis-whisp` PyPI package is **not** a way around Earth Engine,
it needs a registered GEE project too. Only the hosted API avoids that.

### A. Whisp quickstart

Get a key: register or sign in at <https://whisp.openforis.org/login>, open
your account page and generate one. It is free. If you sign in through SSO,
note that the Keycloak access token is **not** accepted by the API, you need
the generated key.

Licensing is worth comparing: Whisp is MIT and explicitly permits commercial
use, where Earth Engine's free tier is noncommercial only. At scale the shared
public API's rate limits still apply, so coordinate with FAO or self-host.

Then:

```bash
pip install pandas requests
```
```bash
set WHISP_API_KEY=your-key
```
```bash
python scripts/run_whisp.py --check
```

`--check` prints the live service limits and needs no key. Then, on any new
dataset, look at the real column names before trusting numbers:

```bash
python scripts/run_whisp.py --list-columns
```

Put the post-2020 loss columns you see into `config.WHISP_LOSS_COLUMNS`, then:

```bash
python scripts/run_whisp.py --plots data/plots_sample.geojson --out out/whisp
```

Service limits (Whisp 3.0.0a17): 250 geometries return inline, up to 5,000 per
job, request body up to 10 MB, 30 requests per 60 seconds per key, and at most
2 concurrent jobs.

Any real portfolio therefore has to be batched. A 16,000-farm book is four
jobs:

```python
from eudr_risk import whisp
for number, batch in whisp.chunk_geojson(geojson):
    ...  # submit each, then concatenate the tables and score once
```

Score the concatenated table rather than each batch, so tiers and the summary
describe the whole portfolio.

#### Keeping your own plot ids

Whisp numbers plots `1, 2, 3...` in its own `plotId` and does not carry your
feature properties through, so matching results back to your records by row
order is fragile. Avoid that by naming the property that holds your id:

```bash
python scripts/run_whisp.py --external-id-column plot_id
```

That is sent as `analysisOptions.externalIdColumn` and Whisp echoes the value
into its `external_id` output column. The adapter prefers `external_id`,
falls back to Whisp's `plotId` per row when it comes back blank, always keeps
Whisp's id in `whisp_plot_id` for traceability, and prints how many rows fell
back. Upstream has had trouble honouring this (whisp issue #257), hence the
per-row fallback rather than trust.

Verified working against the live API on 2026-09-19: `DL-001`, `DL-002` and
`DL-003` round-tripped intact into `external_id`, alongside Whisp's own
`plotId` of `1`, `2`, `3`.

Other `analysisOptions` the API accepts: `unitType` (we request `ha`),
`nationalCodes`, `async`, `geometryAuditTrail` (adds the `geo_original`
column). Build them with `whisp.build_analysis_options()`.

Two operational facts worth knowing: one token is one *job*, not one plot, so
a multi-feature submission returns a single token covering every feature; and
results are **ephemeral**, so persist the CSV or GeoJSON yourself.

There is also an official **Whisp QGIS plugin** (FAO, MIT) if you want a GUI:
install it from inside QGIS via *Manage and install Plugins... → Install from
ZIP*. It needs QGIS 3.40+ and calls the same API. Do not vendor it into this
repo.

### B. Earth Engine quickstart

Free noncommercial access is limited to eligible organisations (nonprofits,
academic and research institutions) and now carries monthly compute quotas;
commercial use has a monthly platform fee. Sign up at
<https://earthengine.google.com>.

```bash
pip install -r requirements.txt
```
```bash
earthengine authenticate
```
```bash
set GEE_PROJECT=my-gee-project
```
```bash
python scripts/run_pipeline.py
```

That runs the three synthetic Di Linh plots and writes `out/`. With real data
and a legality layer:

```bash
python scripts/run_pipeline.py --plots data/real/lamdong_plots.geojson --harvest-year 2026 --legality projects/my-gee-project/assets/lamdong_protection_forest --out out/2026
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
```
```bash
python tests/test_scoring.py
```
```bash
python tests/test_plots.py
```
```bash
python tests/test_whisp_adapter.py
```

119 checks, no network and no credentials:

- `test_scoring.py` (42) — tier rules, boolean coercion from `ee_to_df`,
  missing columns, empty input, the whole export bundle.
- `test_plots.py` (18) — id resolution, duplicate handling, geometry
  validation, plus a pass over Whisp's real 50-feature example file if it is
  present locally.
- `test_whisp_adapter.py` (59) — response-envelope parsing, column mapping,
  loss aggregation, payload guards, and an end-to-end run over
  `tests/fixtures/whisp_result_sample.csv`, a **real 257-column Whisp
  response**. Includes a negative control pinning the over-flagging bug
  described under Risk tiers.

Run them before you touch `config.DEFAULT_WEIGHTS` or the tier thresholds.

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

**Neither Whisp's verdict nor our own arithmetic may be trusted alone**, and
the two failure modes point in opposite directions. `assign_tier` takes the
**more severe** of the two. Both cases below are real Lâm Đồng data, and both
are pinned by tests.

*Our arithmetic alone over-flags.* Plot `DL-002` is 5.05 ha of coffee with
0.076 ha of post-2020 GFC loss, 1.51% of the plot, comfortably over the 0.5%
"high" threshold. But `EUFO_2020`, `ForTy_forest_2020` and `GFT_primary` are
all zero: that ground was already tree crop in 2020, not forest. Whisp returns
`risk_pcrop = low` and `Ind_04_disturbance_after_2020 = no`, and it is right.
Acting on our threshold would have sent a field team to a compliant farm.

*Whisp's verdict alone under-flags, which is worse.* Probe `TADUNG-r7c3` lost
2.16 ha of 4.82 ha after the cutoff — 44.8% — with RADD radar alerts and
Whisp's own `Ind_04` set to **yes**, yet `risk_pcrop` still came back `low`.
An earlier version of this code deferred to that verdict and tiered the plot
low. Across a 243-probe grid on the forest frontier, blind deference found
**2** high-risk plots; taking the worse of the two sources finds **18**.

So: post-cutoff loss only counts when `Ind_04` confirms it was forest-gated
disturbance, a verdict can add severity but never subtract it from confirmed
disturbance, and legality overrides both because Whisp cannot see land tenure.

The Earth Engine backend gets the same forest gating from `strict_jrc=True`,
which intersects Hansen loss with the JRC 2020 forest baseline.

`risk_score` (0–100) is a weighted sum used only to *order* the worklist
within a tier. Weights live in `config.DEFAULT_WEIGHTS`. Change them if you
like, but write down why: this feeds a compliance decision.

Unrecognised Whisp verdicts map to **standard**, never **low**, so a
vocabulary change in a future Whisp release fails safe.

---

## Layout

```
eudr_risk/
  config.py     EUDR constants, asset ids, weights, Whisp limits  <- tune here
  geometry.py   area + the >4 ha polygon rule
  forest.py     JRC 2020 baseline, Hansen post-cutoff loss     [backend B]
  alerts.py     RADD radar alerts (near-real-time)             [backend B]
  legality.py   WDPA + hook for Vietnam forest zoning          [backend B]
  pipeline.py   Earth Engine zonal stats -> one row per plot   [backend B]
  whisp.py      Whisp API client + column adapter              [backend A]
  scoring.py    score, tier, draft conclusion (pandas, no EE)
  dds.py        CSV / DDS GeoJSON / summary / field checklist
notebooks/
  phase1_mvp.ipynb    original MVP: Hansen loss on a map
  phase2_risk.ipynb   full Earth Engine pipeline + map + export
scripts/
  run_whisp.py        CLI, Whisp backend (no Earth Engine)
  run_pipeline.py     CLI, Earth Engine backend
tests/                73 checks, no network or credentials
data/plots_sample.geojson  synthetic demo plots
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
| Whisp API | `https://whisp.openforis.org/api` | Backend A. 257 columns from many layers combined server-side. Coffee verdict is `risk_pcrop` (lowercase), key indicator `Ind_04_disturbance_after_2020` |

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
