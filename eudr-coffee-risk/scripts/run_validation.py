#!/usr/bin/env python
"""Accuracy assessment of the risk tiers, Olofsson et al. (2014).

Two steps, with human work in between.

1. Draw a stratified sample from a scored risk table and write a review sheet::

     python scripts/run_validation.py sample --table out/run/risk_table.csv \
         --out out/run/review.csv --target-se 0.02

2. A reviewer fills the ``reference_class`` column from a better source than
   the map (high-resolution imagery, or a field visit). Then estimate::

     python scripts/run_validation.py estimate --table out/run/risk_table.csv \
         --review out/run/review.csv

The number to read first is the **user's accuracy of the high tier**: it is
the share of flagged plots that really are flagged-worthy, which is exactly
the false-alarm rate a field team experiences.

Nothing here talks to a network or needs an API key.
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIER_ORDER = ["low", "standard", "high"]
# Conservative guesses at user's accuracy, used only to size the sample.
# Guessing high shrinks the sample, so these lean pessimistic.
DEFAULT_EXPECTED_UA = {"low": 0.90, "standard": 0.60, "high": 0.70}


def read_table(path, tier_col="risk_tier", id_col="plot_id", area_col="area_ha"):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{path} is empty.")
    for col in (tier_col, id_col):
        if col not in rows[0]:
            sys.exit(f"{path} has no {col!r} column.")

    items, sizes = {}, {}
    for row in rows:
        tier = (row[tier_col] or "").strip()
        if not tier:
            continue
        items.setdefault(tier, []).append(row[id_col])
        try:
            area = float(row.get(area_col) or 0)
        except ValueError:
            area = 0.0
        sizes[tier] = sizes.get(tier, 0.0) + area

    # Fall back to plot counts when no usable area column is present.
    if not any(sizes.values()):
        sizes = {t: float(len(v)) for t, v in items.items()}
        print("NOTE: no usable area column; using plot counts as stratum sizes.")
    return items, sizes


def cmd_sample(args):
    from eudr_risk import validation as v

    items, sizes = read_table(args.table)
    present = [t for t in TIER_ORDER if t in items] + [
        t for t in items if t not in TIER_ORDER
    ]
    expected = {t: DEFAULT_EXPECTED_UA.get(t, 0.70) for t in present}

    n = v.sample_size(sizes, expected, target_se=args.target_se)
    alloc = v.allocate(n, sizes, minimum=args.minimum)
    # Cannot sample more plots than a tier contains.
    alloc = {t: min(c, len(items[t])) for t, c in alloc.items()}

    print(f"strata sizes (ha or count): "
          f"{ {t: round(sizes[t], 1) for t in present} }")
    print(f"plots per stratum         : { {t: len(items[t]) for t in present} }")
    print(f"Eq. (13) sample size for SE {args.target_se}: n = {n}")
    print(f"allocation (floor {args.minimum})       : {alloc}")

    chosen = v.draw_sample(items, alloc, seed=args.seed)
    path = v.write_review_sheet(chosen, args.out)
    print(f"\nwrote {sum(len(x) for x in chosen.values())} rows to {path}")
    print("Fill the reference_class column from imagery or a field visit, "
          "then run: run_validation.py estimate")
    return 0


def cmd_estimate(args):
    from eudr_risk import validation as v

    _, sizes = read_table(args.table)
    rows, unlabelled = v.read_review_sheet(args.review)
    if unlabelled:
        print(f"{len(unlabelled)} of {len(rows)} rows are still unlabelled; "
              "they are ignored.")
    if len(unlabelled) == len(rows):
        sys.exit("Nothing labelled yet, so there is nothing to estimate.")

    counts, classes = v.error_matrix(rows)
    missing = [c for c in classes if c not in sizes]
    if missing:
        sys.exit(
            f"Reference labels {missing} are not tiers in the risk table. "
            f"Use only: {sorted(sizes)}"
        )

    result = v.estimate(counts, sizes, classes=classes)
    print(v.format_report(result))

    high = result["users_accuracy"].get("high")
    if high and high["value"] is not None:
        ci = f" +/- {high['ci95']:.3f}" if high["ci95"] is not None else ""
        print(
            f"\nHeadline: {high['value']:.1%}{ci} of plots flagged 'high' are "
            f"genuinely high (n={high['n']}). The rest are false alarms the "
            "field team absorbs."
        )

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        serialisable = dict(result)
        serialisable["proportions"] = {
            f"{i}|{j}": p for (i, j), p in result["proportions"].items()
        }
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(serialisable, fh, indent=2, ensure_ascii=False)
        print(f"\nwrote {args.out}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sample", help="Draw a stratified sample.")
    s.add_argument("--table", required=True, help="Scored risk_table.csv")
    s.add_argument("--out", default=os.path.join("out", "review.csv"))
    s.add_argument("--target-se", type=float, default=0.02,
                   help="Target standard error of overall accuracy.")
    s.add_argument("--minimum", type=int, default=50,
                   help="Floor per stratum, so rare tiers are estimable.")
    s.add_argument("--seed", type=int, default=None)
    s.set_defaults(func=cmd_sample)

    e = sub.add_parser("estimate", help="Estimate accuracy from a filled sheet.")
    e.add_argument("--table", required=True)
    e.add_argument("--review", required=True)
    e.add_argument("--out", default=None, help="Optional JSON output.")
    e.set_defaults(func=cmd_estimate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
