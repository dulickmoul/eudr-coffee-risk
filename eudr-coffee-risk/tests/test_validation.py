# -*- coding: utf-8 -*-
"""Tests for the Olofsson accuracy estimator.

The core test reproduces the published worked example in Olofsson et al.
(2014) section 5, Tables 8 and 9. Implementing these formulas without
checking them against a published example is how you end up with confidently
wrong numbers, so the paper's own figures are the fixture.

What reproduces exactly: user's accuracy for all four classes, overall
accuracy, all four producer's accuracy point estimates, all four area
estimates to the hectare, all four area confidence intervals, and the sample
size from Eq. (13).

What does not: two of the four printed producer's accuracy intervals. See
test_producers_accuracy_ci_differs_from_paper for the detail. The paper also
prints S(A_1) as 34,097 pixels while its own margin of error (68,418) implies
34,907, so it contains at least one arithmetic slip. We implement the
published equation and record the difference rather than tuning tolerances
until the test goes green.

    python tests/test_validation.py
"""

import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from eudr_risk import validation as v

FAILS = []

CLASSES = ["deforestation", "forest_gain", "stable_forest", "stable_nonforest"]

# Olofsson et al. (2014) Table 8: sample counts n_ij.
COUNTS = {
    ("deforestation", "deforestation"): 66,
    ("deforestation", "forest_gain"): 0,
    ("deforestation", "stable_forest"): 5,
    ("deforestation", "stable_nonforest"): 4,
    ("forest_gain", "deforestation"): 0,
    ("forest_gain", "forest_gain"): 55,
    ("forest_gain", "stable_forest"): 8,
    ("forest_gain", "stable_nonforest"): 12,
    ("stable_forest", "deforestation"): 1,
    ("stable_forest", "forest_gain"): 0,
    ("stable_forest", "stable_forest"): 153,
    ("stable_forest", "stable_nonforest"): 11,
    ("stable_nonforest", "deforestation"): 2,
    ("stable_nonforest", "forest_gain"): 1,
    ("stable_nonforest", "stable_forest"): 9,
    ("stable_nonforest", "stable_nonforest"): 313,
}

# Mapped area per class. The paper works in 30 m pixels (0.09 ha each);
# hectares here so the published hectare figures can be compared directly.
PIXELS = {
    "deforestation": 200_000,
    "forest_gain": 150_000,
    "stable_forest": 3_200_000,
    "stable_nonforest": 6_450_000,
}
HA_PER_PIXEL = 0.09
STRATA_HA = {k: n * HA_PER_PIXEL for k, n in PIXELS.items()}
TOTAL_HA = sum(STRATA_HA.values())  # 900,000 ha


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise AssertionError(f"{label}: got={got!r} want={want!r}")


