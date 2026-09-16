// The post-draft "season" screen: two-panel roster + standings view fed by
// GET /api/leagues/{key}/season. Loaded on demand -- board.js dynamically
// imports this module the moment a poll reports draft_status === "complete"
// (see refresh() in board/board.js) and hands it the same container +
// leagueKey + meta it was itself mounted with. Matches board.js's house
// style: no framework, plain template-literal string building, direct DOM
// queries, module-level singletons (only one season screen is ever mounted
// at a time, same reasoning as board.js's _leagueKey/_container/_meta).
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Display-only shorthand for the two multi-position slots -- FLEX/SUPER_FLEX
// run past the fixed-width .slot-chip, so abbreviate the way Sleeper's own
// UI does (WRT / WRTQ) rather than widening the chip for two labels. Never
// touches the underlying slot value used for logic elsewhere (e.g. the
// p.slot === "BN" check below).
const SLOT_DISPLAY = { FLEX: "WRT", SUPER_FLEX: "WRTQ" };
function slotDisplay(slot) { return SLOT_DISPLAY[slot] || slot; }

let _c, _key, _meta, _data;
let _panel = "lineup", _pos = "OVR", _scope = "starters";
let _lineupData = null;   // null until the Lineup tab has been opened at least once
let _tradesData = null;   // null until the Trades tab has been opened at least once
let _waiversData = null;  // null until the Waivers tab has been opened at least once
let _scorecardData = null; // null until the Scorecard tab has been opened at least once
let _targetsData = null;  // null until the Targets tab has been opened at least once

// ---- trade builder modal state ----
let _tradeBuilderData = null;      // null until the modal has been opened at least once; {teams:[...]} or {error}
let _tradeBuilderOpen = false;
let _tradeBuilderPartnerId = null; // roster_id of the currently-selected trade partner
let _tradeBuilderYourSel = new Set();    // "player:<id>" or "pick:<season>:<round>:<original_roster_id>"
let _tradeBuilderPartnerSel = new Set();
let _tradeBuilderEval = null;       // last POST /trade/evaluate result, {error:true}, or null (nothing selected yet)
let _tradeBuilderEvalPending = false;
let _tradeBuilderEvalTimer = null;
let _tbSuggestions = null;         // last POST /trade/suggestions result, {error:true}, or null (not loaded yet)
let _tbSuggestionsPending = false;

// Sub-strip (week context + refresh) above a two-panel skeleton. #season-body
// itself is deliberately left empty here -- render() populates it (and
// recreates #season-left/#season-right on every call) so a failed load's
// error text (written straight into #season-body, see load()) doesn't leave
// the screen unable to recover: the next successful load() rebuilds both
// panels from scratch rather than assuming they still exist.
const SHELL = `
<div class="season-strip">
  <div class="season-strip-left">
    <span class="badge">SEASON</span>
    <span id="season-week-label" class="season-week-label">&mdash;</span>
  </div>
  <button id="season-refresh" class="season-refresh-btn" type="button">Refresh</button>
</div>
<div id="season-body" class="season-two-panel"></div>
<div id="trade-builder-root"></div>`;

