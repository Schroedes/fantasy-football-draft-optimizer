# Non-Negative VOR Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Season screen's Power Ranking tab (team "Value" totals, "Bench value", and the per-player "Value" column on the Your Team roster list) from ever displaying a negative number, without changing any value that other engine logic (waiver suggestions, trade evaluation, roster-needs ranking) actually computes with.

**Architecture:** VOR (value over replacement) is deliberately zero-anchored: a player below the league's replacement level at his position has genuine negative value, and every real calculation in the engine (waiver gain, trade before/after deltas, draft optimization) depends on that zero anchor being real, not shifted. A global additive rescale would also be inconsistent across screens, since different endpoints value different-sized player pools. Instead we add an opt-in, display-only "clip negative contributions to zero" mode to the one function that sums per-player VOR into a team total (`engine/power_ranking.py::team_value`), defaulting to off everywhere it already gets called (`rank()`, `trade_targets.py`, `roster_needs.py` are untouched), and turn it on only in the two places `api/app.py` builds JSON for the Season screen. Ranking order (`power_rank`, `delta`) keeps coming from the real, unclipped numbers — only the printed "Value" text changes.

**Tech Stack:** Python (FastAPI backend, `src/ffdo/engine`, `src/ffdo/api`), vanilla JS frontend (`src/ffdo/web/season`), pytest.

**Spec:** No separate spec doc — this plan was scoped directly from a design discussion in chat. Context: the user found it confusing that (a) individual players on the Your Team roster list can show a negative "Value", and (b) a team's total "Value" in Power Ranking can go *down* when switching from "Starters only" to "Full roster" (because that sums in bench players who are below replacement level, i.e. have negative VOR).

## Global Constraints

- Do not change the raw `ValuedPlayer.vor` field or any function that other engine modules rely on for real math (`engine/roster.py::team_lineup`, `engine/trade_targets.py`, `engine/roster_needs.py`, `engine/waiver_value.py`). Those must produce byte-identical output after this change.
- The new clipping behavior must be opt-in (default `False`) on any function whose default behavior is depended on elsewhere.
- Only the Season screen's Power Ranking tab and Your Team roster list are in scope. The Waivers tab already never shows a negative number today (`recommend_adds` filters to `vor_gain > min_vor_gain`, default `5.0`) — verified by reading `engine/waiver_value.py` and `web/season/season.js`; no change needed there.

---

## File Structure

- Modify `src/ffdo/engine/power_ranking.py` — `team_value()` gains a `clip_negative: bool = False` keyword-only param. When `True`, every per-player VOR contribution to a sum is floored at `0.0` before summing. `rank()` is untouched (still calls `team_value()` with the default `False`), so `power_rank`/`delta` ordering and every existing caller (`roster_needs.py`, `trade_targets.py`) behave exactly as before.
- Modify `src/ffdo/api/app.py` — the `_rank()` closure inside `_assemble_season()` (builds the Power Ranking JSON rows) and `_your_roster_payload()` (builds the Your Team JSON, including the per-player roster list) both switch their displayed "value"/"bench_value" numbers to the clipped equivalent, while leaving `power_rank`/`delta`/sort order computed from the real (unclipped) numbers.
- Modify `src/ffdo/web/season/season.js` — remove the now-dead `negative`/`.neg` class logic on the roster-row value (it can never be negative anymore).
- Modify `src/ffdo/web/season/season.css` — remove the now-unused `.roster-row-value.neg` rule.
- Test `tests/engine/test_power_ranking.py` — new tests for `team_value(..., clip_negative=True)`.
- Test `tests/api/test_season_endpoint.py` — new test proving a below-replacement bench player's displayed value is `0.0`, not negative, end to end through the real HTTP endpoint.

---

### Task 1: `team_value()` gains an opt-in `clip_negative` mode

**Files:**
- Modify: `src/ffdo/engine/power_ranking.py:19-42` (the `team_value` function)
- Test: `tests/engine/test_power_ranking.py`

