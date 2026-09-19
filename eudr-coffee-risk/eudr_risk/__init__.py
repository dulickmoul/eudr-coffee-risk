"""EUDR coffee risk analytics.

Plot polygons in, per-plot deforestation risk and a due-diligence bundle out.
Built for robusta smallholders around Di Linh, Lam Dong, but nothing here is
Vietnam-specific beyond :mod:`eudr_risk.config`.

Typical use::

    import ee, geemap, json
    from eudr_risk import pipeline, scoring, dds

    ee.Initialize(project="my-gee-project")
    fc = geemap.geojson_to_ee(json.load(open("data/plots_sample.geojson")))
    features = pipeline.extract_features(fc)
    table = scoring.score_dataframe(pipeline.to_dataframe(features))

See ``scripts/run_pipeline.py`` for the end-to-end version.

Submodules are imported lazily (PEP 562) so that :mod:`eudr_risk.scoring` and
:mod:`eudr_risk.dds`, which are pure pandas/stdlib, stay usable and testable
without the Earth Engine client installed.
"""

import importlib

__version__ = "0.2.0"  # Phase 2: JRC baseline, RADD alerts, scoring, legality

_SUBMODULES = frozenset(
    {
        "alerts",
        "config",
        "dds",
        "forest",
        "geometry",
        "legality",
        "legality_checklist",
        "pipeline",
        "plots",
        "scoring",
        "whisp",
    }
)

__all__ = sorted(_SUBMODULES)


def __getattr__(name):
    if name in _SUBMODULES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_SUBMODULES | {"__version__"})