export async function mountSeason(container, leagueKey, meta) {
  // Self-inject season.css the same way board.js's mount() self-injects
  // board.css -- this module can be reached either from the app shell
  // (which only guarantees app.css) or from board/index.html standalone
  // (which only guarantees board.css), and neither carries the
  // season-specific selectors this screen needs.
  if (!document.querySelector('link[href$="season/season.css"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/season/season.css";
    document.head.appendChild(link);
  }
  // Undoes board.js's mount()'s body.board-mode: that class locks <body> to
  // height: 100vh + overflow: hidden, which only works because the board
  // screen's own panels (#board-scroll etc.) carry their own overflow-y:
  // auto. This screen has no such internal scroll container, so leaving
  // the class on left the page unscrollable the moment its content (or a
  // browser zoom level) didn't fit in one viewport height.
  document.body.classList.remove("board-mode");

  _c = container;
  _key = leagueKey;
  _meta = meta;
  // Reset tab/scope state on every mount, not just module load -- the module
  // is cached across a league switch in the shell (re-import resolves to the
  // same instance), so without this a second league would open wearing the
  // first league's tab selection (same reasoning as board.js's freshState()).
  // A command-center flag click (home.js) navigates to
  // "#/league/<key>?tab=<name>" -- read that suffix directly off the
  // current hash rather than threading a parameter through app.js's
  // renderLeague()/board.js's mount(), since both already exist and
  // neither currently passes anything beyond the league key.
  const tabParam = (location.hash.split("?tab=")[1] || "").split("&")[0];
  const validTabs = ["lineup", "power", "capital", "trades", "waivers", "scorecard", "targets"];
  _panel = validTabs.includes(tabParam) ? tabParam : "lineup";
  _pos = "OVR";
  _scope = "starters";
  _lineupData = null;
  _tradesData = null;
  _waiversData = null;
  _scorecardData = null;
  _targetsData = null;
  _tradeBuilderData = null;
  _tradeBuilderOpen = false;
  _tradeBuilderPartnerId = null;
  _tradeBuilderYourSel = new Set();
  _tradeBuilderPartnerSel = new Set();
  _tradeBuilderEval = null;
  _tradeBuilderEvalPending = false;
  clearTimeout(_tradeBuilderEvalTimer);
  _tbSuggestions = null;
  _tbSuggestionsPending = false;

  container.innerHTML = SHELL;
  container.querySelector("#season-refresh").addEventListener("click", load);
  // One delegated listener on the never-replaced #season-body wrapper --
  // render() only ever rewrites its children's innerHTML, so this survives
  // every re-render (tab clicks, refreshes, and error-then-recover) without
  // needing to be re-attached.
  container.querySelector("#season-body").addEventListener("click", (e) => {
    const panelBtn = e.target.closest("[data-panel-tab]");
    if (panelBtn) { _panel = panelBtn.dataset.panelTab; render(); return; }
    const posBtn = e.target.closest("[data-pos-tab]");
    if (posBtn) { _pos = posBtn.dataset.posTab; render(); return; }
    const scopeBtn = e.target.closest("[data-scope-tab]");
    if (scopeBtn) { _scope = scopeBtn.dataset.scopeTab; render(); return; }
    const proposeBtn = e.target.closest("[data-propose-trade]");
    if (proposeBtn) { openTradeBuilder(); return; }
    const targetRow = e.target.closest("[data-target-row]");
    if (targetRow) { onTargetRowClick(Number(targetRow.dataset.targetRow)); }
  });
  // Two delegated listeners on the never-replaced #trade-builder-root --
  // renderTradeBuilderModal() only ever rewrites this element's innerHTML
  // (and clears it entirely when the modal is closed), so these survive
  // every open/close/re-render without needing to be re-attached. Split
  // click vs. change because a checkbox's own click bubbles as change, not
  // click, and the <select> partner picker only ever fires change.
  container.querySelector("#trade-builder-root").addEventListener("click", (e) => {
    if (e.target.closest("[data-tb-close]")) { closeTradeBuilder(); return; }
    const addBtn = e.target.closest("[data-tb-add-suggestion]");
    if (addBtn) { onAddSuggestionToTrade(Number(addBtn.dataset.tbAddSuggestion)); }
  });
  container.querySelector("#trade-builder-root").addEventListener("change", (e) => {
    const partnerSelect = e.target.closest("[data-tb-partner]");
    if (partnerSelect) { onTradeBuilderPartnerChange(partnerSelect.value); return; }
    const checkbox = e.target.closest("[data-tb-item]");
    if (checkbox) { onTradeBuilderToggle(checkbox); }
  });

  await load();
}

async function load() {
  const res = await fetch(`/api/leagues/${encodeURIComponent(_key)}/season`);
  if (!res.ok) {
    _c.querySelector("#season-body").textContent =
      (await res.json().catch(() => ({}))).detail || "Couldn't load the season view";
    return;
  }
  _data = await res.json();
  render();
}

function render() {
  renderWeekLabel();
  const left = renderYourTeam(_data.your_roster, _data.nfl_week);
  const right = renderRightPanel();
  _c.querySelector("#season-body").innerHTML =
    `<div id="season-left" class="season-left">${left}</div>` +
    `<div id="season-right" class="season-right">${right}</div>`;
}

function renderWeekLabel() {
  const el = _c.querySelector("#season-week-label");
  const w = _data.nfl_week;
  if (!w) { el.textContent = "—"; return; }
  const typeSuffix = w.season_type && w.season_type !== "regular" ? ` · ${w.season_type}` : "";
  el.textContent = `NFL Week ${w.week} · ${w.season}${typeSuffix} · values through week ${w.values_through_week}`;
}

function renderRightPanel() {
  const showCapital = Array.isArray(_data.draft_capital);
  const tabBar = `
    <div class="panel-tabs">
      <button data-panel-tab="lineup" class="${_panel === "lineup" ? "on" : ""}">Lineup</button>
      <button data-panel-tab="power" class="${_panel === "power" ? "on" : ""}">Power ranking</button>
      ${showCapital ? `<button data-panel-tab="capital" class="${_panel === "capital" ? "on" : ""}">Draft capital</button>` : ""}
      <button data-panel-tab="trades" class="${_panel === "trades" ? "on" : ""}">Trades</button>
      <button data-panel-tab="waivers" class="${_panel === "waivers" ? "on" : ""}">Waivers</button>
      <button data-panel-tab="scorecard" class="${_panel === "scorecard" ? "on" : ""}">Scorecard</button>
      <button data-panel-tab="targets" class="${_panel === "targets" ? "on" : ""}">Targets</button>
    </div>`;
  if (_panel === "lineup") {
    if (_lineupData === null) {
      loadLineup();
      return tabBar + `<div class="lineup-loading">Loading this week's lineup&hellip;</div>`;
    }
    return tabBar + renderLineup();
  }
  if (_panel === "trades") {
    if (_tradesData === null) {
      loadTrades();
      return tabBar + `<div class="lineup-loading">Loading trades&hellip;</div>`;
    }
    return tabBar + renderTrades();
  }
  if (_panel === "waivers") {
    if (_waiversData === null) {
      loadWaivers();
      return tabBar + `<div class="lineup-loading">Loading waivers&hellip;</div>`;
    }
    return tabBar + renderWaivers();
  }
  if (_panel === "scorecard") {
    if (_scorecardData === null) {
      loadScorecard();
      return tabBar + `<div class="lineup-loading">Loading scorecard&hellip;</div>`;
    }
    return tabBar + renderScorecard();
  }
  if (_panel === "targets") {
    if (_targetsData === null) {
      loadTargets();
      return tabBar + `<div class="lineup-loading">Loading trade targets&hellip;</div>`;
    }
    return tabBar + renderTargets();
  }
  return tabBar + (_panel === "capital" ? renderCapital() : renderPower());
}

async function loadLineup() {
  // Capture which league this fetch is for before the first await, the same
  // way board.js's refresh()/refreshLive() capture _epoch -- mountSeason()
  // reassigns _key synchronously on a league switch, so if it no longer
  // matches by the time (any) await below resolves, this response belongs to
  // a league the user has already navigated away from and must be discarded:
  // no _lineupData assignment, no render(). Without this, a slow response for
  // league A arriving after the user has switched to league B would overwrite
  // B's freshly-reset _lineupData (or its still-loading null) with A's diff.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/lineup`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load the lineup" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load the lineup" };
  }
  _lineupData = result;
  render();
}

