# Pick History with Tier Grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Pick history" panel to the draft board showing every pick made so far, newest first, each tagged GREAT/GOOD/FAIR/POOR based on how good the pick was.

**Architecture:** A new `ffdo.engine.grading` module holds two pure grading functions (one per draft format) with no dependencies beyond stdlib. `ffdo.api.board` calls them while building the existing auction/snake board payloads, adding a `history` list to each. The static JS/CSS frontend renders that list as a new sidebar card, following the same poll-and-redraw pattern as the existing "Roster power rankings" card.

**Tech Stack:** Python 3.12 (FastAPI backend, pytest), vanilla JS + plain CSS frontend (no build step, no framework).

**Spec:** No separate spec file — this is a bounded, well-scoped addition to existing code (per the brainstorming skill's bounded path). The design was proposed and approved in conversation on 2026-08-23; it is restated in full below.

## Global Constraints

- No new dependencies. No build step for the frontend — plain JS/CSS served as static files.
- Python tests run via `uv run pytest` (pytest config already points `pythonpath` at `src`).
- Follow existing code style: dataclasses/pure functions in `engine/`, JSON-shaping in `api/board.py`, CSS custom-property tokens in `board.css` (no inline hex colors outside `:root`).
- Grading thresholds are named module-level constants (not magic numbers), so they're easy to retune later.

---

## Design recap

**Auction grading** — reuses the existing bargain/over-value idea already live for the current nomination (`board.js:313`), applied to every historical pick instead. Compares `amount` paid to the player's `baseline` (static fair-market price, not inflation-adjusted — grading pick *skill*, not market-wide inflation swings):

| Paid vs. baseline | Grade |
|---|---|
| ≤ 70% | GREAT |
| 70–95% | GOOD |
| 95–110% | FAIR |
| > 110% | POOR |

**Snake grading** — no existing metric for this, so it's new. For each pick, replay the draft up to that point to find every other player who was still undrafted *at that moment* and still fantasy-relevant (`vor > 0`). Rank the picked player's VOR against that pool:

| % of that pool with higher VOR | Grade |
|---|---|
| ≤ 5% | GREAT |
| 5–20% | GOOD |
| 20–50% | FAIR |
| > 50% | POOR |

Both gradings return `None` when there's no signal to grade against (no recorded price for an auction pick; the player isn't in the valued pool at all) — the UI shows no badge rather than fabricating a grade.

---

## File Structure

- Create: `src/ffdo/engine/grading.py` — the two pure grading functions + their threshold constants.
- Create: `tests/engine/test_grading.py` — unit tests for grading.py.
- Modify: `src/ffdo/api/board.py` — add `_history_row`, `_build_auction_history`, `_build_snake_history`; wire a `"history"` key into both board payloads.
- Modify: `tests/api/test_board.py` — auction history tests.
- Modify: `tests/api/test_snake_board.py` — snake history tests.
- Modify: `src/ffdo/web/board/index.html` — new `<aside id="history">` card in the sidebar.
- Modify: `src/ffdo/web/board/board.css` — styles for the history card and grade badges.
- Modify: `src/ffdo/web/board/board.js` — `renderHistory()`, wired into `render()`.

---

### Task 1: Grading engine module

**Files:**
- Create: `src/ffdo/engine/grading.py`
- Test: `tests/engine/test_grading.py`

**Interfaces:**
- Produces: `grading.grade_auction_pick(baseline: float, amount: int) -> str` and `grading.grade_snake_pick(picked_vor: float, alternative_vors: Sequence[float]) -> str`, both returning one of `"GREAT"`, `"GOOD"`, `"FAIR"`, `"POOR"`. Consumed by Task 2 and Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_grading.py`:

```python
from ffdo.engine import grading


def test_grade_auction_pick_great_when_paid_well_under_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=60) == "GREAT"


def test_grade_auction_pick_good_when_paid_slightly_under_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=85) == "GOOD"


def test_grade_auction_pick_fair_when_paid_close_to_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=105) == "FAIR"


def test_grade_auction_pick_poor_when_paid_well_over_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=130) == "POOR"


