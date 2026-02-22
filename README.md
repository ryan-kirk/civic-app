# CivicWatch

Canonical project specification and roadmap live in `AGENTS.md`.

- Runtime entrypoint: `uvicorn app.main:app --reload`
- API docs: `http://127.0.0.1:8000/docs`
- Tests: `./venv/bin/pytest -q`

## Fly.io Beta Deploy (Recommended)

This app is currently designed for a **single instance** beta deployment:

- SQLite database (local file)
- in-memory ingest job tracker
- local cached/raw artifacts

Use a Fly volume and keep machine count at `1`.

Dependency note:

- Docker/Fly installs runtime packages from `requirements.txt`
- Keep `requirements.txt` and `pyproject.toml` dependency pins in sync
- Treat `requirements.txt` as the deployment runtime source of truth

### Files Added For Deploy

- `Dockerfile`
- `fly.toml`
- `.env.example`

### First-Time Setup

1. Install and authenticate Fly CLI:

```bash
brew install flyctl
fly auth login
```

2. Create the Fly app (or edit `fly.toml` first and use your preferred app name):

```bash
fly launch --no-deploy
```

3. Create a persistent volume for SQLite:

```bash
fly volumes create civicwatch_data --region ord --size 10
```

Adjust region/size as needed. `10GB` is a good beta starting point.

4. (Optional) Set secrets/env overrides:

```bash
fly secrets set CIVICWEB_BASE_URL=https://urbandale.civicweb.net
```

5. Deploy:

```bash
fly deploy
```

### Operational Notes (Beta)

- Keep `count = 1` machine (SQLite + in-memory jobs are not multi-instance safe yet)
- Do not enable autoscaling yet
- The app stores SQLite at `/data/civicwatch.db` on the mounted Fly volume
- Ingest endpoints are lightly throttled server-side for beta safety

### Updating the App

```bash
fly deploy
```

### Checking Health / Logs

```bash
fly status
fly logs
```
