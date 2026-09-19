"""Central configuration: EUDR constants, Earth Engine asset ids, scoring weights.

Keep every magic number here so the pipeline stays auditable. Asset versions move,
so verify them in the Earth Engine Data Catalog before a production run.
"""

# --- EUDR constants -------------------------------------------------------
# Regulation (EU) 2023/1115. Cutoff: commodities must not come from land
# deforested after 31 December 2020.
CUTOFF_DATE = "2020-12-31"
CUTOFF_YEAR = 2020

# RADD encodes alert dates as YYDOY (year + day of year). 21001 = 2021-01-01,
# the first day after the cutoff.
CUTOFF_YYDOY = 21001

# Geolocation rule: plots of MORE THAN 4 ha require a polygon; plots of
# 4 ha or less may be given as a single point.
EUDR_AREA_THRESHOLD_HA = 4.0

# Records must be kept for at least 5 years.
RECORD_RETENTION_YEARS = 5

# Country benchmarking, Commission Implementing Regulation (EU) 2025/1093,
# applicable 22 May 2025. Vietnam is classified LOW risk (simplified due
# diligence), but low risk does NOT remove the deforestation-free obligation,
# and circumvention risk still has to be assessed because Vietnam processes
# material imported from higher-risk origins.
COUNTRY = "VN"
COUNTRY_NAME = "Vietnam"
COUNTRY_EUDR_RISK = "low"
COUNTRY_BENCHMARK_SOURCE = "Commission Implementing Regulation (EU) 2025/1093"

# Coffee: HS heading 0901.
COMMODITY = "coffee"
HS_HEADING = "0901"

# --- Earth Engine assets --------------------------------------------------
# Official EUDR-aligned forest baseline for the year 2020 (JRC).
# Image, band "Map", pixel value 1 = Forest, 10 m resolution.
JRC_GFC2020_ASSET = "JRC/GFC2020/V3"
JRC_FOREST_BAND = "Map"
JRC_FOREST_VALUE = 1

# Hansen Global Forest Change: annual tree-cover loss. Detects CHANGE, which
# complements (does not replace) the JRC forest baseline.
HANSEN_ASSET = "UMD/hansen/global_forest_change_2024_v1_12"
FOREST_CANOPY_THRESHOLD = 30  # % canopy in 2000 that counts as forest

# RADD (RAdar for Detecting Deforestation, Wageningen/GFW). Sentinel-1 radar,
# 10 m, near real time. Radar sees through cloud, which matters in the
# Central Highlands wet season. ImageCollection filtered by metadata.
RADD_ASSET = "projects/radar-wur/raddalert/v1"
RADD_GEOGRAPHY = "sea"  # South East Asia tile set (covers Vietnam)
RADD_DATE_BAND = "Date"  # YYDOY encoding; verify with alerts.radd_band_names()

# World Database on Protected Areas. Used as the openly available legality
# proxy. Replace/augment with Vietnam's official 3-loai-rung zoning when you
# have it (see legality.add_user_legality_flag).
WDPA_ASSET = "WCMC/WDPA/current/polygons"

# Reduction scales (m)
SCALE_HANSEN = 30
SCALE_RADD = 10
SCALE_JRC = 30  # JRC is 10 m; 30 m is enough for a neighbourhood fraction
CONTEXT_BUFFER_M = 1000  # radius for "forest nearby" pressure indicator

# --- Risk scoring ---------------------------------------------------------
# Transparent weighted score. Weights sum to 1.0. Tune per supply chain, but
# document any change: this feeds a compliance decision.
DEFAULT_WEIGHTS = {
    "loss": 0.50,   # Hansen post-cutoff loss inside the plot (hard evidence)
    "radd": 0.30,   # recent radar alerts inside the plot (fresh evidence)
    "legal": 0.15,  # plot intersects a protected area (legality risk)
    "edge": 0.05,   # forest remaining nearby (clearing pressure, soft signal)
}

# Saturation points: value at which a component scores 1.0.
SATURATE_LOSS_PCT = 1.0     # 1% of plot area lost saturates the loss term
SATURATE_RADD_HA = 0.10     # 0.1 ha of alerts saturates the alert term

# Tier thresholds (rule-based, see scoring.assign_tier)
HIGH_LOSS_PCT = 0.5
HIGH_RADD_HA = 0.05
WATCH_FOREST_FRAC = 0.25