def test_grade_auction_pick_ratio_boundaries_are_inclusive():
    assert grading.grade_auction_pick(baseline=100.0, amount=70) == "GREAT"
    assert grading.grade_auction_pick(baseline=100.0, amount=95) == "GOOD"
    assert grading.grade_auction_pick(baseline=100.0, amount=110) == "FAIR"


def test_grade_auction_pick_defaults_to_fair_with_no_baseline_signal():
    """A zero or negative baseline carries no fair-value signal -- grading
    it POOR or GREAT would fabricate a verdict the model has no basis for."""
    assert grading.grade_auction_pick(baseline=0.0, amount=5) == "FAIR"


def test_grade_snake_pick_great_at_or_below_5th_percentile():
    alternatives = list(range(99, -1, -1))  # 100 values, 99 down to 0
    assert grading.grade_snake_pick(picked_vor=100.0, alternative_vors=alternatives) == "GREAT"
    assert grading.grade_snake_pick(picked_vor=94.0, alternative_vors=alternatives) == "GREAT"  # exactly 5%


def test_grade_snake_pick_good_between_5_and_20_percent():
    alternatives = list(range(99, -1, -1))
    assert grading.grade_snake_pick(picked_vor=93.0, alternative_vors=alternatives) == "GOOD"
    assert grading.grade_snake_pick(picked_vor=79.0, alternative_vors=alternatives) == "GOOD"  # exactly 20%


def test_grade_snake_pick_fair_between_20_and_50_percent():
    alternatives = list(range(99, -1, -1))
    assert grading.grade_snake_pick(picked_vor=78.0, alternative_vors=alternatives) == "FAIR"
    assert grading.grade_snake_pick(picked_vor=49.0, alternative_vors=alternatives) == "FAIR"  # exactly 50%


def test_grade_snake_pick_poor_above_50_percent():
    alternatives = list(range(99, -1, -1))
    assert grading.grade_snake_pick(picked_vor=48.0, alternative_vors=alternatives) == "POOR"
    assert grading.grade_snake_pick(picked_vor=0.0, alternative_vors=alternatives) == "POOR"


def test_grade_snake_pick_defaults_to_fair_when_no_alternatives_remain():
    """Nothing with positive VOR was left on the board -- there's no reach
    to grade, so this isn't a POOR pick by default."""
    assert grading.grade_snake_pick(picked_vor=-5.0, alternative_vors=[]) == "FAIR"


def test_grade_snake_pick_poor_when_picked_player_has_no_positive_vor_but_better_existed():
    assert grading.grade_snake_pick(picked_vor=-5.0, alternative_vors=[10.0, 5.0, 1.0]) == "POOR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_grading.py -v`
Expected: FAIL (or ERROR) — `ModuleNotFoundError: No module named 'ffdo.engine.grading'`

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/engine/grading.py`:

```python
"""Grades how good a draft pick was, given what else was on the board."""

from __future__ import annotations

from collections.abc import Sequence

AUCTION_GREAT_RATIO = 0.70
AUCTION_GOOD_RATIO = 0.95
AUCTION_FAIR_RATIO = 1.10

SNAKE_GREAT_PERCENTILE = 0.05
SNAKE_GOOD_PERCENTILE = 0.20
SNAKE_FAIR_PERCENTILE = 0.50


def grade_auction_pick(baseline: float, amount: int) -> str:
    """GREAT/GOOD/FAIR/POOR, from what was paid vs. fair-market baseline.

    A zero or negative baseline carries no fair-value signal to grade
    against, so it grades FAIR rather than fabricating a verdict.
    """
    if baseline <= 0:
        return "FAIR"
    ratio = amount / baseline
    if ratio <= AUCTION_GREAT_RATIO:
        return "GREAT"
    if ratio <= AUCTION_GOOD_RATIO:
        return "GOOD"
    if ratio <= AUCTION_FAIR_RATIO:
        return "FAIR"
    return "POOR"


