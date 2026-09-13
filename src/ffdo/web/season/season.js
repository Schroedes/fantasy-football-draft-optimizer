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

let _c, _key, _meta, _data;
let _panel = "lineup", _pos = "OVR", _scope = "starters";
let _lineupData = null;   // null until the Lineup tab has been opened at least once
let _tradesData = null;   // null until the Trades tab has been opened at least once

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
<div id="season-body" class="season-two-panel"></div>`;

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
  _panel = "lineup";
  _pos = "OVR";
  _scope = "starters";
  _lineupData = null;
  _tradesData = null;

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
    if (scopeBtn) { _scope = scopeBtn.dataset.scopeTab; render(); }
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
    const slot = `<span class="slot-chip">${escapeHtml(row.slot_label)}</span>`;
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
  if (_tradesData.error) {
    return `<div class="lineup-error">${escapeHtml(_tradesData.error)}</div>`;
  }
  if (_tradesData.trades.length === 0) {
    return `<div class="lineup-empty">No trades in this league yet</div>`;
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
  return `<div class="lineup-list">${rows}</div>`;
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
    const negative = typeof p.value === "number" && p.value < 0;
    const byeText = p.bye_week != null ? `bye ${p.bye_week}` : "";
    const meta = [p.team ? escapeHtml(p.team) : "FA", byeText].filter(Boolean).join(" · ");
    return `<div class="roster-row${p.starter ? "" : " bench"}">
      <span class="slot-chip${p.slot === "BN" ? " bn" : ""}">${escapeHtml(p.slot)}</span>
      <div class="roster-row-main">
        <span class="roster-row-name">${escapeHtml(p.name)}</span>
        <span class="roster-row-meta">${meta}</span>
      </div>
      <span class="roster-row-value${negative ? " neg" : ""}">${value}</span>
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
