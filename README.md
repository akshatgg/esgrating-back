# esgratings-api

FastAPI service for esgratings.co.in. Serves both scoring pipelines — the ESG
pipeline ported from `esg_score_calculator-master` and the BFSI pipeline
ported from the legacy PHP calculator — plus submissions, admin auth,
ratings and mail, all on MongoDB.

Spec: `../docs/specs/2026-09-10-esgratings-rebuild-design.md`

## Setup

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and a MongoDB
instance reachable on `27017`.

```bash
cp .env.example .env   # fill in real secrets — .env is gitignored
uv sync
./dev_start.sh          # starts the API on http://localhost:8000
./dev_stop.sh            # stops it
```

`dev_start.sh` will try to start MongoDB via `brew services` if it isn't
already reachable on port 27017, then boots uvicorn with `--reload` in the
background, logging to `.dev/api.log` and tracking its pid in `.dev/api.pid`.

## Tests

```bash
uv run pytest
```

Every test runs offline: MongoDB is faked with `mongomock`, uploads go to a
`tmp_path` fixture, and no real OpenAI calls are made.

## Scripts

- `scripts/migrate_mysql.py` — one-off migration of legacy MySQL data.
- `scripts/create_admin.py` — create an admin user for the auth system.

## Docker

```bash
docker build -t esgratings-api .
docker run -p 8000:8000 --env-file .env esgratings-api
```
