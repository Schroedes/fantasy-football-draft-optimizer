# Roster Needs & Trade Depth-Impact Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Every subagent dispatched for this plan (implementer, task reviewer, final reviewer) MUST be pinned to the Sonnet model explicitly — never let one fall through to a more expensive default.**

**Goal:** Give the Trade Machine a live "roster needs" preview so a trade that looks good on raw value doesn't silently gut either side's positional depth.

**Architecture:** A new pure-engine module (`engine/roster_needs.py`) scores each of QB/RB/WR/TE by blending league-wide starter-strength rank with a bench-depth thinness measure that's aware of what each league's own roster construction actually expects (reusing sub-project #6's `POSITION_CAP_EXTRA`). `POST /trade/evaluate` (already live-called by the Trade Machine on every debounced update) gains `needs_before`/`needs_after` fields for both sides of the trade, computed by substituting a hypothetical post-trade roster into the existing league-wide ranking while every other team stays frozen at its real, current value.

**Tech Stack:** Python 3.12, FastAPI, pytest, vanilla JS/CSS (season.js/season.css, no framework).

**Spec:** `docs/superpowers/specs/2026-09-13-roster-needs-design.md`

## Global Constraints

- Thinness must never penalize a position the league's own construction doesn't expect bench depth for (DEF/K, `POSITION_CAP_EXTRA` = 0) — this was the one point the user required a revision on during brainstorming.
- The "after" computation for one side of a trade keeps every OTHER team frozen at its real, current value — no full league-wide re-simulation. This applies independently to `needs_after.you` and `needs_after.partner`: each freezes the other side at its pre-trade value, never both hypothetically changed at once.
- Draft picks never affect need scores — they aren't rostered players, so they never change a position's player count either direction.
- No new endpoint: `needs_before`/`needs_after` are added fields on the existing `POST /trade/evaluate` response, since the Trade Machine already calls it live on every selection change.
- All subagents dispatched to execute this plan (implementer, task reviewer, final reviewer, any fix-round dispatch) use the Sonnet model explicitly.

---

### Task 1: Make `power_ranking.py`'s team-value calculation public

**Files:**
- Modify: `src/ffdo/engine/power_ranking.py`
- Modify: `src/ffdo/api/app.py:1478` (comment-only reference, update to match)

**Interfaces:**
- Consumes: nothing new.
- Produces: `power_ranking.team_value(entry, valued, league, *, position, scope) -> tuple[float, float]` (renamed from the existing private `_team_value`, identical behavior). Task 2 imports and calls this indirectly via `power_ranking.rank`, not directly — this task exists so `rank()`'s existing behavior is provably unchanged, not because Task 2 calls `team_value` itself.

This is a pure rename, no behavior change. `grep -rn "_team_value" src/ tests/` (already run during planning) confirms exactly 3 references: the definition, its one call site inside `rank()`, and a comment in `app.py` that names it for context — no test file imports or calls it directly.

- [ ] **Step 1: Rename the function and its one call site**

In `src/ffdo/engine/power_ranking.py`, change:
```python
def _team_value(
```
to:
```python
def team_value(
```

And change the one call site inside `rank()`:
```python
        value, bench = _team_value(entry, valued, league, position=position, scope=scope)
```
to:
```python
        value, bench = team_value(entry, valued, league, position=position, scope=scope)
```

- [ ] **Step 2: Update the one comment reference in app.py**

Find this comment (around line 1478):
```python
        # another team's player. Same scoping `power_ranking._team_value`
```
Change it to:
```python
        # another team's player. Same scoping `power_ranking.team_value`
```

- [ ] **Step 3: Run the existing power_ranking test suite to confirm no behavior change**

Run: `uv run pytest tests/engine/test_power_ranking.py -v`
Expected: PASS, every test unchanged, same count as before this edit.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: same pass count as before this task (this is a pure rename — confirm nothing else referenced the old private name).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/power_ranking.py src/ffdo/api/app.py
git commit -m "refactor: power_ranking.team_value -- make public, a second real caller is coming"
```

---

### Task 2: `engine/roster_needs.py` — need scoring

**Files:**
- Create: `src/ffdo/engine/roster_needs.py`
- Test: `tests/engine/test_roster_needs.py`

**Interfaces:**
- Consumes: `power_ranking.rank` and `power_ranking.team_value` (existing, Task 1 makes the latter public but this task only calls `rank`), `waiver_value.position_cap` (existing, from sub-project #6), `replacement.FLEX_ELIGIBILITY` (existing).
- Produces: `NeedScore` (frozen dataclass: `rank: int, severity: str`), `position_needs(entry, all_rosters, valued, league) -> dict[str, NeedScore]` (keyed `"QB"`/`"RB"`/`"WR"`/`"TE"`). Task 3 calls `position_needs` directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_roster_needs.py
from dataclasses import replace

import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, RosterEntry, ValuedPlayer
from ffdo.engine import roster_needs


def _league(roster_positions, num_teams=2):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=roster_positions, scoring_settings={}, budget=200)


def _vp(pid, pos, v):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=26, years_exp=4, injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=v, adjusted_points=v,
                        vor=v, tier=1, adjustments={})


def _entry(rid, players):
    return RosterEntry(roster_id=rid, team_name=f"T{rid}", player_ids=tuple(players),
                       starter_ids=(), wins=0, losses=0, ties=0,
                       points_for=0.0, points_against=0.0)


# ---- _weakness: pure rank normalization ----

def test_weakness_best_team_is_zero():
    assert roster_needs._weakness(1, 4) == 0.0


def test_weakness_worst_team_is_one():
    assert roster_needs._weakness(4, 4) == 1.0


def test_weakness_middle_rank_is_fractional():
    assert roster_needs._weakness(2, 4) == pytest.approx(1 / 3)


def test_weakness_single_team_league_is_zero():
    assert roster_needs._weakness(1, 1) == 0.0


# ---- _thinness: the part the user required a revision on ----

def test_thinness_def_never_penalized_for_meeting_starters_only():
    """DEF's POSITION_CAP_EXTRA is 0 -- this league expects zero bench
    depth at DEF, so having exactly enough to start (and nothing behind
    it) must never read as thin."""
    league = _league(("QB", "RB", "WR", "TE", "DEF", "K", "BN"))
    assert roster_needs._thinness("DEF", 1, league) == 0.0


def test_thinness_def_penalized_only_for_missing_a_starter():
    league = _league(("QB", "RB", "WR", "TE", "DEF", "K", "BN"))
    assert roster_needs._thinness("DEF", 0, league) == 1.0


def test_thinness_te_partial_cap_scales_between_starting_reach_and_cap():
    # TE: dedicated=1, flex_eligible=1 (TE reachable via FLEX) -> starting_reach=2.
    # POSITION_CAP_EXTRA["TE"]=1 -> cap=3.
    league = _league(("QB", "RB", "WR", "TE", "FLEX", "DEF", "K", "BN", "BN"))
    assert roster_needs._thinness("TE", 2, league) == 1.0   # exactly starting_reach, no cushion
    assert roster_needs._thinness("TE", 3, league) == 0.0   # at the cap, fully deep
    assert roster_needs._thinness("TE", 1, league) == 1.0   # below starting_reach, clamped at 1.0


def test_thinness_rb_uncapped_decays_smoothly_with_each_extra_player():
    # RB: dedicated=2, flex_eligible=1 (RB reachable via FLEX) -> starting_reach=3.
    # RB is not in POSITION_CAP_EXTRA -- uncapped, no fixed "ideal" bench count.
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "DEF", "K", "BN", "BN", "BN"))
    assert roster_needs._thinness("RB", 3, league) == 1.0              # extra=0
    assert roster_needs._thinness("RB", 4, league) == 0.5              # extra=1
    assert roster_needs._thinness("RB", 5, league) == pytest.approx(1 / 3)  # extra=2


# ---- _severity: bucket thresholds ----

def test_severity_buckets():
    assert roster_needs._severity(0.0) == "Fine"
    assert roster_needs._severity(0.32) == "Fine"
    assert roster_needs._severity(0.33) == "Moderate"
    assert roster_needs._severity(0.65) == "Moderate"
    assert roster_needs._severity(0.66) == "Severe"
    assert roster_needs._severity(1.0) == "Severe"


# ---- position_needs: the full, integration-shaped function ----

_LEAGUE = _league(("QB", "RB", "WR", "TE", "FLEX", "BN", "BN"), num_teams=2)
_VALUED = {
    "q1": _vp("q1", "QB", 30), "r1": _vp("r1", "RB", 25), "w1": _vp("w1", "WR", 20), "t1": _vp("t1", "TE", 10),
    "q2": _vp("q2", "QB", 15), "r2": _vp("r2", "RB", 12), "w2": _vp("w2", "WR", 8), "t2": _vp("t2", "TE", 5),
    "q3": _vp("q3", "QB", 5),
}
_ROSTERS = [
    _entry(1, ["q1", "r1", "w1", "t1"]),
    _entry(2, ["q2", "r2", "w2", "t2"]),
]


def test_position_needs_returns_rank_and_severity_for_all_four_positions():
    result = roster_needs.position_needs(_ROSTERS[0], _ROSTERS, _VALUED, _LEAGUE)
    assert set(result) == {"QB", "RB", "WR", "TE"}
    assert result["QB"].rank == 1   # team1's QB (30) beats team2's (15)
    for need in result.values():
        assert need.severity in ("Fine", "Moderate", "Severe")


def test_position_needs_scores_a_hypothetical_roster_with_other_teams_frozen():
    # Hypothetical: team1 trades away its strong QB (q1, 30) for a much
    # weaker one (q3, 5) -- team2's real, untouched QB (q2, 15) now beats it.
    hypothetical = replace(_ROSTERS[0], player_ids=("q3", "r1", "w1", "t1"))
    result = roster_needs.position_needs(hypothetical, _ROSTERS, _VALUED, _LEAGUE)
    assert result["QB"].rank == 2
    assert result["QB"].severity != "Fine"
    # A second, independent call using the ORIGINAL (unmodified) _ROSTERS
    # list must read team1's REAL QB value (q1, 30), not leftover state
    # from the hypothetical (q3, 5) used above -- team1's real 30 beats
    # team2's real 15, so team2's genuine rank is 2. If Call A's
    # substitution had somehow leaked into this fresh call, team2 would
    # wrongly show rank 1 instead.
    real_team2_result = roster_needs.position_needs(_ROSTERS[1], _ROSTERS, _VALUED, _LEAGUE)
    assert real_team2_result["QB"].rank == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_roster_needs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.roster_needs'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/engine/roster_needs.py
"""How urgently a team needs help at each position -- QB/RB/WR/TE, blending
two signals into one severity score: how weak the team's starters are at
that position league-wide (reusing power_ranking's existing ranking), and
how thin their bench depth is there RELATIVE TO WHAT THIS LEAGUE'S OWN
ROSTER CONSTRUCTION ACTUALLY EXPECTS -- reusing sub-project #6's
POSITION_CAP_EXTRA (waiver_value.position_cap), so a league with little
bench room anywhere never reads DEF/K as "thin" just because nobody
carries a backup there.

Used by api.app's POST /trade/evaluate to show how a hypothetical trade
would change either side's needs -- see position_needs's docstring for how
a hypothetical (post-trade) roster gets scored without touching any other
team's real, current standing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ffdo.domain.models import RosterEntry, ValuedPlayer
from ffdo.engine import power_ranking
from ffdo.engine.replacement import FLEX_ELIGIBILITY
from ffdo.engine.waiver_value import position_cap

_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True, slots=True)
class NeedScore:
    rank: int
    severity: str  # "Severe" | "Moderate" | "Fine"


def _weakness(rank: int, num_teams: int) -> float:
    if num_teams <= 1:
        return 0.0
    return (rank - 1) / (num_teams - 1)


def _thinness(position: str, your_count: int, league) -> float:
    dedicated = sum(1 for s in league.roster_positions if s == position)
    flex_eligible = sum(
        1 for s in league.roster_positions
        if s in FLEX_ELIGIBILITY and position in FLEX_ELIGIBILITY[s])
    starting_reach = dedicated + flex_eligible
    cap = position_cap(position, league)
    if cap is not None:
        if cap <= starting_reach:
            # This league expects zero bench depth here (e.g. DEF/K) --
            # never penalize for lacking a backup nobody expects you to
            # roster.
            return 0.0 if your_count >= starting_reach else 1.0
        return max(0.0, min(1.0, 1.0 - (your_count - starting_reach) / (cap - starting_reach)))
    # No fixed "ideal" bench count exists for an uncapped position (RB/WR)
    # -- thinness decays smoothly with each extra player instead of
    # demanding a specific target.
    extra = max(0, your_count - starting_reach)
    return 1.0 / (1.0 + extra)


def _severity(score: float) -> str:
    if score >= 0.66:
        return "Severe"
    if score >= 0.33:
        return "Moderate"
    return "Fine"


def position_needs(
    entry: RosterEntry,
    all_rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[str, NeedScore]:
    """`entry` may be a real, unmodified RosterEntry (to score a team's
    current needs) or a hypothetical one built via `dataclasses.replace`
    with a different `player_ids` set (to score a "what if" roster). Either
    way, `entry` is substituted into `all_rosters` at its own `roster_id`
    before ranking -- reusing `power_ranking.rank` directly rather than
    re-deriving its sort -- so every OTHER team's value stays frozen at its
    real, current state. `entry.roster_id` must already exist somewhere in
    `all_rosters`; every caller passes a roster list that includes the team
    being scored.
    """
    substituted = [entry if r.roster_id == entry.roster_id else r for r in all_rosters]
    num_teams = len(substituted)
    out: dict[str, NeedScore] = {}
    for position in _POSITIONS:
        rows = power_ranking.rank(
            substituted, valued, league, {}, None, position=position, scope="starters")
        rank = next(r.power_rank for r in rows if r.roster_id == entry.roster_id)
        your_count = sum(
            1 for pid in entry.player_ids
            if pid in valued and valued[pid].profile.position == position)
        score = 0.5 * _weakness(rank, num_teams) + 0.5 * _thinness(position, your_count, league)
        out[position] = NeedScore(rank=rank, severity=_severity(score))
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_roster_needs.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/roster_needs.py tests/engine/test_roster_needs.py
git commit -m "feat: engine.roster_needs -- league-aware weakness + thinness need scoring"
```

---

### Task 3: Wire `needs_before`/`needs_after` into `POST /trade/evaluate`

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_trade_endpoints.py`

**Interfaces:**
- Consumes: `roster_needs.position_needs` (Task 2).
- Produces: nothing consumed by a later task in this plan (Task 4 is the frontend consumer of this endpoint's new response fields).

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_trade_endpoints.py` (the file already imports `_ROSTERS`, `_STATE`, `_USERS`, `_PLAYERS`, `_PROJ`, `_MATCHUPS`, `_tracked` from `tests.api.test_season_endpoint` -- reuse them, do not redefine):

```python
def test_trade_evaluate_includes_needs_before_and_after_for_both_sides(monkeypatch, tmp_path):
    """_ROSTERS (from test_season_endpoint) has roster 1 (you, roster_id=1
    via _tracked's default) with one RB (p_rb) and roster 2 with one RB
    (p_rb2). _PROJ gives p_rb clearly higher raw production than p_rb2 on
    every stat (1100 rush_yd/9 TD/40 rec/300 rec_yd vs p_rb2's 900/6/30/200)
    -- so team 1's RB ranks 1st before this trade. This trade gives away
    your only RB for nothing in return, so afterward you have zero RBs and
    must rank 2nd (worst) at RB -- a real, derivable rank change, not a
    coincidence of arbitrary fixture numbers."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    body = {"partner_roster_id": 2,
            "side_a": {"player_ids": ["p_rb"], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()

    assert set(data["needs_before"]) == {"you", "partner"}
    assert set(data["needs_after"]) == {"you", "partner"}
    for side in ("you", "partner"):
        for pos in ("QB", "RB", "WR", "TE"):
            assert pos in data["needs_before"][side]
            assert "rank" in data["needs_before"][side][pos]
            assert "severity" in data["needs_before"][side][pos]
            assert pos in data["needs_after"][side]

    assert data["needs_before"]["you"]["RB"]["rank"] == 1
    assert data["needs_after"]["you"]["RB"]["rank"] == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_trade_endpoints.py -k needs_before_and_after -v`
Expected: FAIL with `KeyError: 'needs_before'`

- [ ] **Step 3: Write the implementation**

In `src/ffdo/api/app.py`, add the import near the other engine imports:

```python
from ffdo.engine import roster_needs as roster_needs_mod
```

Find `evaluate_trade_endpoint`'s current tail:
```python
        result = trade_value_mod.evaluate_trade(
            {"player_ids": side_a.get("player_ids", []),
             "picks": _picks_from_payload(side_a.get("picks", []))},
            {"player_ids": side_b.get("player_ids", []),
             "picks": _picks_from_payload(side_b.get("picks", []))},
            valued_players=valued, pick_curve=PICK_VALUE_CURVE,
            current_season=lg.season, round_size=lg.num_teams)
        return {
            "side_a_value": round(result.side_a_value, 1),
            "side_b_value": round(result.side_b_value, 1),
            "differential": round(result.differential, 1),
            "differential_pct": (round(result.differential_pct, 3)
                                 if result.differential_pct is not None else None),
        }
```

Replace it with:
```python
        result = trade_value_mod.evaluate_trade(
            {"player_ids": side_a.get("player_ids", []),
             "picks": _picks_from_payload(side_a.get("picks", []))},
            {"player_ids": side_b.get("player_ids", []),
             "picks": _picks_from_payload(side_b.get("picks", []))},
            valued_players=valued, pick_curve=PICK_VALUE_CURVE,
            current_season=lg.season, round_size=lg.num_teams)

        response = {
            "side_a_value": round(result.side_a_value, 1),
            "side_b_value": round(result.side_b_value, 1),
            "differential": round(result.differential, 1),
            "differential_pct": (round(result.differential_pct, 3)
                                 if result.differential_pct is not None else None),
        }

        # Roster-needs depth-impact preview: only computable when both a
        # real partner_roster_id and both rosters are known -- degrades to
        # simply omitting these fields rather than 400ing, since the value
        # comparison above is still useful on its own (e.g. mid-selection,
        # before a partner is fully resolved).
        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        partner_roster = next(
            (r for r in rosters if r.roster_id == payload.get("partner_roster_id")), None)
        if you_roster is not None and partner_roster is not None:
            side_a_ids = set(side_a.get("player_ids", []))
            side_b_ids = set(side_b.get("player_ids", []))
            you_after = replace(you_roster, player_ids=tuple(
                (set(you_roster.player_ids) - side_a_ids) | side_b_ids))
            partner_after = replace(partner_roster, player_ids=tuple(
                (set(partner_roster.player_ids) - side_b_ids) | side_a_ids))

            def _needs_json(entry) -> dict:
                return {pos: {"rank": n.rank, "severity": n.severity}
                       for pos, n in roster_needs_mod.position_needs(
                           entry, rosters, valued, lg).items()}

            response["needs_before"] = {
                "you": _needs_json(you_roster), "partner": _needs_json(partner_roster)}
            response["needs_after"] = {
                "you": _needs_json(you_after), "partner": _needs_json(partner_after)}

        return response
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_trade_endpoints.py -v`
Expected: PASS (all existing trade-endpoint tests plus the new one)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: every prior test still passes, plus everything new from Tasks 1-3

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_trade_endpoints.py
git commit -m "feat: wire needs_before/needs_after into POST /trade/evaluate"
```

---

### Task 4: Trade Machine needs-impact panel + README

**Files:**
- Modify: `src/ffdo/web/season/season.js`
- Modify: `src/ffdo/web/season/season.css`
- Modify: `README.md`

**Interfaces:**
- Consumes: `POST /trade/evaluate`'s new `needs_before`/`needs_after` fields (Task 3).
- Produces: nothing consumed by a later task -- this is the last task in this plan.

- [ ] **Step 1: Add `tbNeedsHTML()` to season.js**

Find `tbScoreboardHTML` (currently ends around line 853 with `}`, immediately followed by `function renderTradeBuilderModal() {`). Add a new function directly after `tbScoreboardHTML`'s closing brace and before `renderTradeBuilderModal`:

```javascript
function tbNeedsHTML() {
  if (!_tradeBuilderEval || _tradeBuilderEval.error || !_tradeBuilderEval.needs_before) {
    return "";
  }
  const positions = ["QB", "RB", "WR", "TE"];
  const sideRows = (side) => positions.map(pos => {
    const before = _tradeBuilderEval.needs_before[side][pos];
    const after = _tradeBuilderEval.needs_after[side][pos];
    const order = { Fine: 0, Moderate: 1, Severe: 2 };
    let deltaClass = "";
    if (order[after.severity] > order[before.severity]) deltaClass = "loss";
    else if (order[after.severity] < order[before.severity]) deltaClass = "gain";
    return `<div class="tb-need-row">
      <span class="tb-need-pos">${pos}</span>
      <span class="tb-need-delta ${deltaClass}">${ordinalSuffix(before.rank)} &rarr; ${ordinalSuffix(after.rank)}
        (${escapeHtml(before.severity)} &rarr; ${escapeHtml(after.severity)})</span>
    </div>`;
  }).join("");

  return `
    <div class="tb-needs">
      <p class="tb-section-label">Roster needs impact</p>
      <div class="tb-needs-cols">
        <div class="tb-needs-col">
          <p class="tb-side-label">Your team</p>
          ${sideRows("you")}
        </div>
        <div class="tb-needs-col">
          <p class="tb-side-label">Partner</p>
          ${sideRows("partner")}
        </div>
      </div>
    </div>`;
}

function ordinalSuffix(n) {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}
```

Note: this file already has an `ordinal(n)` function (used by the your-roster panel) -- `ordinalSuffix` is a deliberately separate, identically-shaped function rather than reusing `ordinal`, because `ordinal(null)` returns `"—"` (a null-safe convenience the your-roster panel needs and this one doesn't; a need score's `rank` is always a real number, never null). Do not merge them.

- [ ] **Step 2: Splice the needs panel into the modal**

Find in `renderTradeBuilderModal`:
```javascript
      ${tbScoreboardHTML()}
      <div class="tb-foot">
```

Replace with:
```javascript
      ${tbScoreboardHTML()}
      ${tbNeedsHTML()}
      <div class="tb-foot">
```

- [ ] **Step 3: Add CSS**

In `src/ffdo/web/season/season.css`, add after the `.tb-sb-verdict` rule (in the trade builder modal section):

```css
.tb-needs { padding: 16px 28px 4px; }
.tb-needs-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 8px; }
.tb-need-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.tb-need-row:last-child { border-bottom: none; }
.tb-need-pos {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--muted);
  width: 28px;
  flex-shrink: 0;
}
.tb-need-delta { color: var(--faint); }
.tb-need-delta.gain { color: var(--green); }
.tb-need-delta.loss { color: var(--red); }