def grade_snake_pick(picked_vor: float, alternative_vors: Sequence[float]) -> str:
    """GREAT/GOOD/FAIR/POOR, from how many still-available players beat it.

    `alternative_vors` is the VOR of every other player who was still
    undrafted immediately before this pick and had positive VOR (i.e. was
    still fantasy-relevant) -- the picked player itself is excluded. An
    empty pool (nothing of value was left) grades FAIR: there was no
    meaningfully better option to have reached past.
    """
    pool = list(alternative_vors)
    if not pool:
        return "FAIR"
    beat_by = sum(1 for v in pool if v > picked_vor)
    percentile = beat_by / len(pool)
    if percentile <= SNAKE_GREAT_PERCENTILE:
        return "GREAT"
    if percentile <= SNAKE_GOOD_PERCENTILE:
        return "GOOD"
    if percentile <= SNAKE_FAIR_PERCENTILE:
        return "FAIR"
    return "POOR"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_grading.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/grading.py tests/engine/test_grading.py
git commit -m "feat: add pick grading engine for auction and snake drafts"
```

---

### Task 2: Auction pick history in the board payload

**Files:**
- Modify: `src/ffdo/api/board.py:1-9` (imports), after `src/ffdo/api/board.py:57` (new helpers), `src/ffdo/api/board.py:144-147` (wire into return dict)
- Test: `tests/api/test_board.py`

**Interfaces:**
- Consumes: `grading.grade_auction_pick(baseline, amount) -> str` from Task 1.
- Produces: `_history_row(pick, vp, teams, grade, amount) -> dict` and `_build_auction_history(state, valued, baseline, teams) -> list[dict]`, both defined in `board.py`. Task 3 reuses `_history_row`.
- The `history` list entries have shape: `{pick_no: int, round: int, roster_id: int | None, team_name: str, player_id: str, name: str, position: str | None, vor: float | None, amount: int | None, grade: str | None}`, newest pick first.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_board.py` (after the existing tests, using the file's existing `_league()` and `_valued()` helpers):

```python
def test_auction_history_is_newest_pick_first_with_grades():
    league = _league()
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=60),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2,
                 picked_by="u2", player_id="p1", amount=130),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    ids = ["p0", "p1"]
    valued = _valued(ids)
    baseline = {pid: 100.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline, teams=_teams())

    assert [h["player_id"] for h in out["history"]] == ["p1", "p0"]
    assert out["history"][0]["grade"] == "POOR"   # p1: paid 130 vs baseline 100
    assert out["history"][1]["grade"] == "GREAT"  # p0: paid 60 vs baseline 100
    assert out["history"][0]["team_name"] == "Bravo"
    assert out["history"][0]["amount"] == 130


def test_auction_history_grade_is_none_when_no_amount_was_recorded():
    """A keeper/commissioner pick can land with no bid amount -- there's
    nothing to compare against, so it must not fabricate a grade."""
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                       picked_by="u1", player_id="p0", amount=None),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 100.0})
    assert out["history"][0]["grade"] is None
    assert out["history"][0]["amount"] is None


def test_auction_history_team_name_falls_back_when_profile_missing():
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=5,
                       picked_by="u5", player_id="p0", amount=10),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 10.0})
    assert out["history"][0]["team_name"] == "Team 5"
```

(`_teams()` is already defined later in the file; if the new tests run before that definition is registered pytest still resolves it fine since it's module-level — no reordering needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_board.py -k history -v`
Expected: FAIL — `KeyError: 'history'`

- [ ] **Step 3: Write the implementation**

Modify the import block at `src/ffdo/api/board.py:1-9`:

```python
"""Shapes engine output into the JSON the board renders."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, TeamProfile, ValuedPlayer
from ffdo.engine import auction
from ffdo.engine import grading
from ffdo.engine import roster as roster_engine
```

Insert after `_build_rosters_payload` (i.e. after the `return rows` at what is currently line 57, before `def build_auction_board`):

```python
def _history_row(pick, vp, teams, grade, amount) -> dict:
    team = teams.get(pick.roster_id) if pick.roster_id is not None else None
    if team is not None:
        team_name = team.display_name
    elif pick.roster_id is not None:
        team_name = f"Team {pick.roster_id}"
    else:
        team_name = "—"
    return {
        "pick_no": pick.pick_no,
        "round": pick.round,
        "roster_id": pick.roster_id,
        "team_name": team_name,
        "player_id": pick.player_id,
        "name": vp.profile.full_name if vp else pick.player_id,
        "position": vp.profile.position if vp else None,
        "vor": round(vp.vor, 1) if vp else None,
        "amount": amount,
        "grade": grade,
    }


def _build_auction_history(
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    teams: Mapping[int, TeamProfile],
) -> list[dict]:
    """Newest pick first. Ungraded (no badge) when the pick has no recorded
    price -- e.g. a keeper slotted in without a bid -- since there is
    nothing to compare against."""
    rows = []
    for pick in sorted(state.picks, key=lambda p: p.pick_no):
        vp = valued.get(pick.player_id)
        grade = None
        if pick.amount is not None:
            base = baseline.get(pick.player_id, 1.0)
            grade = grading.grade_auction_pick(base, pick.amount)
        rows.append(_history_row(pick, vp, teams, grade, pick.amount))
    rows.reverse()
    return rows
```

Modify the return statement of `build_auction_board` (currently `src/ffdo/api/board.py:128-147`) to add a `"history"` key after `"rosters"`:

```python
    return {
        "format": "auction",
        "live_nomination": live_nomination,
        "inflation": round(factor, 3),
        "budget": {
            "total": league.num_teams * league.budget,
            "spent": sum(spent.values()),
            "by_roster": spent,
            "your_roster_id": roster_id,
            "your_spent": your_spent,
            "your_slots_left": your_slots_left,
            "your_dollars_left": your_dollars_left,
            "your_dollars_per_slot": round(your_dollars_per_slot, 1),
            "league_dollars_per_slot": round(league_dollars_per_slot, 1),
            "by_position": by_position,
        },
        "picks_made": len(state.picks),
        "players": rows,
        "rosters": _build_rosters_payload(league, state, valued, teams, roster_id),
        "history": _build_auction_history(state, valued, baseline, teams or {}),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_board.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/board.py tests/api/test_board.py
git commit -m "feat: add graded pick history to the auction board payload"
```

---

### Task 3: Snake pick history in the board payload

**Files:**
- Modify: `src/ffdo/api/board.py` (new `_build_snake_history` helper, wired into `build_snake_board`'s return dict)
- Test: `tests/api/test_snake_board.py`

**Interfaces:**
- Consumes: `grading.grade_snake_pick(picked_vor, alternative_vors) -> str` from Task 1; `_history_row(...)` from Task 2.
- Produces: `_build_snake_history(state, valued, teams) -> list[dict]`, same row shape as Task 2's auction history but with `amount` always `None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_snake_board.py` (using the file's existing `_league()`, `_valued()`, `_state()` helpers — `_valued()` there produces `RB0..RB5` and `WR0..WR5` with VOR `100 - i*10`, so `RB0`/`WR0` are the highest-VOR player at each position):

```python
def test_snake_history_is_newest_pick_first_with_grades():
    from ffdo.domain.models import TeamProfile

    valued = _valued()  # RB0..RB5, WR0..WR5; vor = 100 - i*10 within each position
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="RB0", amount=None),  # best VOR (100) on the board -> GREAT
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2, picked_by="u2",
                 player_id="RB5", amount=None),  # vor=50, but WR0 (vor=100) still on board -> POOR
    )
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=12, rounds=6, budget=None, picks=picks)
    teams = {1: TeamProfile(roster_id=1, display_name="Alpha"),
             2: TeamProfile(roster_id=2, display_name="Bravo")}

    out = board.build_snake_board(_league(), state, valued,
                                  {pid: 0.5 for pid in valued}, {}, teams=teams)

    assert [h["player_id"] for h in out["history"]] == ["RB5", "RB0"]
    assert out["history"][1]["grade"] == "GREAT"
    assert out["history"][0]["grade"] == "POOR"
    assert out["history"][0]["amount"] is None
    assert out["history"][1]["team_name"] == "Alpha"


def test_snake_history_pool_shrinks_as_earlier_picks_are_replayed():
    """The pool a pick is graded against must exclude players already taken
    earlier in the same draft, not just the one player being graded."""
    valued = _valued()
    # Take the top-VOR RB and WR first, then take the (now second-best) RB.
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="RB0", amount=None),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2, picked_by="u2",
                 player_id="WR0", amount=None),
        DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=1, picked_by="u1",
                 player_id="RB1", amount=None),
    )
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=12, rounds=6, budget=None, picks=picks)

    out = board.build_snake_board(_league(), state, valued,
                                  {pid: 0.5 for pid in valued}, {})

    by_id = {h["player_id"]: h for h in out["history"]}
    # RB1 (vor=90) is now the best VOR left on the board -- RB0 and WR0 are
    # already gone -- so it grades GREAT, not merely "good".
    assert by_id["RB1"]["grade"] == "GREAT"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_snake_board.py -k history -v`
Expected: FAIL — `KeyError: 'history'`

- [ ] **Step 3: Write the implementation**

Insert after `_build_auction_history` in `src/ffdo/api/board.py` (added in Task 2):

```python
def _build_snake_history(
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    teams: Mapping[int, TeamProfile],
) -> list[dict]:
    """Newest pick first. Each pick is graded against the VOR of every other
    still-fantasy-relevant (VOR > 0) player who was undrafted immediately
    before it -- reconstructed by replaying picks in order, not against the
    full player pool, so a 10th-round pick isn't graded against 1st-round
    talent that was already gone."""
    drafted_so_far: set[str] = set()
    rows = []
    for pick in sorted(state.picks, key=lambda p: p.pick_no):
        vp = valued.get(pick.player_id)
        grade = None
        if vp is not None:
            alternatives = [
                other.vor for pid, other in valued.items()
                if other.vor > 0 and pid != pick.player_id and pid not in drafted_so_far
            ]
            grade = grading.grade_snake_pick(vp.vor, alternatives)
        rows.append(_history_row(pick, vp, teams, grade, None))
        drafted_so_far.add(pick.player_id)
    rows.reverse()
    return rows
```

Modify the return statement of `build_snake_board` (currently `src/ffdo/api/board.py:200-206`) to add a `"history"` key after `"rosters"`:

```python
    return {
        "format": "snake",
        "cost_of_waiting": dict(cost_of_waiting),
        "picks_made": len(state.picks),
        "players": rows,
        "rosters": _build_rosters_payload(league, state, valued, teams, roster_id),
        "history": _build_snake_history(state, valued, teams or {}),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_snake_board.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones)

- [ ] **Step 5: Run the full backend test suite**

Run: `uv run pytest`
Expected: PASS, no regressions anywhere else in the suite.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/board.py tests/api/test_snake_board.py
git commit -m "feat: add graded pick history to the snake board payload"
```

---

### Task 4: Pick history card in the frontend

**Files:**
- Modify: `src/ffdo/web/board/index.html:108-115`
- Modify: `src/ffdo/web/board/board.css` (new rules after the roster-card section, `src/ffdo/web/board/board.css:1-166`)
- Modify: `src/ffdo/web/board/board.js:88-126` (`render()`), after `renderRosters()` (`src/ffdo/web/board/board.js:353-393`)

**Interfaces:**
- Consumes: `d.history` from the `/api/board` payload (Tasks 2 & 3) — array of `{pick_no, round, roster_id, team_name, player_id, name, position, vor, amount, grade}`, newest first.

There is no JS test runner in this repo (no `package.json`, no build step) — this task's "test" is a manual check against a running server, same as every other piece of this frontend.

- [ ] **Step 1: Add the history card markup**

In `src/ffdo/web/board/index.html`, insert a new `<aside>` right after the `</aside>` that closes `#rosters` (currently line 114), still inside `#sidebar` (which closes at line 115):

```html
  <aside id="history">
    <div class="history-head">
      <h2>Pick history</h2>
      <span class="history-sub">grades how each pick's value compared to what was still on the board</span>
    </div>
    <div id="history-rows"></div>
  </aside>
```

- [ ] **Step 2: Add the history card styles**

In `src/ffdo/web/board/board.css`, insert after the `.roster-detail-player.bench { color: var(--faint); }` rule (currently line 166):

```css
/* ---- pick history card ---- */
#history {
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.history-head h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.history-sub { font-size: 11px; color: var(--faint); }
#history-rows { display: flex; flex-direction: column; max-height: 360px; overflow-y: auto; }
.history-empty { color: var(--faint); font-size: 12px; padding: 8px 4px; }
.history-row { display: flex; align-items: center; gap: 10px; padding: 8px 4px; border-radius: 7px; }
.history-row:hover { background: var(--surface-2); }
.history-pickno { width: 52px; flex-shrink: 0; font-family: var(--font-mono); font-size: 10.5px; color: var(--faint); }
.history-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.history-name { font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-meta { font-family: var(--font-mono); font-size: 10.5px; }
.history-amount { font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--text); flex-shrink: 0; }
.history-badge {
  font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
  padding: 3px 7px; border-radius: 5px; flex-shrink: 0;
}
.history-badge.great { color: var(--green); background: color-mix(in oklch, var(--green) 15%, transparent); border: 1px solid color-mix(in oklch, var(--green) 40%, transparent); }
.history-badge.good { color: var(--accent); background: color-mix(in oklch, var(--accent) 15%, transparent); border: 1px solid color-mix(in oklch, var(--accent) 40%, transparent); }
.history-badge.fair { color: var(--amber); background: color-mix(in oklch, var(--amber) 15%, transparent); border: 1px solid color-mix(in oklch, var(--amber) 40%, transparent); }
.history-badge.poor { color: var(--red); background: color-mix(in oklch, var(--red) 15%, transparent); border: 1px solid color-mix(in oklch, var(--red) 40%, transparent); }
```

- [ ] **Step 3: Add `renderHistory()` and wire it into `render()`**

In `src/ffdo/web/board/board.js`, add `renderHistory();` to `render()` right after `renderRosters();` (currently `src/ffdo/web/board/board.js:124`):

```javascript
  renderCow();
  renderPositionBudget();
  renderMoneyHeader();
  renderTable();
  renderNominated();
  renderRosters();
  renderHistory();
  renderSortHeaders();
```

Add the function itself after `renderRosters()` (currently ends at `src/ffdo/web/board/board.js:393`), before `renderSortHeaders()`:

```javascript
function renderHistory() {
  const d = state.data;
  const el = document.getElementById("history-rows");
  if (!d || !d.history) { el.innerHTML = ""; return; }

  if (d.history.length === 0) {
    el.innerHTML = `<div class="history-empty">No picks yet.</div>`;
    return;
  }

  el.innerHTML = d.history.map(h => {
    const posColor = `var(--${(h.position ?? "").toLowerCase()}, var(--muted))`;
    const amount = h.amount !== null && h.amount !== undefined
      ? `<span class="history-amount">$${h.amount}</span>` : "";
    const badge = h.grade
      ? `<span class="history-badge ${h.grade.toLowerCase()}">${h.grade}</span>` : "";
    return `<div class="history-row">
      <span class="history-pickno">R${h.round} P${h.pick_no}</span>
      <div class="history-main">
        <span class="history-name">${escapeHtml(h.name)}</span>
        <span class="history-meta" style="color:${posColor}">${h.position ?? ""} &middot; ${escapeHtml(h.team_name)}</span>
      </div>
      ${amount}
      ${badge}
    </div>`;
  }).join("");
}
```

- [ ] **Step 4: Manually verify in a running server**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

Open `http://localhost:8000` in a browser (use a mock draft if no live draft is in progress — see the Sleeper mock draft support already in this repo). Confirm:
- A "Pick history" card appears in the sidebar below "Roster power rankings".
- Before any picks exist, it shows "No picks yet."
- After a pick (nominate/simulate one via the mock draft), the card shows it at the top, with a colored GREAT/GOOD/FAIR/POOR badge.
- Auction boards show a `$amount` next to each historical pick; snake boards do not.
- New picks appear at the top of the list as the poll refreshes, older picks shift down.

Stop the server (`Ctrl+C`) once verified.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/web/board/index.html src/ffdo/web/board/board.css src/ffdo/web/board/board.js
git commit -m "feat: render graded pick history in the draft board sidebar"
```

---

## Definition of done

- `uv run pytest` passes with no regressions.
- Both auction and snake board payloads include a `history` array, newest pick first, each entry graded GREAT/GOOD/FAIR/POOR (or ungraded when there's no signal).
- The board UI shows a "Pick history" card in the sidebar, manually verified against a running server in both an auction and a snake context (via mock draft or FFDO_DRAFT_ID pointed at a snake league).
