# Test fixtures: where these coordinates came from

Every coordinate in this folder is synthetic or machine-generated. **None is a
surveyed farm boundary and none identifies a household.** Real plot boundaries
are personal data and are blocked by the repository `.gitignore`.

| File | Provenance |
|---|---|
| `whisp_result_sample.csv` | Whisp 3.0.0a17 output for the three **synthetic demo polygons** in `data/plots_sample.geojson`. Those polygons were drawn by hand near Di Linh to exercise the code paths, including one deliberately just over 4 ha to trigger the EUDR polygon rule. 257 columns, kept in full so the adapter is tested against real column names rather than a guess. |
| `whisp_frontier_sample.csv` | 18 rows from a sweep of **machine-generated probe cells** on a regular lattice over the Lâm Đồng and Đắk Nông forest frontier: fixed region centres, a fixed 0.012° step, and ids of the form `REGION-rNcM`. They are grid squares, not parcels. |
| `whisp_genuine_high.csv` | 7 rows from the same lattice, kept because they carry a genuine post-cutoff `high` verdict. Without them the high path could not be tested. |

## Why the verdicts in here are not accusations

A `high` verdict is a screening signal from a tool whose accuracy has not been
measured against ground truth. The underlying alerts (Hansen, GLAD, RADD, TMF)
are public and can be inspected by anyone on Global Forest Watch at the same
coordinates, so nothing here reveals information that was not already open.

These files exist so the scoring rules can be tested against real API output
instead of invented numbers. The lesson that produced most of them is recorded
in the repository README: neither the local heuristic nor the published verdict
is trustworthy alone, and both failure directions occurred in this data.