function renderLineup() {
  if (_lineupData.error) {
    return `<div class="lineup-error">${escapeHtml(_lineupData.error)}</div>`;
  }
  const d = _lineupData;
  if (d.diff.length === 0) {
    return `<div class="lineup-empty">Roster not available</div>`;
  }
  const header = d.week_locked
    ? `Week ${d.nfl_week.week} — locked, review below`
    : `Week ${d.nfl_week.week} lineup — ${d.swaps_suggested} swap${d.swaps_suggested === 1 ? "" : "s"} suggested`;

  const rows = d.diff.map(row => {
    const slot = `<span class="slot-chip">${escapeHtml(slotDisplay(row.slot_label))}</span>`;
    if (row.status === "match") {
      const cur = row.current
        ? `${escapeHtml(row.current.name)} <span class="lineup-team">${escapeHtml(row.current.team || "")}</span> &middot; ${row.current.value}`
        : `<span class="lineup-team">empty</span>`;
      return `<div class="lineup-row lineup-match">${slot}<span class="lineup-current">${cur}</span></div>`;
    }
    const curName = row.current ? escapeHtml(row.current.name) : "empty";
    const optName = row.optimal ? escapeHtml(row.optimal.name) : "";
    const sign = row.delta > 0 ? "+" : "";
    if (row.status === "suggested_swap") {
      return `<div class="lineup-row lineup-swap">
        ${slot}
        <span class="lineup-current lineup-bench-out">${curName}</span>
        <span class="lineup-arrow">&rarr;</span>
        <span class="lineup-optimal">${optName}</span>
        <span class="lineup-delta">${sign}${row.delta} pts</span>
      </div>`;
    }
    return `<div class="lineup-row lineup-missed">
      ${slot}
      <span class="lineup-current lineup-bench-out">${curName}</span>
      <span class="lineup-missed-label">missed &mdash; ${optName} already locked out</span>
    </div>`;
  }).join("");

  return `<div class="lineup-header">${escapeHtml(header)}</div><div class="lineup-list">${rows}</div>`;
}

async function loadTrades() {
  // Same stale-response guard as loadLineup() above -- capture _key before
  // the first await and bail if the user has switched leagues by the time
  // any await resolves, so a slow response for a league the user has left
  // can't clobber the newly-reset (or still-loading) _tradesData.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trades`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load trades" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load trades" };
  }
  _tradesData = result;
  render();
}

function renderTrades() {
  const proposeBtn = `<div class="trades-toolbar">
    <button class="season-refresh-btn" type="button" data-propose-trade>Propose a trade</button>
  </div>`;
  if (_tradesData.error) {
    return proposeBtn + `<div class="lineup-error">${escapeHtml(_tradesData.error)}</div>`;
  }
  if (_tradesData.trades.length === 0) {
    return proposeBtn + `<div class="lineup-empty">No trades in this league yet</div>`;
  }
  // Players and picks are both "what a side got" -- rendered as one
  // comma-joined list of names/labels rather than two separate lists, since
  // a trade is naturally described as "got X, Y, and pick Z" in one breath.
  const side = (players, picks) => {
    const parts = [
      ...players.map(p => escapeHtml(p.name)),
      ...picks.map(p => escapeHtml(p.label)),
    ];
    return parts.join(", ") || "(nothing)";
  };
  const rows = _tradesData.trades.map(t => {
    const gotA = side(t.roster_a_gets, t.roster_a_picks);
    const gotB = side(t.roster_b_gets, t.roster_b_picks);
    return `<div class="lineup-row trade-row">
      <div>Week ${t.week}: ${escapeHtml(t.roster_a_team_name)} got ${gotA}
        (value at trade ${t.side_a_value_at_trade}, since then
        ${t.current_player_points_delta_a > 0 ? "+" : ""}${t.current_player_points_delta_a} pts
        ${t.roster_a_picks.length ? `, pick value now ${t.current_pick_value_a}` : ""})</div>
      <div>${escapeHtml(t.roster_b_team_name)} got ${gotB}
        (value at trade ${t.side_b_value_at_trade}, since then
        ${t.current_player_points_delta_b > 0 ? "+" : ""}${t.current_player_points_delta_b} pts
        ${t.roster_b_picks.length ? `, pick value now ${t.current_pick_value_b}` : ""})</div>
    </div>`;
  }).join("");
  return proposeBtn + `<div class="lineup-list">${rows}</div>`;
}

async function loadWaivers() {
  // Same stale-response guard as loadLineup()/loadTrades() above -- capture
  // _key before the first await and bail if the user has switched leagues by
  // the time any await resolves, so a slow response for a league the user
  // has left can't clobber the newly-reset (or still-loading) _waiversData.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/waivers`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load waivers" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load waivers" };
  }
  _waiversData = result;
  render();
}

function renderWaivers() {
  if (_waiversData.error) {
    return `<div class="lineup-error">${escapeHtml(_waiversData.error)}</div>`;
  }
  // remaining_budget/suggested_bid are null for a waiver-priority league --
  // there's no FAAB budget to bid a fraction of, but the add/drop call
  // itself is still real VOR-based signal.
  const header = _waiversData.remaining_budget != null
    ? `<div class="lineup-header">Remaining budget: $${_waiversData.remaining_budget}</div>`
    : "";
  if (_waiversData.recommendations.length === 0) {
    return header + `<div class="lineup-empty">No recommended adds right now</div>`;
  }
  const rows = _waiversData.recommendations.map(r => {
    const addLabel = `${escapeHtml(r.free_agent_name)} (${escapeHtml(r.free_agent_position)})`;
    const drop = r.drop_player_id
      ? `drop ${escapeHtml(r.drop_player_name)} (${escapeHtml(r.drop_player_position)})`
      : "open bench slot, no drop needed";
    const bid = r.suggested_bid != null ? `<div>Suggested bid: $${r.suggested_bid}</div>` : "";
    return `<div class="lineup-row waiver-row">
      <div>Add ${addLabel} &mdash; ${drop} &mdash; +${r.vor_gain} VOR</div>
      ${bid}
    </div>`;
  }).join("");
  return header + `<div class="lineup-list">${rows}</div>`;
}

