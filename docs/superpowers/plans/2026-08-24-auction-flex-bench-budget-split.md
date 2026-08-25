# Auction Flex/Bench Budget Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the auction budget engine's combined `flex_bench_reserve` bucket into two independently priced categories — `FLEX`, priced like a real starting position from the best remaining flex-eligible players, and `BENCH`, kept as a flat $1/slot reserve — and update the board UI to show FLEX as a fifth position row.

**Architecture:** One function (`positional_budget()` in `src/ffdo/engine/auction.py`) changes: dedicated-position pools now retain their sorted remainder instead of discarding it, and a new flex pool draws its top-N from the union of those remainders across flex-eligible positions. The `by_position` API payload gains `FLEX`/`BENCH` keys (shaped like the existing `QB`/`RB`/`WR`/`TE` entries) in place of `flex_bench_reserve`/`flex_bench_slots_open`. `board.js` renders `FLEX` as a normal position row and relabels the reserve line "Bench reserve".

**Tech Stack:** Python 3 (pytest), vanilla JS/CSS (no build step, no JS test runner in this repo).

**Spec:** `docs/superpowers/specs/2026-08-24-auction-flex-bench-budget-split-design.md`

## Global Constraints

- Output keys for the new categories are uppercase `"FLEX"` / `"BENCH"`, matching the existing `OFFENSE_POSITIONS` casing convention (`"QB"`, `"RB"`, `"WR"`, `"TE"`) — not lowercase.
- Both new entries are shaped exactly like the existing position entries: `{"recommended": float, "slots_open": int}`.
- No change to `baseline_prices()`, `inflation_factor()`, `max_bid()`, or how negative VOR is floored (`MIN_BID` = $1, already handled in `baseline_prices()`).
- No `board.css` changes — reuse `.posbudget-row` and `.posbudget-reserve` as-is.
- Run `uv run pytest` (full suite) before every commit in this plan; every commit must leave the suite green.

---

### Task 1: Split flex/bench pricing in `positional_budget()`

**Files:**
- Modify: `src/ffdo/engine/auction.py:76-148` (the `positional_budget` function)
- Modify: `tests/engine/test_auction.py:106-131` (`test_positional_budget_need_and_slot_invariant`)
- Modify: `tests/engine/test_auction.py:134-155` (`test_positional_budget_scales_to_your_dollars_left`)
- Modify: `tests/engine/test_auction.py:157-181` (`test_extra_drafted_players_reduce_flex_bench_not_dedicated_need`, renamed)
- Modify: `tests/api/test_board.py:252-271` (`test_board_includes_positional_budget_recommendation`)
- Modify: `tests/api/test_board.py:274-316` (`test_positional_budget_slot_invariant_holds_for_a_real_roster`)
- Test: new tests added to `tests/engine/test_auction.py`

