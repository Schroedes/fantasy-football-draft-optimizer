# fantasy-football-draft-optimizer

Once a league's draft is complete, its page becomes the **season view** —
a luck-adjusted power ranking (overall and by position, starters or full
roster), your roster with rest-of-season / dynasty values, the league
standings, and — for Sleeper dynasty/keeper leagues — a draft-capital
ranking.
The season screen's **Lineup** tab shows this week's optimal starting
lineup against what you actually have set, lock-aware (a player whose
game has already started is never suggested as a swap).

Dynasty values are a real, multi-year model: this week's projected value
is projected forward using an age curve fit from real historical Sleeper
data (not a hand-authored guess), discounted for both time and
injury/durability risk, and collapsed back into a single season-scale
number.

The **Trades** tab shows every real trade in the league (not just yours),
each with its value at the moment it happened and how that trade has
played out since -- draft picks included, valued from real historical
rookie-draft outcomes rather than a guessed chart. A **Propose a trade**
button opens the Trade Machine, a what-if calculator: pick players and
future picks from your roster and any other team's, and the value/
differential updates live as you go. Nothing built there is saved --
only real, completed trades ever show up in the ledger above it.

The **Waivers** tab (FAAB leagues only) recommends free-agent adds, which
bench player to drop for each, and how much to bid -- respecting roster
construction (it won't suggest stockpiling QBs or TEs beyond what you can
usefully start), with bid amounts fit from your own leagues' real
historical waiver-claim outcomes.

The **Scorecard** tab shows how good FFDO's own recommendations have
actually been -- weeks your lineup rec was followed and points left on the
bench when it wasn't, how your trades have played out since, your FAAB
claim win rate and bid efficiency, and a grade breakdown for your own
draft picks.

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
