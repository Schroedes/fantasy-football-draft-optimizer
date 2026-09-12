# fantasy-football-draft-optimizer

Once a league's draft is complete, its page becomes the **season view** —
a luck-adjusted power ranking (overall and by position, starters or full
roster), your roster with rest-of-season / dynasty values, the league
standings, and — for Sleeper dynasty/keeper leagues — a draft-capital
ranking.
The season screen's **Lineup** tab shows this week's optimal starting
lineup against what you actually have set, lock-aware (a player whose
game has already started is never suggested as a swap).

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
