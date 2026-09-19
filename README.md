# Agro_Tech

Technical tooling for the regenerative coffee work in Lâm Đồng (Di Linh),
Vietnam. The farmer-facing agronomy documents live separately in
`Agro_Project`; this repo holds the code.

## Projects

| Folder | What it does |
|---|---|
| [`eudr-coffee-risk/`](eudr-coffee-risk/) | EUDR deforestation-risk screening for coffee plots. Plot polygons in, per-plot risk tier and a due-diligence bundle out. Google Earth Engine, open datasets, $0 running cost. |

## Conventions

- Python, no framework. Each project is self-contained with its own
  `requirements.txt` and README.
- Configuration and magic numbers go in one `config.py` per project, so a
  reviewer can audit the assumptions in one file.
- **Never commit** real farmer plot boundaries, personal data, or Earth Engine
  / service-account credentials. The root `.gitignore` blocks the usual paths;
  check `git status` before a first push anyway.