async function loadScorecard() {
  // Same stale-response guard as loadLineup()/loadTrades()/loadWaivers()
  // above -- capture _key before the first await and bail if the user has
  // switched leagues by the time any await resolves, so a slow response for
  // a league the user has left can't clobber the newly-reset (or
  // still-loading) _scorecardData.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/scorecard`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load the scorecard" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load the scorecard" };
  }
  _scorecardData = result;
  render();
}

function renderScorecard() {
  if (_scorecardData.error) {
    return `<div class="lineup-error">${escapeHtml(_scorecardData.error)}</div>`;
  }
  const l = _scorecardData.lineup;
  const t = _scorecardData.trade;
  const w = _scorecardData.waiver;
  const d = _scorecardData.draft;

  const lineupCard = `
    <div class="scorecard-card">
      <h3>Lineup</h3>
      <p>${l.weeks_resolved} weeks resolved &mdash; ${l.weeks_full} fully followed,
         ${l.weeks_partial} partial, ${l.weeks_none} not followed</p>
      <p>Points left on the bench when not followed: ${l.points_left_on_bench}</p>
    </div>`;

  const tradeCard = `
    <div class="scorecard-card">
      <h3>Trades</h3>
      <p>${t.total_trades_in_league} trades in the league, ${t.your_trades} involving you</p>
      <p>Gained value: ${t.gained_value} &middot; Lost value: ${t.lost_value} &middot; Unchanged: ${t.unchanged}</p>
    </div>`;

  const waiverCard = `
    <div class="scorecard-card">
      <h3>Waivers</h3>
      <p>${w.recommended_claims} recommended claims &mdash;
         win rate: ${w.win_rate === null ? "&mdash;" : (w.win_rate * 100).toFixed(0) + "%"}</p>
      <p>Avg bid vs. recommended: ${w.avg_bid_delta === null ? "&mdash;" : w.avg_bid_delta}</p>
    </div>`;

  const draftCard = `
    <div class="scorecard-card">
      <h3>Draft</h3>
      <p>${d.picks_graded} of your picks graded</p>
      <p>GREAT: ${d.grade_counts.GREAT} &middot; GOOD: ${d.grade_counts.GOOD} &middot;
         FAIR: ${d.grade_counts.FAIR} &middot; POOR: ${d.grade_counts.POOR}</p>
    </div>`;

  return `<div class="scorecard-grid">${lineupCard}${tradeCard}${waiverCard}${draftCard}</div>`;
}

async function loadTargets() {
  // Same stale-response guard as loadLineup()/loadTrades()/loadWaivers()/
  // loadScorecard() above.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trade-targets`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load trade targets" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load trade targets" };
  }
  _targetsData = result;
  render();
}

function renderTargets() {
  if (_targetsData.error) {
    return `<div class="lineup-error">${escapeHtml(_targetsData.error)}</div>`;
  }
  const items = _targetsData.suggestions || [];
  if (items.length === 0) {
    return `<div class="lineup-empty">No trade targets clear both the fairness and value-gain bar right now.</div>`;
  }
  const rows = items.map((s, i) => {
    const targets = s.target_players.map(p => escapeHtml(p.name)).join(", ");
    const offers = [...s.offer_players.map(p => escapeHtml(p.name)),
                    ...s.offer_picks.map(p => escapeHtml(p.label))].join(", ") || "(nothing else)";
    return `<div class="lineup-row target-row" data-target-row="${i}">
      <div class="target-row-main">
        <span class="target-partner">${escapeHtml(s.partner_team_name)}</span>
        <span class="target-ask">Ask for ${targets}</span>
        <span class="target-arrow">&harr;</span>
        <span class="target-give">offer ${offers}</span>
      </div>
      <div class="target-row-meta">
        <span class="target-why">${escapeHtml(s.why)}</span>
        <span class="target-gain">+${s.net_value_gain.toFixed(1)} value</span>
      </div>
    </div>`;
  }).join("");
  return `<div class="lineup-list">${rows}</div>`;
}

function onTargetRowClick(index) {
  const s = _targetsData && _targetsData.suggestions && _targetsData.suggestions[index];
  if (!s) return;
  openTradeBuilderWithSuggestion(s);
}

// Mirrors openTradeBuilder() but seeds the partner and both selections
// from a suggestion instead of defaulting to the first other team --
// used by the Targets tab's click-through. Kept separate from
// openTradeBuilder() rather than adding an optional argument there, since
// the two callers' defaulting behavior genuinely differs (first-other-team
// vs. this-specific-suggestion).
function openTradeBuilderWithSuggestion(s) {
  _tradeBuilderOpen = true;
  _tradeBuilderPartnerId = s.partner_roster_id;
  _tradeBuilderPartnerSel = new Set(s.target_players.map(p => `player:${p.player_id}`));
  _tradeBuilderYourSel = new Set([
    ...s.offer_players.map(p => `player:${p.player_id}`),
    ...s.offer_picks.map(p => `pick:${p.season}:${p.round}:${p.original_roster_id}`),
  ]);
  _tradeBuilderEval = null;
  _tbSuggestions = null;
  if (_tradeBuilderData === null) {
    loadTradeBuilder();
  }
  renderTradeBuilderModal();
  if (_tradeBuilderData !== null && !_tradeBuilderData.error) {
    scheduleTradeBuilderEvaluate();
  }
}