@media (max-width: 640px) {
  .tb-needs-cols { grid-template-columns: 1fr; gap: 10px; }
}
```

- [ ] **Step 4: Manually verify in a browser**

Start the dev server (`ffdo-api` from `.claude/launch.json`), open a real tracked league's season screen, go to the Trades tab, click "Propose a trade," select at least one player on each side, and confirm the "Roster needs impact" panel appears below the value scoreboard showing rank/severity deltas for both Your team and Partner columns, updating live as selections change. Test a case where a position clearly gets thinner (e.g. give away your only player at a position) and confirm the delta renders in red (loss); test the reverse (receive a player at a thin position) and confirm green (gain).

- [ ] **Step 5: Document in the README**

In `README.md`, the Trade Machine paragraph (added earlier this session) currently ends `... only real, completed trades ever show up in the ledger above it.` Add a sentence directly after it:

```markdown
It also shows how the trade would change either side's roster needs --
positional rank and depth severity before and after -- so a trade that
looks good on value alone doesn't quietly leave you thin at a position,
or hand your trade partner a rank jump you didn't mean to give them.
```

- [ ] **Step 6: Run the full test suite one more time**

Run: `uv run pytest -q`
Expected: all tests still pass (this task touches no Python)

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/web/season/season.js src/ffdo/web/season/season.css README.md
git commit -m "feat: Trade Machine roster-needs impact panel"
```

---

## Final Review Checklist (for the controller, not a task)

1. Confirm Task 1's rename left no other `_team_value` reference anywhere (`grep -rn "_team_value" src/ tests/` should return nothing).
2. Hand-verify at least one of Task 2's `_thinness` test cases against the formula by re-deriving it independently, per this project's established pattern of checking curve/formula tasks for real magnitude, not just direction.
3. Confirm `needs_after.you` and `needs_after.partner` are each computed independently against a field frozen at real, current values -- neither should reflect the OTHER side's hypothetical roster (the Global Constraint this plan is built around).
4. Manually smoke-test Task 4's UI against a real tracked league, not just the fixture-driven test -- per this project's established pattern, the most consequential bugs across this whole initiative have come from testing against real data, not fixtures.
