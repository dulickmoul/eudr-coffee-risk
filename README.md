# EUDR Coffee Risk

Deforestation-risk screening for coffee plots under the EU Deforestation
Regulation, plus a tracker for the half of EUDR that satellites cannot answer.

Plot boundaries in, per-plot risk tier and a due-diligence bundle out.
Every dataset is open. Running cost is zero.

Built for robusta smallholders around **Di Linh, Lâm Đồng**, Vietnam's largest
robusta area, but nothing is region-locked beyond one config file.

---

## The problem

A buyer working with ~16,000 smallholders cannot send a field team to every
farm before the EUDR deadline. This ranks plots so the team visits the handful
that actually need checking.

## What a real run looks like

900 probe cells on the Lâm Đồng forest frontier, screened against the
31 December 2020 forest baseline:

| Verdict | Plots | |
|---|---:|---|
| `low` | 555 | no evidence of post-cutoff deforestation |
| `more_info_needed` | 342 | **evidence is insufficient, not clean** |
| `high` | 3 | post-cutoff disturbance on land that was forest in 2020 |

The interesting number is 342. The dominant class is "we cannot tell yet", so
effort belongs there rather than in tuning the `high` threshold.

## Two halves, and satellites answer one

EUDR requires products to be deforestation-free **and produced legally**.
Article 2(40) defines legality across eight areas: land use rights,
environmental protection, forest rules, third parties' rights, labour rights,
human rights, FPIC, and tax and customs. Imagery cannot answer six of them.

So the repo does two separate jobs and keeps them visibly separate:

1. **Deforestation screening** from satellite evidence, which ranks plots.
2. **A legality checklist** over the eight areas, filled from paper records by
   a person.

A plot reads `eudr_readiness = ready` only when **both** pass. A plot with no
checklist reads `not_started`, never `ready`. An unassessed plot is not a
compliant plot.

## Measured, not asserted

Most screening tools output a risk flag and stop. This one can tell you how
often that flag is right.

[`eudr_risk/validation.py`](eudr-coffee-risk/eudr_risk/validation.py) implements
the Olofsson et al. (2014) accuracy assessment: stratified random sampling, an
error matrix by **area proportion** rather than raw counts, user's and
producer's accuracy and area estimates with 95% confidence intervals, and
sample-size calculation. It reproduces the published worked example, and the
two places where the arithmetic disagrees with the printed paper are pinned by
a test rather than smoothed over with a looser tolerance.

The first number to read is **user's accuracy of the `high` class**: the share
of flagged plots that were flagged correctly, which is the false-alarm rate a
field team has to absorb.

## Two data backends, one output

- **Whisp (FAO Open Foris) hosted API** — needs only an API key, no Earth
  Engine project. `scripts/run_whisp.py`
- **Earth Engine** — run the layers yourself. `scripts/run_pipeline.py`

Layers: JRC forest 2020, Hansen Global Forest Change annual loss, RADD
Sentinel-1 radar alerts, GLAD alerts, TMF disturbance, WDPA protected areas.

Neither source is trusted alone. Scoring takes the more severe of the two
verdicts, because both failure directions showed up in real data: the local
heuristic raised a false alarm on land that was already tree crop in 2020, and
deferring entirely to the published verdict missed a plot that had lost 45% of
its area with radar alerts confirming it.

## Quick start

```bash
cd eudr-coffee-risk
pip install -r requirements.txt

export WHISP_API_KEY=...
python scripts/run_whisp.py --plots data/plots_sample.geojson --out out/run
```

Tests need only `pandas`, no network and no credentials:

```bash
for t in tests/test_*.py; do python "$t"; done   # 296 checks
```

Full documentation, including the scoring rules, the Whisp column reference and
the legality workflow, is in
[`eudr-coffee-risk/README.md`](eudr-coffee-risk/README.md).

## Data and privacy

Every coordinate committed here is either a **synthetic demo polygon** or a
**machine-generated probe cell** on a regular grid. None of it is a surveyed
farm boundary and none of it identifies a household. Real plot boundaries are
personal data and are blocked by `.gitignore`, as are API keys and Earth Engine
credentials.

A `high` verdict from this tool is a **screening signal from a tool whose
accuracy has not yet been measured against ground truth**. It is not a finding
of illegality and should not be reported as one. The underlying alerts are
public and can be inspected independently on Global Forest Watch.

## Licence

MIT. See [LICENSE](LICENSE).