**Interfaces:**
- Consumes: nothing new — same `RosterEntry`, `Mapping[str, ValuedPlayer]`, `league`, `position: str`, `scope: str` this function already takes.
- Produces: `team_value(entry, valued, league, *, position, scope, clip_negative=False) -> tuple[float, float]` — unchanged 2-tuple `(value, bench_value)` return shape. `rank()` and every other existing caller keep calling it without the new kwarg, so their behavior is unchanged (verified by Step 4 below, which reruns the existing test suite unmodified).

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_power_ranking.py` (this file already has a `VALUED`/`ROSTERS`/`STANDINGS` fixture at module scope and a `_vp(pid, pos, v)` helper — reuse both):

```python
def test_clip_negative_floors_each_players_contribution_at_zero():
    # Team 4: reuses q2/w2/f2/r2 from the module fixture (QB 25, WR 20, WR 18,
    # RB 22) plus one new deeply-negative bench RB. With slots QB/RB/WR/FLEX,
    # this team's own lineup fill seats q2, r2, w2 in their dedicated slots
    # and f2 (18) in FLEX over r4c (-15) -- r4c is left on the bench, exactly
    # the "one bad bench player" case the clip is meant to fix.
    valued = dict(VALUED)
    valued["r4c"] = _vp("r4c", "RB", -15)
    entry = _entry(4, ["q2", "r2", "w2", "f2", "r4c"])

    raw_value, raw_bench = power_ranking.team_value(
        entry, valued, _league(), position="RB", scope="full")
    assert raw_bench < 0          # sanity check: this fixture really does produce a negative sum today

    clipped_value, clipped_bench = power_ranking.team_value(
        entry, valued, _league(), position="RB", scope="full", clip_negative=True)
    assert clipped_bench == 0.0   # r4c's -15 contributes 0, not -15
    assert clipped_value == 22.0  # r2 (22) + r4c (clipped to 0)


def test_clip_negative_defaults_to_off():
    # No kwarg passed -- must match pre-existing behavior exactly.
    entry = _entry(2, ["q2", "r2", "w2", "f2", "b2a", "b2b"])
    a = power_ranking.team_value(entry, VALUED, _league(), position="OVR", scope="full")
    b = power_ranking.team_value(entry, VALUED, _league(), position="OVR", scope="full", clip_negative=False)
    assert a == b
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/engine/test_power_ranking.py -v`
Expected: `test_clip_negative_floors_each_players_contribution_at_zero` FAILs with `TypeError: team_value() got an unexpected keyword argument 'clip_negative'`. `test_clip_negative_defaults_to_off` passes trivially (it doesn't need the new param) — that's fine, it's there to pin down the no-op default once the param exists.

- [ ] **Step 3: Implement `clip_negative`**

Replace the body of `team_value()` in `src/ffdo/engine/power_ranking.py`:

```python
def team_value(
    entry: RosterEntry,
    valued: Mapping[str, ValuedPlayer],
    league,
    *,
    position: str,
    scope: str,
    clip_negative: bool = False,
) -> tuple[float, float]:
    """Returns (value, bench_value) for one team at one (position, scope).

    `clip_negative` floors each player's own VOR at 0.0 before summing --
    display-only, for screens where a below-replacement bench player
    dragging the team *total* negative reads as broken rather than as the
    (correct) fact that he's worth less than a free-agent pickup. Who
    starts (`lineup.starters`, from `team_lineup`) is still decided by the
    real, unclipped VOR -- only the printed sum changes. Defaults to False
    so every existing caller (`rank()`, and through it `roster_needs.py`
    and `trade_targets.py`, whose before/after trade deltas need the real
    zero-anchored numbers to stay additive) is unaffected.
    """
    team_valued = {pid: valued[pid] for pid in entry.player_ids if pid in valued}
    lineup = team_lineup(team_valued, league)

    def contrib(vp: ValuedPlayer) -> float:
        return max(vp.vor, 0.0) if clip_negative else vp.vor

    if position == "OVR":
        starting = sum(contrib(vp) for pid, vp in team_valued.items() if pid in lineup.starters)
        if scope == "full":
            bench = sum(contrib(vp) for pid, vp in team_valued.items() if pid not in lineup.starters)
            return starting + bench, bench
        return starting, 0.0

    at_pos = {pid: vp for pid, vp in team_valued.items() if vp.profile.position == position}
    started = sum(contrib(vp) for pid, vp in at_pos.items() if pid in lineup.starters)
    if scope == "starters":
        return started, 0.0
    total = sum(contrib(vp) for vp in at_pos.values())
    return total, total - started