function renderPower() {
  const posTabsHtml = ["OVR", "QB", "RB", "WR", "TE"].map(p =>
    `<button data-pos-tab="${p}" class="${_pos === p ? "on" : ""}">${p === "OVR" ? "Overall" : p}</button>`
  ).join("");
  const scopeTabsHtml = [["starters", "Starters only"], ["full", "Full roster"]].map(([id, label]) =>
    `<button data-scope-tab="${id}" class="${_scope === id ? "on" : ""}">${label}</button>`
  ).join("");

  const rows = _pos === "OVR"
    ? _data.power_ranking.overall[_scope]
    : _data.power_ranking.by_position[_pos][_scope];
  const standingsById = new Map(_data.standings.map(s => [s.roster_id, s]));
  const valueLabel = _pos === "OVR" ? "Value" : `${_pos} value`;
  const deltaShown = _pos === "OVR";

  const rowsHtml = rows.map(r => {
    const st = standingsById.get(r.roster_id);
    const rec = st ? `${st.wins}-${st.losses}${st.ties ? `-${st.ties}` : ""}` : "—";
    const pf = st ? st.points_for.toFixed(1) : "—";
    const delta = deltaShown
      ? (r.delta === 0 ? "—" : (r.delta > 0 ? `+${r.delta}` : `${r.delta}`))
      : "·";
    const deltaClass = deltaShown ? (r.delta > 0 ? "pos" : r.delta < 0 ? "neg" : "") : "";
    return `<div class="pr-row${r.is_you ? " you" : ""}">
      <span class="pr-rank">${r.power_rank}</span>
      <span class="pr-team">${escapeHtml(r.team_name)}${r.is_you ? `<span class="you-badge">YOU</span>` : ""}</span>
      <span class="pr-value">${r.value.toFixed(1)}</span>
      <span class="pr-rec">${rec}</span>
      <span class="pr-pf">${pf}</span>
      <span class="pr-delta ${deltaClass}">${delta}</span>
    </div>`;
  }).join("");

  return `
    <div class="power-toolbar">
      <div class="pos-tabs">${posTabsHtml}</div>
      <div class="scope-toggle">${scopeTabsHtml}</div>
    </div>
    <div class="pr-table">
      <div class="pr-row pr-head">
        <span>#</span><span>Team</span><span>${valueLabel}</span><span>Rec</span><span>PF</span><span>&Delta;</span>
      </div>
      ${rowsHtml}
    </div>
    <p class="legend">&Delta; = power-ranking spot minus standings spot. The "Starters only" view drops bench players.</p>`;
}

function renderCapital() {
  const dc = _data.draft_capital;
  if (dc == null) {
    return `<div class="season-empty">Draft capital not available for this league.</div>`;
  }
  if (dc.length === 0) {
    return `<div class="season-empty">No tradeable picks in this league.</div>`;
  }

  const years = Object.keys(dc[0].picks).sort();
  const headCols = years.map(y => `<span>${y} picks</span>`).join("");

  const rowsHtml = dc.map(row => {
    const yearCells = years.map(y => {
      const picks = row.picks[y] || [];
      const chips = picks.length
        ? picks.map(p => {
            const traded = !!p.via_team_name;
            const label = traded ? `${p.label} via ${p.via_team_name}` : p.label;
            return `<span class="pick-chip${traded ? " traded" : ""}">${escapeHtml(label)}</span>`;
          }).join("")
        : `<span class="pick-cell-empty">&mdash;</span>`;
      return `<div class="pick-cell">${chips}</div>`;
    }).join("");
    return `<div class="pr-row cap-row${row.is_you ? " you" : ""}">
      <span class="pr-rank">${row.power_rank}</span>
      <span class="pr-team">${escapeHtml(row.team_name)}${row.is_you ? `<span class="you-badge">YOU</span>` : ""}</span>
      ${yearCells}
    </div>`;
  }).join("");

  return `
    <div class="capital-toolbar">
      <span class="ghost-chip">blended power + capital score &mdash; coming</span>
    </div>
    <div class="pr-table cap-table" style="--cap-cols:${years.length}">
      <div class="pr-row cap-row pr-head">
        <span>#</span><span>Team</span>${headCols}
      </div>
      ${rowsHtml}
    </div>
    <p class="legend">Slot follows the original team's projected finish. <span class="legend-amber">Amber</span> picks came via trade &mdash; label shows "via &lt;team&gt;".</p>`;
}

