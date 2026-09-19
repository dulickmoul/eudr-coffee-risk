# -*- coding: utf-8 -*-
"""Accuracy assessment and area estimation, Olofsson et al. (2014).

Turns "I think the screening is accurate" into "the user's accuracy of the
high tier is 0.78, 95% CI 0.66 to 0.90". Without this the risk tiers are an
opinion, and a field team cannot know how often a flag wastes their day.

Reference: Olofsson, Foody, Herold, Stehman, Woodcock, Wulder (2014), "Good
practices for estimating area and assessing accuracy of land change",
Remote Sensing of Environment 148, 42-57. Equation numbers below are theirs.

Design, stratified random sampling with the map classes as strata:

* p_ij = W_i * n_ij / n_i                      Eq. (4)
* overall accuracy  O = sum_j p_jj
* V(O) = sum_i W_i^2 U_i (1-U_i) / (n_i - 1)   Eq. (5)
* user's accuracy   U_i = n_ii / n_i
* V(U_i) = U_i (1-U_i) / (n_i - 1)             Eq. (6)
* producer's accuracy P_j = p_jj / p_.j
* V(P_j)                                       Eq. (7), see :func:`_pa_variance`
* S(p_.j) = sqrt(sum_i W_i^2 q_ij (1-q_ij) / (n_i - 1)),  q_ij = n_ij / n_i
                                               Eq. (10)
* area A_j = A_total * p_.j

Two things this module refuses to do, because both would flatter the result:
it will not compute a variance from a stratum with fewer than two samples,
and it will not report producer's accuracy for a class the reference data
never found.
"""

import csv
import math
import os
import random

Z_95 = 1.959963985  # two-sided normal quantile for a 95% interval

# Below this many samples in a stratum the interval is too wide to mean much.
MIN_USEFUL_STRATUM_N = 20

REVIEW_COLUMNS = [
    "plot_id", "stratum", "map_class", "reference_class",
    "reviewer", "review_date", "evidence", "notes",
]


class ValidationError(ValueError):
    pass


# --------------------------------------------------------------------------
# Sampling design
# --------------------------------------------------------------------------

def weights(strata_sizes):
    """W_i = N_i / N. ``strata_sizes`` maps class -> population size."""
    total = sum(strata_sizes.values())
    if total <= 0:
        raise ValidationError("Total population size must be positive.")
    return {k: v / total for k, v in strata_sizes.items()}


def sample_size(strata_sizes, expected_ua, target_se=0.01):
    """Total sample size for a target standard error of overall accuracy.

    Olofsson Eq. (13): n = ( sum_i W_i S_i / S(O) )^2, S_i = sqrt(U_i(1-U_i)).

    ``expected_ua`` is your guess at each class's user's accuracy, from a
    pilot or from experience. Guessing high shrinks the sample, so guess
    conservatively.
    """
    if not 0 < target_se < 1:
        raise ValidationError("target_se must be between 0 and 1.")
    w = weights(strata_sizes)
    missing = set(w) - set(expected_ua)
    if missing:
        raise ValidationError(f"No expected user's accuracy for {sorted(missing)}")

    total = 0.0
    for cls, wi in w.items():
        ua = expected_ua[cls]
        if not 0 <= ua <= 1:
            raise ValidationError(f"expected_ua[{cls!r}] must be in [0, 1]")
        total += wi * math.sqrt(ua * (1 - ua))
    return math.ceil((total / target_se) ** 2)