**Interfaces:**
- Consumes: `FLEX_ELIGIBILITY: dict[str, frozenset[str]]` from `ffdo.engine.replacement` (already imported at `auction.py:9`); `OFFENSE_POSITIONS: frozenset[str]` from `ffdo.domain.constants` (already imported at `auction.py:7`).
- Produces: `positional_budget(...) -> dict[str, dict[str, float] | float]` — same signature as today, but the returned dict's keys are `"QB"`, `"RB"`, `"WR"`, `"TE"`, `"FLEX"`, `"BENCH"` (was `"QB"`, `"RB"`, `"WR"`, `"TE"`, `"flex_bench_reserve"`, `"flex_bench_slots_open"`). `out["FLEX"]` and `out["BENCH"]` are each `{"recommended": float, "slots_open": int}`, matching the shape every other entry already has. `board.py`'s `build_auction_board()` (Task 2's caller) passes this dict through unchanged as `budget.by_position` — no code change needed there.

- [ ] **Step 1: Update `test_positional_budget_need_and_slot_invariant`**

Replace the body of the test in `tests/engine/test_auction.py` (currently lines 106-131) with:

```python
def test_positional_budget_need_and_slot_invariant():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "qb2": ("QB", 40.0),
        "rb1": ("RB", 80.0), "rb2": ("RB", 70.0), "rb3": ("RB", 60.0),
        "wr1": ("WR", 90.0), "wr2": ("WR", 85.0), "wr3": ("WR", 75.0),
        "te1": ("TE", 30.0), "te2": ("TE", 20.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=200.0)

    assert result["QB"]["slots_open"] == 1
    assert result["RB"]["slots_open"] == 2
    assert result["WR"]["slots_open"] == 2
    assert result["TE"]["slots_open"] == 1
    assert result["FLEX"]["slots_open"] == 1
    assert result["BENCH"]["slots_open"] == 2
    total_slots_accounted = (
        result["QB"]["slots_open"] + result["RB"]["slots_open"]
        + result["WR"]["slots_open"] + result["TE"]["slots_open"]
        + result["FLEX"]["slots_open"] + result["BENCH"]["slots_open"])
    assert total_slots_accounted == league.roster_size
```

- [ ] **Step 2: Update `test_positional_budget_scales_to_your_dollars_left`**

Replace the body of the test (currently lines 134-155) with:

```python
def test_positional_budget_scales_to_your_dollars_left():
    league = _league_multi(("QB", "RB", "WR", "TE", "BN"))
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "rb1": ("RB", 80.0),
        "wr1": ("WR", 90.0), "te1": ("TE", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 5, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=90.0)

    total = (result["QB"]["recommended"] + result["RB"]["recommended"]
             + result["WR"]["recommended"] + result["TE"]["recommended"]
             + result["FLEX"]["recommended"] + result["BENCH"]["recommended"])
    # Six independently-rounded (1-decimal) values can compound to ~0.1-0.3
    # off the true total even though the underlying scale is exact -- widen
    # accordingly rather than chase a razor-tight bound.
    assert total == pytest.approx(90.0, abs=0.5)
```

- [ ] **Step 3: Rename and update `test_extra_drafted_players_reduce_flex_bench_not_dedicated_need`**

Replace the test (currently lines 157-181, including its name) with:

```python
def test_extra_drafted_players_reduce_flex_not_dedicated_need():
    """A 2nd RB drafted beyond the single dedicated RB slot must have used
    the FLEX slot, not created negative dedicated need."""
    league = _league_multi(("RB", "FLEX", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, budget=200, picks=picks)
    valued = _valued_positions({
        "rb_drafted_1": ("RB", 50.0), "rb_drafted_2": ("RB", 40.0),
        "rb_avail": ("RB", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=1,
        your_dollars_left=180.0)

    assert result["RB"]["slots_open"] == 0
    assert result["FLEX"]["slots_open"] == 0
    assert result["BENCH"]["slots_open"] == 1
```

- [ ] **Step 4: Add `test_flex_prices_from_leftover_pool_not_min_bid`**

Add this new test to `tests/engine/test_auction.py`, after the renamed test from Step 3:

```python
def test_flex_prices_from_leftover_pool_not_min_bid():
    """FLEX must price like a real starting slot -- from the best remaining
    flex-eligible players left after dedicated slots are filled -- not the
    $1 floor bench gets."""
    league = _league_multi(("RB", "FLEX", "BN"))
    valued = _valued_positions({
        "rb1": ("RB", 50.0), "rb2": ("RB", 30.0), "rb3": ("RB", 10.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 3, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=81.0)

    # RB's dedicated slot claims rb1 (50); FLEX's only candidates are what's
    # left (rb2=30, rb3=10), takes the top one; BENCH stays at the $1 floor.
    assert result["RB"]["recommended"] == pytest.approx(50.0, abs=0.1)
    assert result["FLEX"]["recommended"] == pytest.approx(30.0, abs=0.1)
    assert result["BENCH"]["recommended"] == pytest.approx(1.0, abs=0.1)
    assert result["FLEX"]["recommended"] > result["BENCH"]["recommended"]
```

- [ ] **Step 5: Add `test_flex_pool_excludes_players_already_claimed_by_dedicated_slots`**

Add this new test right after it:

```python
def test_flex_pool_excludes_players_already_claimed_by_dedicated_slots():
    """The flex candidate pool must draw from what's left after dedicated
    RB/WR budgets take their top picks -- never double-count the same
    player in both a dedicated budget and the flex budget."""
    league = _league_multi(("RB", "WR", "FLEX", "BN"))
    valued = _valued_positions({
        "rb1": ("RB", 50.0), "rb2": ("RB", 20.0),
        "wr1": ("WR", 40.0), "wr2": ("WR", 15.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 4, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=111.0)

    # Dedicated RB/WR each claim their top player (50, 40); FLEX's only
    # candidates are what's left (rb2=20, wr2=15) -- never rb1/wr1 again.
    assert result["RB"]["recommended"] == pytest.approx(50.0, abs=0.1)
    assert result["WR"]["recommended"] == pytest.approx(40.0, abs=0.1)
    assert result["FLEX"]["recommended"] == pytest.approx(20.0, abs=0.1)
```

- [ ] **Step 6: Update `test_board_includes_positional_budget_recommendation`**

Replace the test in `tests/api/test_board.py` (currently lines 252-271) with:

```python
def test_board_includes_positional_budget_recommendation():
    league = LeagueProfile(league_id="x", season=2025, num_teams=12,
                           roster_positions=("QB", "RB", "RB", "WR", "WR", "TE",
                                            "FLEX", "BN", "BN"),
                           scoring_settings={}, budget=200)
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])
    ids = ["p0", "p1", "p2"]
    valued = _valued(ids)
    baseline = {pid: 10.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline, roster_id=None)

    by_pos = out["budget"]["by_position"]
    assert set(by_pos) == {"QB", "RB", "WR", "TE", "FLEX", "BENCH"}
    assert by_pos["RB"]["slots_open"] == 2
    total = (by_pos["QB"]["recommended"] + by_pos["RB"]["recommended"]
             + by_pos["WR"]["recommended"] + by_pos["TE"]["recommended"]
             + by_pos["FLEX"]["recommended"] + by_pos["BENCH"]["recommended"])
    assert total == pytest.approx(out["budget"]["your_dollars_left"], abs=0.5)
```

- [ ] **Step 7: Update `test_positional_budget_slot_invariant_holds_for_a_real_roster`**

In `tests/api/test_board.py` (currently lines 274-316), replace only the `slots_accounted` block near the end of the test:

```python
    by_pos = out["budget"]["by_position"]
    slots_accounted = (
        sum(by_pos[p]["slots_open"] for p in ("QB", "RB", "WR", "TE"))
        + by_pos["FLEX"]["slots_open"] + by_pos["BENCH"]["slots_open"])
    assert slots_accounted == out["budget"]["your_slots_left"]
    # roster 1 already drafted 1 of 2 dedicated RB slots
    assert by_pos["RB"]["slots_open"] == 1
```

Leave the rest of that test (the fixture setup above the `by_pos = ...` line) unchanged.

- [ ] **Step 8: Run the updated/new tests to verify they fail against current code**

Run: `uv run pytest tests/engine/test_auction.py tests/api/test_board.py -v -k "positional_budget or flex"`

Expected: FAIL — `KeyError: 'FLEX'` (or `'BENCH'`) on every test touched above, since `positional_budget()` still only produces `flex_bench_reserve`/`flex_bench_slots_open`.

- [ ] **Step 9: Implement the flex/bench split in `positional_budget()`**

Replace the full body of `positional_budget()` in `src/ffdo/engine/auction.py:76-148` with:

```python
def positional_budget(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> dict[str, dict[str, float] | float]:
    """Recommended $ per position to fill your remaining roster slots.

    Dedicated slots (e.g. a plain "RB" slot) only ever take that exact
    position. FLEX-eligible slots are priced from the best remaining
    players at any flex-eligible position, after dedicated slots have
    already claimed their share -- so FLEX (a real starting spot that
    scores every week) gets a real market price, not a floor. Bench slots
    carry no positional preference and score nothing, so they stay a flat
    $1/slot reserve. `roster_id=None` (FFDO_ROSTER_ID unset) is treated as
    a fresh roster -- zero drafted -- the same fallback the board applies
    to max-bid elsewhere.
    """
    drafted = state.drafted_player_ids()
    your_picks = ([p for p in state.picks if p.roster_id == roster_id]
                  if roster_id is not None else [])

    dedicated_count = {pos: league.roster_positions.count(pos)
                       for pos in OFFENSE_POSITIONS}
    drafted_count = dict.fromkeys(OFFENSE_POSITIONS, 0)
    undetermined = 0
    for pick in your_picks:
        vp = valued.get(pick.player_id)
        if vp is None or vp.profile.position not in OFFENSE_POSITIONS:
            undetermined += 1
            continue
        drafted_count[vp.profile.position] += 1

    dedicated_need = {
        pos: max(0, dedicated_count[pos] - min(dedicated_count[pos], drafted_count[pos]))
        for pos in OFFENSE_POSITIONS
    }
    leftover = sum(max(0, drafted_count[pos] - dedicated_count[pos])
                   for pos in OFFENSE_POSITIONS)

    flex_total = sum(1 for slot in league.roster_positions
                     if slot in FLEX_ELIGIBILITY)
    bench_total = league.roster_positions.count("BN")
    flex_remaining = max(0, flex_total - leftover)
    bench_spill = max(0, leftover - flex_total)
    bench_remaining = max(0, bench_total - bench_spill - undetermined)

    available = [vp for pid, vp in valued.items() if pid not in drafted]

    raw: dict[str, float] = {}
    pos_leftover_pool: dict[str, list[float]] = {}
    for pos in OFFENSE_POSITIONS:
        need = dedicated_need[pos]
        pool = sorted(
            (max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)
             for vp in available if vp.profile.position == pos),
            reverse=True,
        )
        raw[pos] = sum(pool[:need])
        pos_leftover_pool[pos] = pool[need:]

    flex_positions = {
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    }
    flex_candidates = sorted(
        (price for pos in flex_positions for price in pos_leftover_pool.get(pos, [])),
        reverse=True,
    )[:flex_remaining]
    raw_flex = sum(flex_candidates)
    raw_bench = MIN_BID * bench_remaining

    total_raw = sum(raw.values()) + raw_flex + raw_bench
    scale = your_dollars_left / total_raw if total_raw > 0 else 0.0

    out: dict[str, dict[str, float] | float] = {
        pos: {
            "recommended": round(raw[pos] * scale, 1),
            "slots_open": dedicated_need[pos],
        }
        for pos in OFFENSE_POSITIONS
    }
    out["FLEX"] = {
        "recommended": round(raw_flex * scale, 1),
        "slots_open": flex_remaining,
    }
    out["BENCH"] = {
        "recommended": round(raw_bench * scale, 1),
        "slots_open": bench_remaining,
    }
    return out
```

- [ ] **Step 10: Run the same tests again to verify they pass**

Run: `uv run pytest tests/engine/test_auction.py tests/api/test_board.py -v -k "positional_budget or flex"`

Expected: PASS — all 7 tests (5 in `test_auction.py`, 2 in `test_board.py`).

- [ ] **Step 11: Run the full test suite to check for regressions**

Run: `uv run pytest`

Expected: PASS, no failures anywhere else in the suite (nothing else references `flex_bench_reserve`/`flex_bench_slots_open` per the earlier repo-wide search).

- [ ] **Step 12: Commit**

```bash
git add src/ffdo/engine/auction.py tests/engine/test_auction.py tests/api/test_board.py
git commit -m "feat: price FLEX as a real starting slot, separate from BENCH reserve"
```

---

### Task 2: Render FLEX as a position row and relabel the bench reserve line

**Files:**
- Modify: `src/ffdo/web/board/board.js:214-246` (`renderPositionBudget`)

**Interfaces:**
- Consumes: `state.data.budget.by_position` — now `{"QB": {...}, "RB": {...}, "WR": {...}, "TE": {...}, "FLEX": {...}, "BENCH": {...}}`, each value `{"recommended": number, "slots_open": number}` (produced by Task 1).
- Produces: no new interface — `renderPositionBudget()` remains a void function called elsewhere in `board.js` with no arguments.

- [ ] **Step 1: Update `renderPositionBudget()`**

Replace the function body (currently lines 214-246) with:

```javascript
function renderPositionBudget() {
  const el = document.getElementById("position-budget");
  const d = state.data;
  const byPos = d.budget && d.budget.by_position;
  if (d.format === "snake" || !byPos) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  const positions = ["QB", "RB", "WR", "TE", "FLEX"];
  const maxAmount = Math.max(1, ...positions.map(pos => byPos[pos].recommended));

  const posRows = positions.map(pos => {
    const entry = byPos[pos];
    const posColor = `var(--${pos.toLowerCase()}, var(--muted))`;
    return `<div class="posbudget-row">
      <span class="posbudget-pos" style="color:${posColor}">${pos}</span>
      <span class="posbudget-amount">$${entry.recommended}</span>
      <span class="posbudget-slots">${entry.slots_open} slot${entry.slots_open === 1 ? "" : "s"} open</span>
      <div class="posbudget-bar-track">
        <div class="posbudget-bar-fill" style="width:${Math.min(100, (entry.recommended / maxAmount) * 100)}%"></div>
      </div>
    </div>`;
  }).join("");

  document.getElementById("posbudget-rows").innerHTML = posRows + `
    <div class="posbudget-reserve">
      <span>Bench reserve</span>
      <span>$${byPos.BENCH.recommended} · ${byPos.BENCH.slots_open} slot${byPos.BENCH.slots_open === 1 ? "" : "s"} open</span>
    </div>`;
}
```

Note: `var(--flex, var(--muted))` — there's no `--flex` CSS custom property defined in `board.css`, so the FLEX row's label falls back to the existing `--muted` color automatically. No CSS changes needed.

- [ ] **Step 2: Commit**

```bash
git add src/ffdo/web/board/board.js
git commit -m "feat: render FLEX as a position row, relabel bench reserve"
```

---

### Task 3: Manual verification in the browser

**Files:** none (verification only)

**Interfaces:**
- Consumes: the running app at `http://localhost:8000`, backed by Tasks 1-2's changes.

- [ ] **Step 1: Start the dev server**

Run: `uv run uvicorn ffdo.api.app:app --port 8000` (background/long-running — start it, don't wait for it to exit)

- [ ] **Step 2: Open the board in the browser and locate the position-budget panel**

Navigate to `http://localhost:8000`. The "position-budget" panel (top of the sidebar, above the nominated-player card) should render five position rows — QB, RB, WR, TE, FLEX — each with a dollar amount and an "N slot(s) open" label and a bar, followed by a "Bench reserve" line below a divider.

- [ ] **Step 3: Verify FLEX and Bench values look sane**

Confirm:
- The FLEX row shows a dollar amount comparable to (not pinned at $1 like today) the RB/WR/TE rows — it should track real remaining market value, not sit flat at the bottom.
- The "Bench reserve" line still shows a small flat-looking $ total roughly equal to $1 × its open slot count.
- No row is missing, no row shows `undefined` or `NaN`.

- [ ] **Step 4: Check the browser console for errors**

Confirm no JS errors logged (e.g. `Cannot read properties of undefined (reading 'recommended')`) — this would indicate a key mismatch between the API payload and `board.js`.

- [ ] **Step 5: Stop the dev server**

Stop the server process once verification is complete.

No commit for this task — it's manual verification only, confirming Tasks 1-2's changes render correctly together.