function renderYourTeam(you, week) {
  if (you == null) {
    return `<div class="season-empty">Roster not available.</div>`;
  }

  const nTeams = _data.standings.length;
  const record = `${you.wins}-${you.losses}${you.ties ? `-${you.ties}` : ""}`;
  const standingsN = you.standings_rank != null ? ordinal(you.standings_rank) : "—";
  const powerN = you.power_rank != null ? ordinal(you.power_rank) : "—";
  const delta = (you.standings_rank != null && you.power_rank != null)
    ? you.standings_rank - you.power_rank : null;
  const deltaLabel = delta == null ? ""
    : delta === 0 ? "even vs standings"
    : delta > 0 ? `+${delta} vs standings` : `${delta} vs standings`;
  const deltaClass = delta == null ? "" : delta > 0 ? "pos" : delta < 0 ? "neg" : "";

  const posBarsHtml = ["QB", "RB", "WR", "TE"].map(pos => {
    const rank = you.positional_rank ? you.positional_rank[pos] : null;
    if (rank == null) {
      return `<div class="pos-bar-row">
        <span class="pos-bar-label">${pos}</span>
        <div class="pos-bar"><div class="pos-bar-fill" style="width:0%"></div></div>
        <span class="pos-bar-rank">&mdash;</span>
      </div>`;
    }
    const pct = Math.max(0, Math.min(100, ((nTeams - rank + 1) / nTeams) * 100));
    const strong = rank <= 3;
    const weak = rank >= nTeams - 2;
    const colorClass = strong ? "strong" : weak ? "weak" : "";
    return `<div class="pos-bar-row">
      <span class="pos-bar-label">${pos}</span>
      <div class="pos-bar"><div class="pos-bar-fill ${colorClass}" style="width:${pct}%"></div></div>
      <span class="pos-bar-rank ${colorClass}">${ordinal(rank)} /${nTeams}</span>
    </div>`;
  }).join("");

  const rosterHtml = (you.players || []).map(p => {
    const value = typeof p.value === "number" ? p.value.toFixed(1) : "—";
    const byeText = p.bye_week != null ? `bye ${p.bye_week}` : "";
    const meta = [p.team ? escapeHtml(p.team) : "FA", byeText].filter(Boolean).join(" · ");
    return `<div class="roster-row${p.starter ? "" : " bench"}">
      <span class="slot-chip${p.slot === "BN" ? " bn" : ""}">${escapeHtml(slotDisplay(p.slot))}</span>
      <div class="roster-row-main">
        <span class="roster-row-name">${escapeHtml(p.name)}</span>
        <span class="roster-row-meta">${meta}</span>
      </div>
      <span class="roster-row-value">${value}</span>
    </div>`;
  }).join("");

  const calloutHtml = you.callout
    ? `<div class="callout">${escapeHtml(you.callout)}</div>` : "";

  return `
    <div class="your-team-header">
      <div class="your-team-id">
        <span class="your-team-kicker">Your team</span>
        <span class="your-team-name">${escapeHtml(you.team_name)}</span>
        <span class="your-team-record">${record} &middot; ${standingsN} of ${nTeams} &middot; ${you.points_for.toFixed(1)} PF</span>
      </div>
      <div class="your-team-rank">
        <span class="your-team-kicker">Power rank</span>
        <span class="your-team-power">${powerN}</span>
        <span class="your-team-delta ${deltaClass}">${deltaLabel}</span>
      </div>
    </div>
    <div class="hr"></div>
    <div class="pos-bars">
      <span class="section-label">Positional strength</span>
      ${posBarsHtml}
    </div>
    <div class="hr"></div>
    <div class="roster-list">
      <div class="roster-list-head"><span>Roster</span><span>Value</span></div>
      ${rosterHtml}
      <div class="bench-value">Bench value ${you.bench_value != null ? you.bench_value.toFixed(1) : "—"}</div>
    </div>
    ${calloutHtml}`;
}

function ordinal(n) {
  if (n == null) return "—";
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}

// ---- trade builder modal ----
// A what-if calculator layered over the Trades tab's real-trade ledger --
// nothing selected here is ever saved. Rendered into #trade-builder-root
// (a sibling of #season-body in SHELL, see mountSeason) rather than inside
// the tab-panel flow render() otherwise owns, since the modal is an overlay
// over the whole screen, not scoped to one tab, and must survive tab
// switches while it's open.

function openTradeBuilder() {
  _tradeBuilderOpen = true;
  _tradeBuilderYourSel = new Set();
  _tradeBuilderPartnerSel = new Set();
  _tradeBuilderEval = null;
  _tbSuggestions = null;
  if (_tradeBuilderData === null) {
    loadTradeBuilder();
  } else if (!_tradeBuilderData.error && _tradeBuilderPartnerId === null) {
    const firstOther = _tradeBuilderData.teams.find(t => !t.is_you);
    _tradeBuilderPartnerId = firstOther ? firstOther.roster_id : null;
  }
  renderTradeBuilderModal();
  if (_tradeBuilderData !== null && !_tradeBuilderData.error && _tradeBuilderPartnerId !== null) {
    fetchTradeSuggestions();
  }
}

function closeTradeBuilder() {
  _tradeBuilderOpen = false;
  clearTimeout(_tradeBuilderEvalTimer);
  renderTradeBuilderModal();
}

async function loadTradeBuilder() {
  // Same stale-response guard as loadLineup()/loadTrades()/loadWaivers()/
  // loadScorecard() above.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trade-builder`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load rosters" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load rosters" };
  }
  _tradeBuilderData = result;
  if (!result.error && _tradeBuilderPartnerId === null) {
    const firstOther = result.teams.find(t => !t.is_you);
    _tradeBuilderPartnerId = firstOther ? firstOther.roster_id : null;
  }
  renderTradeBuilderModal();
  if (!result.error && _tradeBuilderPartnerId !== null) {
    evaluateTradeBuilder();
    fetchTradeSuggestions();
  }
}

function _tbTeam(rosterId) {
  return _tradeBuilderData.teams.find(t => t.roster_id === rosterId);
}

function onTradeBuilderPartnerChange(rosterIdStr) {
  _tradeBuilderPartnerId = Number(rosterIdStr);
  _tradeBuilderPartnerSel = new Set();
  _tradeBuilderEval = null;
  _tbSuggestions = null;
  _tbSuggestionsPending = true;
  renderTradeBuilderModal();
  scheduleTradeBuilderEvaluate();
}

function onTradeBuilderToggle(checkbox) {
  const side = checkbox.dataset.tbSide;
  const itemKey = checkbox.dataset.tbItem;
  const sel = side === "your" ? _tradeBuilderYourSel : _tradeBuilderPartnerSel;
  if (checkbox.checked) sel.add(itemKey); else sel.delete(itemKey);
  renderTradeBuilderModal();
  scheduleTradeBuilderEvaluate();
}

function scheduleTradeBuilderEvaluate() {
  _tradeBuilderEvalPending = true;
  _tbSuggestionsPending = true;
  clearTimeout(_tradeBuilderEvalTimer);
  _tradeBuilderEvalTimer = setTimeout(() => {
    evaluateTradeBuilder();
    fetchTradeSuggestions();
  }, 400);
}