```

Note this also refactors the `OVR` branch to compute `starting` from `team_valued`/`lineup.starters` directly instead of reading `lineup.starting_vor`/`lineup.bench_vor` — this is arithmetically identical when `clip_negative=False` (both sum the same players' raw `.vor`), it's just the only way to share one code path with the clipped case. Step 4 proves this refactor changed nothing observable.

- [ ] **Step 4: Run tests to verify they pass, and that nothing else moved**

Run: `pytest tests/engine/test_power_ranking.py -v`
Expected: all PASS, including every pre-existing test in that file unmodified (`test_overall_starters_ranks_by_starting_lineup_value`, `test_full_scope_can_reorder_vs_starters`, `test_position_ranking_respects_scope`, `test_flex_started_player_counts_toward_its_own_position`, `test_delta_sign_positive_when_roster_beats_record`).

Also run the two engine modules that call `team_value()`/`rank()` downstream, to confirm zero behavior change there:
Run: `pytest tests/engine/test_trade_targets.py tests/engine/test_roster.py -v`
Expected: all PASS, unmodified.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/power_ranking.py tests/engine/test_power_ranking.py
git commit -m "engine: add opt-in clip_negative display mode to power_ranking.team_value"
```

---

### Task 2: Wire the Season screen to the clipped display values

**Files:**
- Modify: `src/ffdo/api/app.py:1110-1160` (`_your_roster_payload`) and `src/ffdo/api/app.py:1234-1243` (the `_rank` closure inside `_assemble_season`)
- Modify: `src/ffdo/web/season/season.js:689-701` (roster row rendering)
- Modify: `src/ffdo/web/season/season.css:128` (drop the now-dead rule)
- Test: `tests/api/test_season_endpoint.py`

