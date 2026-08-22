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

Then open `http://localhost:8000` in a browser.

By default the board points at the pinned 2026 auction league/draft. To
point it elsewhere (e.g. a snake league), set `FFDO_LEAGUE_ID` /
`FFDO_DRAFT_ID` before starting the server; set `FFDO_ROSTER_ID` to see
your own max-bid and budget numbers instead of a fresh-roster estimate.