// Picks have no stable id of their own (unlike a player_id) -- the
// selection key encodes (season, round, original_roster_id), the same
// 3-tuple traded_picks.capital() uses to identify a synthesized pick asset,
// so a selection survives re-render and maps back to the exact asset the
// GET /trade-builder response described it with.
function _tbSelectionToPayload(sel, team) {
  const player_ids = [];
  const picks = [];
  sel.forEach((key) => {
    if (key.startsWith("player:")) {
      player_ids.push(key.slice("player:".length));
      return;
    }
    const [, season, round, originalId] = key.split(":");
    const pick = team.picks.find(p =>
      String(p.season) === season && String(p.round) === round &&
      String(p.original_roster_id) === originalId);
    if (pick) {
      picks.push({
        season: pick.season, round: pick.round, projected_slot: pick.projected_slot,
        current_owner_roster_id: pick.current_owner_roster_id,
        original_roster_id: pick.original_roster_id,
      });
    }
  });
  return { player_ids, picks };
}

async function evaluateTradeBuilder() {
  const yourTeam = _tradeBuilderData.teams.find(t => t.is_you);
  const partnerTeam = _tbTeam(_tradeBuilderPartnerId);
  if (!yourTeam || !partnerTeam ||
      (_tradeBuilderYourSel.size === 0 && _tradeBuilderPartnerSel.size === 0)) {
    _tradeBuilderEval = null;
    _tradeBuilderEvalPending = false;
    renderTradeBuilderModal();
    return;
  }
  const myKey = _key;
  const body = {
    partner_roster_id: partnerTeam.roster_id,
    side_a: _tbSelectionToPayload(_tradeBuilderYourSel, yourTeam),
    side_b: _tbSelectionToPayload(_tradeBuilderPartnerSel, partnerTeam),
  };
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trade/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (_key !== myKey) return;
    _tradeBuilderEval = res.ok ? await res.json() : { error: true };
  } catch (e) {
    if (_key !== myKey) return;
    _tradeBuilderEval = { error: true };
  }
  _tradeBuilderEvalPending = false;
  renderTradeBuilderModal();
}

async function fetchTradeSuggestions() {
  const yourTeam = _tradeBuilderData.teams.find(t => t.is_you);
  const partnerTeam = _tbTeam(_tradeBuilderPartnerId);
  if (!yourTeam || !partnerTeam) {
    _tbSuggestions = null;
    _tbSuggestionsPending = false;
    renderTradeBuilderModal();
    return;
  }
  _tbSuggestionsPending = true;
  const myKey = _key;
  const body = {
    partner_roster_id: partnerTeam.roster_id,
    side_a: _tbSelectionToPayload(_tradeBuilderYourSel, yourTeam),
    side_b: _tbSelectionToPayload(_tradeBuilderPartnerSel, partnerTeam),
  };
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trade/suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (_key !== myKey) return;
    _tbSuggestions = res.ok ? await res.json() : { error: true };
  } catch (e) {
    if (_key !== myKey) return;
    _tbSuggestions = { error: true };
  }
  _tbSuggestionsPending = false;
  renderTradeBuilderModal();
}

function onAddSuggestionToTrade(index) {
  const s = _tbSuggestions && _tbSuggestions.suggestions && _tbSuggestions.suggestions[index];
  if (!s) return;
  s.target_players.forEach(p => _tradeBuilderPartnerSel.add(`player:${p.player_id}`));
  s.offer_players.forEach(p => _tradeBuilderYourSel.add(`player:${p.player_id}`));
  s.offer_picks.forEach(p => _tradeBuilderYourSel.add(`pick:${p.season}:${p.round}:${p.original_roster_id}`));
  renderTradeBuilderModal();
  scheduleTradeBuilderEvaluate();
}

function tbBackdrop(inner) {
  return `<div class="tb-backdrop">${inner}</div>`;
}

function tbRowHTML(item, side, key, sel, isPick) {
  const checked = sel.has(key) ? "checked" : "";
  const label = isPick ? item.label : item.name;
  const chip = isPick
    ? `<span class="tb-pos-chip tb-pos-chip-pick">PICK</span>`
    : `<span class="tb-pos-chip">${escapeHtml(item.position)}</span>`;
  return `<label class="tb-row ${sel.has(key) ? "checked" : ""}">
    <input type="checkbox" data-tb-item="${escapeHtml(key)}" data-tb-side="${side}" ${checked} />
    <span class="tb-row-main">
      <span class="tb-player-name">${escapeHtml(label)}</span>
      ${chip}
    </span>
    <span class="tb-row-value">${item.value.toFixed(1)}</span>
  </label>`;
}

function tbSideHTML(team, side, sel) {
  const playerRows = team.players
    .map(p => tbRowHTML(p, side, `player:${p.player_id}`, sel, false))
    .join("") || `<div class="tb-empty">No players</div>`;
  const pickRows = team.picks
    .map(p => tbRowHTML(p, side, `pick:${p.season}:${p.round}:${p.original_roster_id}`, sel, true))
    .join("") || `<div class="tb-empty">No future picks</div>`;
  return `
    <p class="tb-section-label">Players</p>
    <div class="tb-roster-list">${playerRows}</div>
    <p class="tb-section-label">Future picks</p>
    <div class="tb-roster-list">${pickRows}</div>`;
}