def close(label, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    shown = f"{got:.4f}" if isinstance(got, float) else got
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={shown} want={want}+/-{tol}")
    if not ok:
        FAILS.append(label)
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise AssertionError(f"{label}: got={got!r} want={want!r}")


def result():
    return v.estimate(COUNTS, STRATA_HA, classes=CLASSES, total_area=TOTAL_HA)


def test_weights_and_n():
    r = result()
    close("W deforestation", r["W"]["deforestation"], 0.020, 1e-9)
    close("W forest_gain", r["W"]["forest_gain"], 0.015, 1e-9)
    close("W stable_forest", r["W"]["stable_forest"], 0.320, 1e-9)
    close("W stable_nonforest", r["W"]["stable_nonforest"], 0.645, 1e-9)
    check("stratum sample sizes",
          [r["n"][c] for c in CLASSES], [75, 75, 165, 325])
    check("total sample", sum(r["n"].values()), 640)


def test_proportions_table9():
    """Table 9: the error matrix as estimated area proportions."""
    r = result()
    p = r["proportions"]
    close("p_11", p[("deforestation", "deforestation")], 0.0176, 5e-5)
    close("p_13", p[("deforestation", "stable_forest")], 0.0013, 5e-5)
    close("p_22", p[("forest_gain", "forest_gain")], 0.0110, 5e-5)
    close("p_33", p[("stable_forest", "stable_forest")], 0.2967, 5e-5)
    close("p_44", p[("stable_nonforest", "stable_nonforest")], 0.6212, 5e-5)
    # Column totals, Table 9 bottom row.
    for cls, want in zip(CLASSES, [0.0235, 0.0130, 0.3175, 0.6460]):
        close(f"column total {cls}", r["area"][cls]["proportion"], want, 5e-5)


def test_users_accuracy():
    """Paper: 0.88+/-0.07, 0.73+/-0.10, 0.93+/-0.04, 0.96+/-0.02."""
    r = result()["users_accuracy"]
    for cls, value, ci in [
        ("deforestation", 0.88, 0.07),
        ("forest_gain", 0.73, 0.10),
        ("stable_forest", 0.93, 0.04),
        ("stable_nonforest", 0.96, 0.02),
    ]:
        close(f"UA {cls}", r[cls]["value"], value, 0.005)
        close(f"UA CI {cls}", r[cls]["ci95"], ci, 0.005)


def test_overall_accuracy():
    """Paper: 0.95 +/- 0.02."""
    oa = result()["overall_accuracy"]
    close("OA", oa["value"], 0.9465, 5e-4)
    close("OA rounds to paper's 0.95", round(oa["value"], 2), 0.95, 1e-9)
    close("OA CI", oa["ci95"], 0.0185, 5e-3)


def test_producers_accuracy_points():
    """Paper: 0.75, 0.85, 0.93, 0.96. All four reproduce."""
    r = result()["producers_accuracy"]
    for cls, value in [
        ("deforestation", 0.75),
        ("forest_gain", 0.85),
        ("stable_forest", 0.93),
        ("stable_nonforest", 0.96),
    ]:
        close(f"PA {cls}", r[cls]["value"], value, 0.005)


def test_producers_accuracy_ci_differs_from_paper():
    """Two of the paper's four PA intervals do not reproduce from Eq. (7).

    Paper prints 0.21, 0.23, 0.03, 0.01. Eq. (7) as published gives 0.213,
    0.254, 0.034, 0.018. Deforestation and stable forest agree; forest gain
    and stable non-forest do not. Pinned here so the discrepancy stays
    visible instead of being rationalised away, and so a later change to our
    implementation is caught.
    """
    r = result()["producers_accuracy"]
    close("PA CI deforestation (paper 0.21)", r["deforestation"]["ci95"], 0.213, 0.003)
    close("PA CI stable_forest (paper 0.03)", r["stable_forest"]["ci95"], 0.034, 0.003)
    close("PA CI forest_gain (paper prints 0.23)",
          r["forest_gain"]["ci95"], 0.254, 0.003)
    close("PA CI stable_nonforest (paper prints 0.01)",
          r["stable_nonforest"]["ci95"], 0.018, 0.003)


def test_area_estimates():
    """Paper: 21,158+/-6158; 11,686+/-3756; 285,770+/-15,510; 581,386+/-16,282 ha."""
    r = result()["area"]
    for cls, area, ci in [
        ("deforestation", 21_158, 6_158),
        ("forest_gain", 11_686, 3_756),
        ("stable_forest", 285_770, 15_510),
        ("stable_nonforest", 581_386, 16_282),
    ]:
        close(f"area {cls}", r[cls]["area"], area, 2.0)
        close(f"area CI {cls}", r[cls]["area_ci95"], ci, 3.0)

    # The paper's headline point: mapped deforestation was an underestimate.
    close("deforestation bias vs mapped area",
          r["deforestation"]["bias"], 21_158 - 18_000, 2.0)
    check("mapped area carried through",
          r["deforestation"]["mapped_area"], 18_000.0)


def test_sample_size_eq13():
    """Paper: target SE 0.01 with expected UA 0.70/0.60/0.90/0.95 gives n=641."""
    expected = {
        "deforestation": 0.70,
        "forest_gain": 0.60,
        "stable_forest": 0.90,
        "stable_nonforest": 0.95,
    }
    check("Eq. (13) sample size",
          v.sample_size(STRATA_HA, expected, target_se=0.01), 641)
    # Halving the target standard error quadruples the sample. 4 x 641 = 2564;
    # 2563 because each is separately rounded up from 640.5 and 2562.1.
    check("halving the target SE quadruples n",
          v.sample_size(STRATA_HA, expected, target_se=0.005), 2563)


def test_allocation():
    alloc = v.allocate(641, STRATA_HA, minimum=50)
    check("allocation covers all strata", sorted(alloc), sorted(CLASSES))
    check("rare classes get the floor",
          alloc["deforestation"] >= 50 and alloc["forest_gain"] >= 50, True)
    check("total within the budget", sum(alloc.values()) <= 641, True)
    # Proportional alone would starve the rare class; that is the whole point.
    proportional_only = int(641 * 0.020)
    check("floor beats bare proportional",
          alloc["deforestation"] > proportional_only, True)

    equal = v.allocate(640, STRATA_HA, scheme="equal")
    check("equal allocation", set(equal.values()), {160})


def test_sampling_and_review_sheet():
    items = {
        "low": [f"L{i}" for i in range(100)],
        "standard": [f"S{i}" for i in range(40)],
        "high": [f"H{i}" for i in range(5)],
    }
    alloc = {"low": 10, "standard": 8, "high": 5}
    chosen = v.draw_sample(items, alloc, seed=42)
    check("sample sizes honoured",
          {k: len(x) for k, x in chosen.items()}, alloc)
    check("no duplicates drawn",
          all(len(set(x)) == len(x) for x in chosen.values()), True)
    check("deterministic with a seed",
          v.draw_sample(items, alloc, seed=42), chosen)

    # Asking for more than a stratum holds must fail, not silently shrink.
    try:
        v.draw_sample(items, {"high": 10}, seed=1)
        check("over-draw rejected", False, True)
    except v.ValidationError:
        check("over-draw rejected", True, True)

    path = os.path.join(tempfile.mkdtemp(), "review.csv")
    v.write_review_sheet(chosen, path)
    rows, unlabelled = v.read_review_sheet(path)
    check("sheet row count", len(rows), 23)
    check("reference labels start empty", len(unlabelled), 23)
    check("map class recorded",
          {r["map_class"] for r in rows}, {"low", "standard", "high"})


def test_error_matrix_from_rows():
    rows = [
        {"map_class": "high", "reference_class": "high"},
        {"map_class": "high", "reference_class": "low"},
        {"map_class": "low", "reference_class": "low"},
        {"map_class": "low", "reference_class": ""},        # unlabelled, skipped
    ]
    counts, classes = v.error_matrix(rows)
    check("classes discovered", classes, ["high", "low"])
    check("diagonal counted", counts[("high", "high")], 1)
    check("off-diagonal counted", counts[("high", "low")], 1)
    check("unlabelled ignored", counts[("low", "low")], 1)

    try:
        v.error_matrix([{"map_class": "x", "reference_class": ""}])
        check("all-unlabelled rejected", False, True)
    except v.ValidationError:
        check("all-unlabelled rejected", True, True)


def test_guards():
    """The estimator must refuse to flatter thin or empty samples."""
    # A stratum with a single sample: variance undefined, not zero.
    counts = {("a", "a"): 1, ("a", "b"): 0, ("b", "a"): 0, ("b", "b"): 30}
    r = v.estimate(counts, {"a": 100.0, "b": 900.0}, classes=["a", "b"])
    check("single-sample stratum has no UA variance",
          r["users_accuracy"]["a"]["ci95"], None)
    check("overall accuracy variance suppressed too",
          r["overall_accuracy"]["ci95"], None)
    check("warned about it", any("fewer than 2" in w for w in r["warnings"]), True)

    # An empty stratum cannot be estimated at all.
    try:
        v.estimate({("a", "a"): 0, ("b", "b"): 5}, {"a": 1.0, "b": 1.0},
                   classes=["a", "b"])
        check("empty stratum rejected", False, True)
    except v.ValidationError:
        check("empty stratum rejected", True, True)

    # A reference class nobody found: PA is undefined, not 0 or 1.
    counts = {("a", "a"): 30, ("a", "b"): 0, ("b", "a"): 30, ("b", "b"): 0}
    r = v.estimate(counts, {"a": 500.0, "b": 500.0}, classes=["a", "b"])
    check("PA undefined for an unfound class",
          r["producers_accuracy"]["b"]["value"], None)

    # Thin strata still warn even when estimable.
    thin = {("a", "a"): 5, ("a", "b"): 1, ("b", "a"): 1, ("b", "b"): 25}
    r = v.estimate(thin, {"a": 100.0, "b": 900.0}, classes=["a", "b"])
    check("thin stratum warning",
          any("too\nwide" in w or "too wide" in w for w in r["warnings"]), True)

    try:
        v.sample_size(STRATA_HA, {"deforestation": 0.7}, target_se=0.01)
        check("missing expected UA rejected", False, True)
    except v.ValidationError:
        check("missing expected UA rejected", True, True)


def test_report_renders():
    text = v.format_report(result())
    check("report mentions overall accuracy", "Overall accuracy" in text, True)
    check("report lists every class",
          all(c in text for c in CLASSES), True)
    check("report shows an area column", "estimated" in text, True)
    print("\n" + text)


def main():
    print("--- design ---")
    test_weights_and_n()
    test_sample_size_eq13()
    test_allocation()
    test_sampling_and_review_sheet()
    test_error_matrix_from_rows()
    print("\n--- Olofsson 2014 worked example, Tables 8 and 9 ---")
    test_proportions_table9()
    test_users_accuracy()
    test_overall_accuracy()
    test_producers_accuracy_points()
    test_producers_accuracy_ci_differs_from_paper()
    test_area_estimates()
    print("\n--- guards ---")
    test_guards()
    print("\n--- report ---")
    test_report_renders()

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