**Interfaces:**
- Consumes: `power_ranking_mod.team_value(entry, valued, league, *, position, scope, clip_negative=False)` from Task 1.
- Produces: no new functions — same `/api/leagues/{league_key}/season` JSON shape (`your_roster.players[].value`, `your_roster.bench_value`, `power_ranking.overall/by_position[...][...][].value`, `[...].bench_value`), just non-negative values.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/api/test_season_endpoint.py`. This extends the module's existing `_PLAYERS`/`_PROJ`/`_ROSTERS` fixtures with four extra bench RBs on team 1, chosen so the replacement-level math (`engine/replacement.py`, greedy-fills 2 dedicated RB slots + 2 more via the league's single FLEX slot, leaguewide, since `num_teams=2`) leaves the two weakest of them outside every slot -- meaning the league's real RB replacement level lands on the 5th-best RB (30 rush-yard points), and the 6th-best (dummy RB "D", 20 points) has genuine raw VOR of `20 - 30 = -10`. Today that surfaces as `"value": -10.0` in the JSON; after this task it must be `0.0`.

```python
def test_below_replacement_bench_player_never_shows_negative_value(monkeypatch, tmp_path):
    players = dict(_PLAYERS)
    proj = list(_PROJ)
    for name, rush_yd in (("A", 500), ("B", 400), ("C", 300), ("D", 200)):
        pid = f"p_rb_{name}"
        players[pid] = {"first_name": "Dummy", "last_name": name, "position": "RB",
                         "team": "ZZZ", "age": 25, "years_exp": 2, "active": True}
        proj.append({"player_id": pid,
                     "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
                     "stats": {"rush_yd": rush_yd}})
    rosters = [
        {**_ROSTERS[0], "players": _ROSTERS[0]["players"] + [f"p_rb_{n}" for n in "ABCD"]},
        _ROSTERS[1],
    ]
    _seed(monkeypatch, tmp_path, _tracked())
    # Override the client _seed just installed with one serving the extended
    # roster/player/projection fixtures built above.
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client({
        f"{V1}/league/L1/rosters": rosters,
        f"{V1}/players/nfl": players,
        "/projections/": proj,
    }))

    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res.status_code == 200
    body = res.json()

    by_id = {p["player_id"]: p for p in body["your_roster"]["players"]}
    # Dummy "D" (200 rush yards -> 20 pts) is below this league's real RB
    # replacement level (30 pts, set by dummy "C") -- raw VOR is -10.
    assert by_id["p_rb_D"]["value"] == 0.0
    assert all(p["value"] >= 0 for p in body["your_roster"]["players"])
    assert body["your_roster"]["bench_value"] >= 0

    rb_full = next(r for r in body["power_ranking"]["by_position"]["RB"]["full"]
                   if r["roster_id"] == 1)
    assert rb_full["value"] >= 0
    assert rb_full["bench_value"] >= 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/api/test_season_endpoint.py::test_below_replacement_bench_player_never_shows_negative_value -v`
Expected: FAILs on `assert by_id["p_rb_D"]["value"] == 0.0` — today it reports `-10.0`.

- [ ] **Step 3: Wire `_rank()` to the clipped values**

In `src/ffdo/api/app.py`, `_assemble_season()` already has `rosters: list[RosterEntry]` in scope. Just above the `def _rank(...)` closure (around line 1234), add a lookup map, then change the closure to overlay clipped value/bench_value:

```python
        rosters_by_id = {r.roster_id: r for r in rosters}

        def _rank(position: str, scope: str) -> list[dict]:
            rows = power_ranking_mod.rank(
                rosters, valued, lg, standings_rank, lg.roster_id,
                position=position, scope=scope)
            out = []
            for row in rows:
                # power_rank/delta above come from the real (unclipped) VOR --
                # only the printed value/bench_value are display-clipped, so a
                # bad bench player can't drag a team's printed total negative
                # (see power_ranking.team_value's clip_negative docstring).
                display_value, display_bench = power_ranking_mod.team_value(
                    rosters_by_id[row.roster_id], valued, lg,
                    position=position, scope=scope, clip_negative=True)
                out.append({
                    "roster_id": row.roster_id, "team_name": row.team_name,
                    "is_you": row.is_you, "value": round(display_value, 1),
                    "bench_value": round(display_bench, 1), "power_rank": row.power_rank,
                    "standings_rank": row.standings_rank, "delta": row.delta,
                })
            return out
```

This replaces the existing `_rank` closure body (the current one-line list comprehension using `row.value`/`row.bench_value` directly).

- [ ] **Step 4: Wire `_your_roster_payload()` to the clipped per-player value**

In `_your_roster_payload()` (around line 1118-1137), change the player list to sort by real VOR but display the clipped value:

```python
        starters = set(you.starter_ids)

        def _raw_vor(pid: str) -> float:
            vp = valued.get(pid)
            return vp.vor if vp is not None else 0.0

        # Same silent omission `ros_value.roster_value` already applies: a
        # rostered id with no Sleeper profile (a stale crosswalk miss, an
        # offseason-only id) has no name, position or value to show, and
        # inventing zeros for it would put a phantom row on the user's roster.
        ordered_ids = sorted(
            (pid for pid in you.player_ids if profiles.get(pid) is not None),
            key=lambda pid: (pid not in starters, -_raw_vor(pid)))
        players = []
        for pid in ordered_ids:
            prof = profiles[pid]
            vp = valued.get(pid)
            players.append({
                "player_id": pid, "name": prof.full_name, "position": prof.position,
                "team": prof.team, "slot": prof.position if pid in starters else "BN",
                "starter": pid in starters,
                "value": round(max(vp.vor, 0.0), 1) if vp else 0.0,
                "age": prof.age, "bye_week": byes.get(prof.team or ""),
                "injury_status": prof.injury_status,
            })