function tbScoreboardHTML() {
  const updating = _tradeBuilderEvalPending ? "updating" : "";
  let yourValue = "0.0", partnerValue = "0.0", diffText = "&mdash;", diffClass = "";
  let verdict = "Select players or picks on both sides to evaluate.";

  if (_tradeBuilderEval && _tradeBuilderEval.error) {
    verdict = "Couldn't evaluate this trade -- try again.";
  } else if (_tradeBuilderEval) {
    yourValue = _tradeBuilderEval.side_a_value.toFixed(1);
    partnerValue = _tradeBuilderEval.side_b_value.toFixed(1);
    const diff = _tradeBuilderEval.differential;
    diffText = `${diff > 0 ? "+" : ""}${diff.toFixed(1)}`;
    const mag = Math.abs(diff);
    if (mag < 5) {
      verdict = "Fair trade";
    } else if (diff > 0) {
      verdict = mag < 20 ? "Slight edge: you" : "Lopsided: favors you";
      diffClass = "gain";
    } else {
      verdict = mag < 20 ? "Slight edge: partner" : "Lopsided: favors partner";
      diffClass = "loss";
    }
  }

  return `
    <div class="tb-scoreboard">
      <div class="tb-sb-value">
        <p class="tb-sb-label">Your value</p>
        <p class="tb-sb-number ${updating}">${yourValue}</p>
      </div>
      <div class="tb-sb-center">
        <p class="tb-sb-diff ${diffClass} ${updating}">${diffText}</p>
        <p class="tb-sb-verdict">${escapeHtml(verdict)}</p>
      </div>
      <div class="tb-sb-value">
        <p class="tb-sb-label">Partner value</p>
        <p class="tb-sb-number ${updating}">${partnerValue}</p>
      </div>
    </div>`;
}

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

function tbSuggestionsHTML() {
  if (!_tbSuggestions || _tbSuggestions.error) return "";
  const items = _tbSuggestions.suggestions || [];
  const updating = _tbSuggestionsPending ? "updating" : "";
  if (items.length === 0) {
    return `
      <div class="tb-suggestions ${updating}">
        <p class="tb-section-label">Suggested additions</p>
        <p class="tb-empty">No additional players clear both the fairness and value-gain bar right now.</p>
      </div>`;
  }
  const rows = items.map((s, i) => {
    const targets = s.target_players.map(p => escapeHtml(p.name)).join(", ");
    const offers = [...s.offer_players.map(p => escapeHtml(p.name)),
                    ...s.offer_picks.map(p => escapeHtml(p.label))].join(", ") || "(nothing else)";
    return `<div class="tb-suggestion-row">
      <div class="tb-suggestion-main">
        <span class="tb-suggestion-ask">Ask for ${targets}</span>
        <span class="tb-suggestion-arrow">&harr;</span>
        <span class="tb-suggestion-give">offer ${offers}</span>
        <span class="tb-suggestion-gain">+${s.net_value_gain.toFixed(1)} value</span>
      </div>
      <button class="tb-add-btn" type="button" data-tb-add-suggestion="${i}">Add to trade</button>
    </div>`;
  }).join("");
  return `
    <div class="tb-suggestions ${updating}">
      <p class="tb-section-label">Suggested additions</p>
      <div class="tb-suggestions-list">${rows}</div>
    </div>`;
}

function renderTradeBuilderModal() {
  const root = document.getElementById("trade-builder-root");
  if (!root) return;
  if (!_tradeBuilderOpen) { root.innerHTML = ""; return; }

  if (_tradeBuilderData === null) {
    root.innerHTML = tbBackdrop(`<div class="tb-modal tb-modal-message">
      <div class="lineup-loading">Loading rosters&hellip;</div>
    </div>`);
    return;
  }
  if (_tradeBuilderData.error) {
    root.innerHTML = tbBackdrop(`<div class="tb-modal tb-modal-message">
      <div class="lineup-error">${escapeHtml(_tradeBuilderData.error)}</div>
    </div>`);
    return;
  }

  const yourTeam = _tradeBuilderData.teams.find(t => t.is_you);
  const otherTeams = _tradeBuilderData.teams.filter(t => !t.is_you);
  const partnerTeam = _tbTeam(_tradeBuilderPartnerId) || otherTeams[0];

  const partnerOptions = otherTeams.map(t =>
    `<option value="${t.roster_id}" ${partnerTeam && t.roster_id === partnerTeam.roster_id ? "selected" : ""}>${escapeHtml(t.team_name)}</option>`
  ).join("");

  const partnerSideHtml = partnerTeam
    ? tbSideHTML(partnerTeam, "partner", _tradeBuilderPartnerSel)
    : `<div class="lineup-empty">No other teams in this league</div>`;

  root.innerHTML = tbBackdrop(`
    <div class="tb-modal">
      <div class="tb-head">
        <div class="tb-titles">
          <p class="tb-eyebrow">Hypothetical trade</p>
          <h2 class="tb-title">Trade Machine</h2>
        </div>
        <button class="tb-close" type="button" data-tb-close aria-label="Close">&times;</button>
      </div>
      <div class="tb-sides">
        <div class="tb-side">
          <div class="tb-side-head">
            <p class="tb-side-label">Your team</p>
            <p class="tb-team-name">${escapeHtml(yourTeam.team_name)}</p>
          </div>
          ${tbSideHTML(yourTeam, "your", _tradeBuilderYourSel)}
        </div>
        <div class="tb-side">
          <div class="tb-side-head">
            <p class="tb-side-label">Trade partner</p>
            <select class="tb-team-picker" data-tb-partner aria-label="Choose trade partner">${partnerOptions}</select>
          </div>
          ${partnerSideHtml}
        </div>
      </div>
      ${tbScoreboardHTML()}
      ${tbNeedsHTML()}
      ${tbSuggestionsHTML()}
      <div class="tb-foot">
        <p class="tb-hint">This is a what-if calculator only &mdash; nothing here is saved. Real completed trades still show up in the ledger below once they happen.</p>
        <button class="tb-done-btn" type="button" data-tb-close>Done</button>
      </div>
    </div>`);
}