# --- Whisp (Open Foris / FAO) hosted API ----------------------------------
# Second data backend. Runs the analysis server-side using FAO's "convergence
# of evidence" approach, so it needs only an API key rather than your own
# registered Earth Engine project.
#
# Note: the `openforis-whisp` PyPI package is NOT an Earth Engine workaround,
# it requires a registered GEE project too. Only the hosted API avoids that.
#
# Limits below were read from GET https://whisp.openforis.org/api/config
# (Whisp 3.0.0a17, September 2026). Re-check with whisp.get_config().
WHISP_BASE_URL = "https://whisp.openforis.org/api"
WHISP_GEOMETRY_LIMIT_SYNC = 250    # at or below this, results come back inline
WHISP_GEOMETRY_LIMIT_ASYNC = 5000  # hard ceiling for one job
WHISP_TIMEOUT_SYNC_S = 60
WHISP_TIMEOUT_ASYNC_S = 600
WHISP_MAX_BODY_KB = 10240
WHISP_VERSION_SEEN = "3.0.0a17"

# Published rate limits, per API key. Defaults, and FAO notes they may change.
WHISP_RATE_LIMIT_REQUESTS = 30
WHISP_RATE_LIMIT_WINDOW_S = 60
WHISP_MAX_CONCURRENT_JOBS = 2

# Get a key by registering at https://whisp.openforis.org/login and generating
# one on your account page. Free. Note that an SSO (Keycloak) access token is
# NOT accepted by the API: you need the generated key.
#
# Licensing contrast worth remembering: Whisp is MIT and explicitly permits
# commercial use, unlike Earth Engine's noncommercial tier. At a 16,000-farm
# scale the shared public API's rate limits still apply, so either coordinate
# with FAO or self-host, which the licence allows.

# Column names below were read off a real Whisp 3.0.0a17 response (257
# columns) rather than guessed. See tests/fixtures/whisp_result_sample.csv.
#
# Whisp's risk column depends on commodity. Coffee is a perennial crop, so
# coffee uses risk_pcrop. Note the lowercase: the web table displays
# "RISK_PCROP" only because of CSS, the CSV header is lowercase.
WHISP_RISK_COLUMN = "risk_pcrop"
WHISP_RISK_COLUMNS_ALL = ["risk_pcrop", "risk_acrop", "risk_timber"]

# Whisp's own verdict vocabulary. Only "low" has been observed directly; the
# other two are inferred from the three-colour breakdown in its UI. Anything
# unrecognised maps to "standard" rather than "low", so a vocabulary change
# fails safe instead of silently clearing plots.
WHISP_TIER_MAP = {
    "low": "low",
    "more_info_needed": "standard",
    "moreinfoneeded": "standard",
    "medium": "standard",
    "standard": "standard",
    "high": "high",
}
WHISP_TIER_FALLBACK = "standard"

# Post-cutoff disturbance, in hectares, one column per source dataset.
WHISP_LOSS_COLUMNS = [
    "TMF_def_after_2020",
    "TMF_deg_after_2020",
    "GFC_loss_after_2020",
    "GLAD-L_after_2020",
    "GLAD-S2_after_2020",
]

# These datasets detect the same clearing events, so summing them
# double-counts. Take the largest single estimate instead.
WHISP_LOSS_AGGREGATION = "max"  # "max" or "sum"

# Whisp reports RADD separately, which is the same measurement the Earth
# Engine backend computes itself.
WHISP_RADD_COLUMN = "RADD_after_2020"

# Whisp's own yes/no indicator for post-cutoff disturbance. This is the one
# that matters for EUDR, because Whisp gates it on whether the land was
# forest at the cutoff, which raw loss hectares do not.
WHISP_INDICATOR_AFTER_2020 = "Ind_04_disturbance_after_2020"

# Coffee presence, useful for sanity-checking that a plot really is coffee.
WHISP_COMMODITY_COLUMN = "Coffee_FDaP"

# Whisp assigns its own sequential plotId (1, 2, 3...) and does not carry your
# feature properties through. Name the property holding your own id here and
# it is sent as analysisOptions.externalIdColumn; Whisp then fills the
# external_id output column, giving a real join key instead of relying on row
# order. Upstream has had trouble with this (whisp issue #257), so the adapter
# falls back to Whisp's internal plotId whenever external_id comes back blank.
WHISP_EXTERNAL_ID_COLUMN = "plot_id"   # property name in YOUR input GeoJSON
WHISP_EXTERNAL_ID_FIELD = "external_id"  # column name in Whisp's OUTPUT

# analysisOptions.unitType: ask for hectares explicitly rather than hoping.
WHISP_UNIT_TYPE = "ha"