```

This replaces the existing `for pid in you.player_ids: ... players.append(...)` loop and the `players.sort(...)` line that followed it — sorting now happens up front on the id list (by real, unclipped VOR, so bench ordering among several below-replacement players is still meaningful internally), and the displayed `"value"` is the clipped one built directly into each dict. The skip-if-no-profile comment moves up to the new filter but keeps its original wording.

`bench_value` in the same function's return dict already reads `bench_full["bench_value"]`, which now comes from the clipped `_rank("OVR", "full")` result from Step 3 — no separate change needed there.

- [ ] **Step 5: Run the integration test to verify it passes**

Run: `pytest tests/api/test_season_endpoint.py -v`
Expected: all PASS, including the new test and every pre-existing one in the file (`test_season_payload_is_well_formed`, `test_dynasty_league_gets_draft_capital`, `test_cross_format_guard_same_roster_different_values`, etc. — none of their assertions touch exact `value` numbers in a way this changes, since their fixtures don't produce negative VOR in the first place).

- [ ] **Step 6: Drop the now-dead negative styling in the frontend**

In `src/ffdo/web/season/season.js`, replace the `rosterHtml` mapping (around line 689-701):

```javascript
  const rosterHtml = (you.players || []).map(p => {
    const value = typeof p.value === "number" ? p.value.toFixed(1) : "—";
    const byeText = p.bye_week != null ? `bye ${p.bye_week}` : "";
    const meta = [p.team ? escapeHtml(p.team) : "FA", byeText].filter(Boolean).join(" · ");
    return `<div class="roster-row${p.starter ? "" : " bench"}">
      <span class="slot-chip${p.slot === "BN" ? " bn" : ""}">${escapeHtml(p.slot)}</span>
      <div class="roster-row-main">
        <span class="roster-row-name">${escapeHtml(p.name)}</span>
        <span class="roster-row-meta">${meta}</span>
      </div>
      <span class="roster-row-value">${value}</span>
    </div>`;
  }).join("");
```

(Removes the `negative` variable and the `${negative ? " neg" : ""}` class it fed — `p.value` can no longer be negative, so that branch was always going to be dead.)

In `src/ffdo/web/season/season.css`, delete line 128:

```css
.roster-row-value.neg { color: var(--red); }
```

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py src/ffdo/web/season/season.js src/ffdo/web/season/season.css tests/api/test_season_endpoint.py
git commit -m "api/web: clip Season screen VOR display at zero, never show negative value"
```

---

## Self-Review Notes

- **Spec coverage:** Both user complaints are addressed — individual negative player values (Task 2, Step 4/6) and team totals dropping when more (bench) players are included (Task 1 + Task 2 Step 3, via `clip_negative` flooring each contribution before summing instead of letting negative contributions subtract from the total).
- **Math safety:** `roster_needs.py` and `trade_targets.py` never pass `clip_negative`, so they keep getting the real, zero-anchored, additive VOR their before/after trade-delta and needs-ranking math require. `rank()`'s own sort/`power_rank`/`delta` also stay on the unclipped numbers — only the second, display-only `team_value(..., clip_negative=True)` call in `_rank()` (Task 2 Step 3) feeds the printed text.
- **Waivers tab:** confirmed out of scope — `recommend_adds` already filters to `vor_gain > min_vor_gain` (default `5.0`), so it never displays a negative number today. No task touches `engine/waiver_value.py` or the Waivers tab rendering in `season.js`.
