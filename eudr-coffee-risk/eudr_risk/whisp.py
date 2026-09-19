"""Whisp backend: per-plot deforestation indicators via the Open Foris API.

Why this module exists alongside :mod:`eudr_risk.pipeline`:

* ``pipeline`` computes everything yourself in Earth Engine. Full control,
  but you need your own registered GEE Cloud project, and the free
  noncommercial tier is restricted to eligible organisations.
* ``whisp`` sends geometries to FAO's hosted Whisp service, which runs a
  "convergence of evidence" analysis over many open datasets and returns
  per-plot indicators plus its own EUDR-oriented risk verdict. You need an
  API key, not an Earth Engine project.

Both feed the same downstream :mod:`eudr_risk.scoring` and
:mod:`eudr_risk.dds`, so the export bundle is identical either way.

Endpoints, the ``x-api-key`` requirement and the limits were taken from the
published OpenAPI spec at ``https://whisp.openforis.org/api/docs`` and from
``GET /api/config`` (Whisp 3.0.0a17, September 2026).

**Not yet exercised against a live key.** The response envelope is handled
defensively (see :func:`_extract_token` and :func:`_extract_rows`) and
:func:`describe_columns` exists so you can inspect the real column set on your
first run rather than trusting a guess.
"""

import io
import json
import os
import time

from .config import (
    WHISP_BASE_URL,
    WHISP_GEOMETRY_LIMIT_ASYNC,
    WHISP_GEOMETRY_LIMIT_SYNC,
    WHISP_LOSS_COLUMNS,
    WHISP_MAX_BODY_KB,
    WHISP_RISK_COLUMN,
    WHISP_TIMEOUT_ASYNC_S,
)

# Candidate source names for each field we care about, best first. Whisp
# renames things between versions, so we look for any of these rather than
# hard-coding one.
COLUMN_ALIASES = {
    # user_id is what Whisp's own example file uses, so it is a real case.
    "plot_id": ["plot_id", "plotId", "PlotID", "user_id", "plot", "ID", "id", "geoid"],
    "area_ha": ["area_ha", "Area", "area"],
    "country": ["Country", "country", "ISO3", "iso3"],
    "unit": ["Unit", "unit"],
}


class WhispError(RuntimeError):
    """Raised for transport or API-level failures."""


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

def api_key_from_env():
    key = os.environ.get("WHISP_API_KEY")
    if not key:
        raise WhispError(
            "No Whisp API key. Set WHISP_API_KEY, or pass api_key=...\n"
            "Request one via https://whisp.openforis.org"
        )
    return key


def _headers(api_key):
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _requests():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise WhispError("The Whisp backend needs 'requests' (pip install requests)") from exc
    return requests


def get_config(base_url=WHISP_BASE_URL):
    """Live service limits and version. No API key needed."""
    requests = _requests()
    resp = requests.get(f"{base_url}/config", timeout=30)
    resp.raise_for_status()
    return resp.json()


def health(base_url=WHISP_BASE_URL):
    requests = _requests()
    return requests.get(f"{base_url}/health", timeout=30).json()


# --------------------------------------------------------------------------
# Submit / poll / download
# --------------------------------------------------------------------------

def _check_payload(geojson):
    features = geojson.get("features") or []
    count = len(features)
    if count == 0:
        raise WhispError("GeoJSON has no features.")
    if count > WHISP_GEOMETRY_LIMIT_ASYNC:
        raise WhispError(
            f"{count} geometries exceeds the Whisp per-job ceiling of "
            f"{WHISP_GEOMETRY_LIMIT_ASYNC}. Split into batches and concatenate "
            "the resulting tables."
        )
    size_kb = len(json.dumps(geojson).encode("utf-8")) / 1024
    if size_kb > WHISP_MAX_BODY_KB:
        raise WhispError(
            f"Request body is {size_kb:.0f} KB, over the {WHISP_MAX_BODY_KB} KB "
            "limit. Simplify geometries or split the batch."
        )
    return count