def allocate(n, strata_sizes, minimum=50, scheme="proportional"):
    """Split a total sample across strata.

    Proportional allocation starves the rare classes, and the rare class here
    is exactly the one that matters: ``high``. So a floor is applied first and
    the remainder is allocated proportionally. Olofsson suggests 50 to 100
    units for classes you want to estimate individually.
    """
    if n <= 0:
        raise ValidationError("n must be positive.")
    classes = list(strata_sizes)
    if scheme == "equal":
        base = n // len(classes)
        out = {c: base for c in classes}
    elif scheme == "proportional":
        w = weights(strata_sizes)
        floor_total = min(minimum * len(classes), n)
        out = {c: min(minimum, floor_total // len(classes)) for c in classes}
        remaining = n - sum(out.values())
        if remaining > 0:
            for c in classes:
                out[c] += int(remaining * w[c])
    else:
        raise ValidationError("scheme must be 'proportional' or 'equal'")

    # Never ask for more units than a stratum contains.
    for c in classes:
        out[c] = int(min(out[c], strata_sizes[c]))
    return out


def draw_sample(items, allocation, seed=None):
    """Stratified random sample. ``items`` maps class -> list of plot ids."""
    rng = random.Random(seed)
    chosen = {}
    for cls, want in allocation.items():
        pool = list(items.get(cls, []))
        if want > len(pool):
            raise ValidationError(
                f"Stratum {cls!r} has {len(pool)} plots but {want} requested."
            )
        chosen[cls] = rng.sample(pool, want)
    return chosen


def write_review_sheet(chosen, path):
    """Blank reference-label sheet for a reviewer to fill in.

    ``reference_class`` is left empty on purpose: it must come from a better
    source than the map, not be copied from it.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for cls, ids in sorted(chosen.items()):
            for plot_id in ids:
                writer.writerow({
                    "plot_id": plot_id, "stratum": cls, "map_class": cls,
                    "reference_class": "", "reviewer": "", "review_date": "",
                    "evidence": "", "notes": "",
                })
    return path


def read_review_sheet(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh)]
    unlabelled = [r["plot_id"] for r in rows if not (r.get("reference_class") or "").strip()]
    return rows, unlabelled


# --------------------------------------------------------------------------
# Error matrix
# --------------------------------------------------------------------------

def error_matrix(rows, classes=None, map_key="map_class", ref_key="reference_class"):
    """Counts n_ij from labelled review rows. Unlabelled rows are ignored."""
    labelled = [
        r for r in rows
        if (r.get(ref_key) or "").strip() and (r.get(map_key) or "").strip()
    ]
    if not labelled:
        raise ValidationError("No labelled rows: nothing to assess.")

    if classes is None:
        found = {r[map_key].strip() for r in labelled}
        found |= {r[ref_key].strip() for r in labelled}
        classes = sorted(found)

    counts = {(i, j): 0 for i in classes for j in classes}
    for row in labelled:
        i, j = row[map_key].strip(), row[ref_key].strip()
        if (i, j) not in counts:
            raise ValidationError(f"Label outside the class list: map={i!r} ref={j!r}")
        counts[(i, j)] += 1
    return counts, list(classes)


# --------------------------------------------------------------------------
# Estimation
# --------------------------------------------------------------------------

def _pa_variance(j, classes, counts, n, N, ua, pa):
    """Olofsson Eq. (7). Undefined when the reference class was never found."""
    n_hat_j = sum((N[i] / n[i]) * counts[(i, j)] for i in classes if n[i])
    if n_hat_j <= 0:
        return None

    first = (
        N[j] ** 2 * (1 - pa) ** 2 * ua * (1 - ua) / (n[j] - 1)
        if n[j] > 1 else 0.0
    )
    second = 0.0
    for i in classes:
        if i == j or n[i] < 2:
            continue
        q = counts[(i, j)] / n[i]
        second += N[i] ** 2 * q * (1 - q) / (n[i] - 1)
    return (first + pa ** 2 * second) / n_hat_j ** 2


def estimate(counts, strata_sizes, classes=None, total_area=None, z=Z_95):
    """Full Olofsson estimation. Returns a nested dict of results.

    ``strata_sizes`` is N_i, the population size of each map class, in
    whatever unit you want the areas in (hectares, pixels, plot counts).
    ``total_area`` defaults to their sum.
    """
    if classes is None:
        classes = sorted({i for i, _ in counts})

    missing = [c for c in classes if c not in strata_sizes]
    if missing:
        raise ValidationError(f"No population size given for {missing}")

    n = {i: sum(counts.get((i, j), 0) for j in classes) for i in classes}
    empty = [i for i in classes if n[i] == 0]
    if empty:
        raise ValidationError(
            f"Strata {empty} have no sample units; they cannot be estimated."
        )

    N = {i: float(strata_sizes[i]) for i in classes}
    N_total = sum(N.values())
    A_total = float(total_area) if total_area is not None else N_total
    W = {i: N[i] / N_total for i in classes}

    thin = [i for i in classes if n[i] < MIN_USEFUL_STRATUM_N]
    single = [i for i in classes if n[i] < 2]

    # Eq. (4)
    p = {
        (i, j): W[i] * counts.get((i, j), 0) / n[i]
        for i in classes for j in classes
    }

    # User's accuracy, Eq. (6)
    user = {}
    for i in classes:
        ua = counts.get((i, i), 0) / n[i]
        var = ua * (1 - ua) / (n[i] - 1) if n[i] > 1 else None
        user[i] = _with_ci(ua, var, z, n[i])

    # Overall accuracy, Eq. (5)
    oa = sum(p[(j, j)] for j in classes)
    oa_var = sum(
        W[i] ** 2 * user[i]["value"] * (1 - user[i]["value"]) / (n[i] - 1)
        for i in classes if n[i] > 1
    ) if not single else None
    overall = _with_ci(oa, oa_var, z, sum(n.values()))

    # Producer's accuracy and area, Eqs. (7) and (10)
    producer, area = {}, {}
    for j in classes:
        p_col = sum(p[(i, j)] for i in classes)
        pa = p[(j, j)] / p_col if p_col > 0 else None
        if pa is None:
            producer[j] = _with_ci(None, None, z, n[j])
        else:
            var = _pa_variance(j, classes, counts, n, N, user[j]["value"], pa)
            producer[j] = _with_ci(pa, var, z, n[j])

        se_p = math.sqrt(sum(
            W[i] ** 2 * (counts.get((i, j), 0) / n[i])
            * (1 - counts.get((i, j), 0) / n[i]) / (n[i] - 1)
            for i in classes if n[i] > 1
        )) if not single else None
        area[j] = {
            "proportion": p_col,
            "proportion_se": se_p,
            "area": A_total * p_col,
            "area_se": A_total * se_p if se_p is not None else None,
            "area_ci95": z * A_total * se_p if se_p is not None else None,
            "mapped_area": N[j],
            "bias": A_total * p_col - N[j],
        }

    return {
        "classes": list(classes),
        "n": n,
        "N": N,
        "W": W,
        "total_area": A_total,
        "proportions": p,
        "overall_accuracy": overall,
        "users_accuracy": user,
        "producers_accuracy": producer,
        "area": area,
        "warnings": _warnings(thin, single),
    }


def _with_ci(value, variance, z, n):
    se = math.sqrt(variance) if variance is not None and variance >= 0 else None
    return {
        "value": value,
        "se": se,
        "ci95": z * se if se is not None else None,
        "low": max(0.0, value - z * se) if (value is not None and se is not None) else None,
        "high": min(1.0, value + z * se) if (value is not None and se is not None) else None,
        "n": n,
    }


def _warnings(thin, single):
    out = []
    if single:
        out.append(
            f"Strata {sorted(single)} have fewer than 2 samples; variances "
            "cannot be estimated and are reported as None."
        )
    if thin:
        out.append(
            f"Strata {sorted(thin)} have fewer than {MIN_USEFUL_STRATUM_N} "
            "samples. Point estimates are unbiased but the intervals are too "
            "wide to act on. Olofsson suggests 50 to 100 per class you want "
            "to report."
        )
    return out


def format_report(result, decimals=3):
    """Plain-text report. The number to read first is the high tier's UA."""
    lines = []
    oa = result["overall_accuracy"]
    lines.append(f"Overall accuracy: {_fmt(oa, decimals)}  (n={oa['n']})")
    lines.append("")
    width = max(18, max(len(str(c)) for c in result["classes"]) + 2)
    lines.append(
        f"{'class':<{width}}{'n':>6}  {'users acc (95% CI)':<24}"
        f"{'producers acc (95% CI)':<24}"
    )
    for c in result["classes"]:
        ua, pa = result["users_accuracy"][c], result["producers_accuracy"][c]
        lines.append(
            f"{c:<{width}}{ua['n']:>6}  {_fmt(ua, decimals):<24}"
            f"{_fmt(pa, decimals):<24}"
        )
    lines.append("")
    lines.append(
        f"{'class':<{width}}{'mapped':>14}{'estimated':>14}{'+/- 95%':>12}{'bias':>12}"
    )
    for c in result["classes"]:
        a = result["area"][c]
        ci = f"{a['area_ci95']:.1f}" if a["area_ci95"] is not None else "n/a"
        lines.append(
            f"{c:<{width}}{a['mapped_area']:>14.1f}{a['area']:>14.1f}"
            f"{ci:>12}{a['bias']:>12.1f}"
        )
    for warning in result["warnings"]:
        lines.append("")
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines)


def _fmt(entry, decimals):
    if entry["value"] is None:
        return "not estimable"
    if entry["ci95"] is None:
        return f"{entry['value']:.{decimals}f} (CI n/a)"
    return f"{entry['value']:.{decimals}f} +/- {entry['ci95']:.{decimals}f}"
