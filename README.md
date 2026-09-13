# fantasy-football-draft-optimizer

## Running it

Install dependencies:

```
uv sync
```

Run the tests:

```
uv run pytest
```

Start the server for draft day:

```
uv run uvicorn ffdo.api.app:app --port 8000
```

Then open `http://localhost:8000` in a browser and connect a provider
(Sleeper username, or ESPN `espn_s2` / `SWID` cookies), then pick which
leagues to track.

For a zero-config dev league, run `uv run python scripts/seed_dev_league.py`
to track the pinned 2026 auction league into `data/ffdo.db`.