def _extract_token(payload):
    for key in ("token", "jobId", "job_id", "id", "taskId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_token(data)
    return None


def _extract_rows(payload):
    """Pull a list of per-plot dicts out of whatever envelope Whisp used."""
    if isinstance(payload, list):
        return payload
    for key in ("data", "results", "result", "rows", "plots"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_rows(value)
            if nested:
                return nested
    features = payload.get("features")
    if isinstance(features, list):
        return [
            {**(f.get("properties") or {}), "_geometry": f.get("geometry")}
            for f in features
        ]
    return []


def submit_geojson(geojson, api_key=None, analysis_options=None,
                   base_url=WHISP_BASE_URL):
    """POST a FeatureCollection. Returns ``(status_code, payload)``.

    200 means the results are inline (at or below the sync limit of 250
    geometries). 202 means queued, and the payload carries a token to poll.
    """
    requests = _requests()
    api_key = api_key or api_key_from_env()
    count = _check_payload(geojson)

    body = {"geojson": geojson}
    if analysis_options:
        body["analysisOptions"] = analysis_options

    timeout = 90 if count <= WHISP_GEOMETRY_LIMIT_SYNC else 30
    resp = requests.post(
        f"{base_url}/submit/geojson",
        headers=_headers(api_key),
        json=body,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise WhispError(f"Whisp submit failed [{resp.status_code}]: {resp.text[:500]}")
    return resp.status_code, resp.json()


def poll_status(token, api_key=None, base_url=WHISP_BASE_URL,
                interval=5, timeout=WHISP_TIMEOUT_ASYNC_S, verbose=True):
    """Poll ``/status/{token}`` until the job finishes or the timeout passes."""
    requests = _requests()
    api_key = api_key or api_key_from_env()
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = requests.get(
            f"{base_url}/status/{token}", headers=_headers(api_key), timeout=60
        )
        if resp.status_code >= 400:
            raise WhispError(f"Whisp status failed [{resp.status_code}]: {resp.text[:500]}")
        payload = resp.json()
        state = str(
            payload.get("status") or payload.get("state") or ""
        ).lower()
        if verbose:
            progress = payload.get("progress") or payload.get("percent") or ""
            print(f"  whisp job {token}: {state or 'running'} {progress}")
        if state in {"failed", "error", "cancelled", "canceled"}:
            raise WhispError(f"Whisp job {token} ended as '{state}': {payload}")
        if _extract_rows(payload):
            return payload
        if state in {"completed", "complete", "done", "success", "finished"}:
            return payload
        time.sleep(interval)

    raise WhispError(
        f"Whisp job {token} did not finish within {timeout}s. "
        f"Results may still arrive: GET {base_url}/download-csv/{token}"
    )


def download_csv(token, base_url=WHISP_BASE_URL):
    """CSV text for a finished job. This endpoint needs no API key."""
    requests = _requests()
    resp = requests.get(f"{base_url}/download-csv/{token}", timeout=120)
    resp.raise_for_status()
    return resp.text


def download_geojson(token, base_url=WHISP_BASE_URL):
    """GeoJSON output for a finished job. No API key needed."""
    requests = _requests()
    resp = requests.get(f"{base_url}/generate-geojson/{token}", timeout=120)
    resp.raise_for_status()
    return resp.json()


def analyse(geojson, api_key=None, analysis_options=None,
            base_url=WHISP_BASE_URL, prefer_csv=True, verbose=True):
    """Submit, wait, and return a raw Whisp DataFrame (its own columns)."""
    import pandas as pd

    status, payload = submit_geojson(
        geojson, api_key=api_key, analysis_options=analysis_options,
        base_url=base_url,
    )

    rows = _extract_rows(payload)
    if rows:
        if verbose:
            print(f"Whisp returned {len(rows)} rows synchronously.")
        return pd.DataFrame(rows)

    token = _extract_token(payload)
    if not token:
        raise WhispError(
            f"No results and no job token in the Whisp response: {payload}"
        )
    if verbose:
        print(f"Whisp queued the job (HTTP {status}), token {token}. Polling...")

    final = poll_status(token, api_key=api_key, base_url=base_url, verbose=verbose)
    rows = _extract_rows(final)
    if rows:
        return pd.DataFrame(rows)

    if prefer_csv:
        if verbose:
            print("Falling back to the CSV download endpoint.")
        return pd.read_csv(io.StringIO(download_csv(token, base_url=base_url)))

    return pd.DataFrame(_extract_rows(download_geojson(token, base_url=base_url)))


# --------------------------------------------------------------------------
# Adapter: Whisp columns -> our schema
# --------------------------------------------------------------------------

def describe_columns(df):
    """Sorted column list. Run this first: the real names beat any guess."""
    return sorted(str(c) for c in df.columns)


def _first_present(df, candidates):
    lookup = {str(c).lower(): c for c in df.columns}
    for name in candidates:
        hit = lookup.get(name.lower())
        if hit is not None:
            return hit
    return None


def to_risk_frame(df, risk_column=WHISP_RISK_COLUMN,
                  loss_columns=None, area_unit_hint="ha"):
    """Rename Whisp output into the schema :mod:`eudr_risk.scoring` expects.

    Only fields we can identify with confidence are mapped. Everything else is
    carried through untouched, so nothing is lost and nothing is invented.

    ``loss_columns`` (defaults to ``config.WHISP_LOSS_COLUMNS``) is summed into
    ``loss_pct``. It is empty by default on purpose: run
    :func:`describe_columns` against your own results and name the post-2020
    loss columns explicitly rather than letting the code guess.
    """
    import pandas as pd

    out = df.copy()
    out.columns = [str(c) for c in out.columns]

    renames = {}
    for target, candidates in COLUMN_ALIASES.items():
        source = _first_present(out, candidates)
        if source is not None and source != target:
            renames[source] = target
    out = out.rename(columns=renames)

    if "plot_id" not in out.columns:
        out["plot_id"] = [f"row-{i}" for i in range(len(out))]
    out["plot_id"] = out["plot_id"].astype(str)

    # Whisp reports the unit alongside the area; only trust hectares.
    if "area_ha" in out.columns:
        out["area_ha"] = pd.to_numeric(out["area_ha"], errors="coerce").fillna(0.0)
        if "unit" in out.columns:
            units = {str(u).strip().lower() for u in out["unit"].dropna().unique()}
            unexpected = units - {area_unit_hint, "ha", "hectare", "hectares", ""}
            if unexpected:
                print(
                    f"WARNING: Whisp reported area unit(s) {sorted(unexpected)}; "
                    "area_ha may not be hectares. Check before using."
                )
    else:
        out["area_ha"] = 0.0

    # Whisp's own EUDR-oriented verdict, kept verbatim. This is the
    # authoritative signal; our risk_score only orders the worklist.
    source_risk = _first_present(out, [risk_column])
    out["whisp_risk"] = out[source_risk] if source_risk else None

    cols = WHISP_LOSS_COLUMNS if loss_columns is None else loss_columns
    present = [c for c in cols if c in out.columns]
    if present:
        loss = sum(pd.to_numeric(out[c], errors="coerce").fillna(0.0) for c in present)
        out["loss_pct"] = loss
    elif cols:
        print(
            f"WARNING: none of the configured loss columns {list(cols)} are "
            "present. loss_pct left at 0; check describe_columns()."
        )

    # Signals that only the Earth Engine backend produces. Left absent so
    # scoring defaults them rather than implying a measurement we never made.
    return out
