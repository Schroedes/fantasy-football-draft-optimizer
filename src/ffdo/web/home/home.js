// The command-center home screen: a grid of cards, one per tracked
// league, matching board.js/season.js's house style -- plain ES module
// exporting mount(), module-level singleton state (only one home screen
// is ever mounted at a time), direct DOM string-building, no framework.
const LAST_LEAGUE_KEY = "ffdo:lastLeagueKey";
const POLL_INTERVAL_MS = 60000;

let _c, _leagues, _summaries, _pollTimer;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export async function mount(container) {
  _c = container;
  _summaries = new Map();  // league_key -> {status: "loading"|"ok"|"error", data|error}
  clearInterval(_pollTimer);

  const res = await fetch("/api/leagues");
  _leagues = res.ok ? await res.json() : [];

  if (_leagues.length === 0) {
    location.hash = "#/connect";
    return;
  }

  render();
  loadAllSummaries();
  _pollTimer = setInterval(loadAllSummaries, POLL_INTERVAL_MS);
}

export function unmount() {
  clearInterval(_pollTimer);
  _c = null;
}

function loadAllSummaries() {
  for (const lg of _leagues) {
    if (lg.draft_status !== "complete") continue;
    loadOneSummary(lg.league_key);
  }
}

async function loadOneSummary(leagueKey) {
  const prior = _summaries.get(leagueKey);
  _summaries.set(leagueKey, { status: "loading", data: prior && prior.data });
  if (!prior) render();
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(leagueKey)}/home-summary`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      _summaries.set(leagueKey, { status: "error", error: body.detail || "Couldn't load this league" });
    } else {
      _summaries.set(leagueKey, { status: "ok", data: await res.json() });
    }
  } catch (e) {
    _summaries.set(leagueKey, { status: "error", error: "Couldn't load this league" });
  }
  render();
}

function render() {
  if (!_c) return;
  const sorted = [..._leagues].sort((a, b) => a.name.localeCompare(b.name));

  let continueHtml = "";
  let lastKey = null;
  try { lastKey = localStorage.getItem(LAST_LEAGUE_KEY); } catch {}
  const lastLeague = lastKey ? _leagues.find(l => l.league_key === lastKey) : null;
  if (lastLeague) {
    continueHtml = `<div class="home-continue">
      <span>Pick up where you left off</span>
      <a href="#/league/${encodeURIComponent(lastLeague.league_key)}">Continue: ${escapeHtml(lastLeague.name)} &rarr;</a>
    </div>`;
  }

  const cards = sorted.map(lg => cardHtml(lg)).join("");
  _c.innerHTML = `${continueHtml}<div class="home-grid">${cards}</div>`;

  _c.querySelectorAll("[data-home-card]").forEach(el => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-home-retry]")) return;
      const tab = e.target.closest("[data-home-flag]");
      const key = el.dataset.homeCard;
      if (tab) { location.hash = `#/league/${encodeURIComponent(key)}?tab=${tab.dataset.homeFlag}`; return; }
      location.hash = `#/league/${encodeURIComponent(key)}`;
    });
  });
  _c.querySelectorAll("[data-home-retry]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      loadOneSummary(btn.dataset.homeRetry);
    });
  });
}

function cardHtml(lg) {
  if (lg.draft_status !== "complete") {
    return `<div class="home-card predraft" data-home-card="${escapeHtml(lg.league_key)}">
      <div class="home-card-name">${escapeHtml(lg.name)}</div>
      <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}</div>
      <span class="home-predraft-status">${escapeHtml(lg.draft_status || "Pre-draft")}</span>
    </div>`;
  }

  const state = _summaries.get(lg.league_key);
  if (!state || state.status === "loading" && !state.data) {
    return `<div class="home-card" data-home-card="${escapeHtml(lg.league_key)}">
      <div class="home-card-head"><div class="home-card-name">${escapeHtml(lg.name)}</div></div>
      <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}</div>
      <div class="home-card-skeleton">Loading&hellip;</div>
    </div>`;
  }
  if (state.status === "error") {
    return `<div class="home-card" data-home-card="${escapeHtml(lg.league_key)}">
      <div class="home-card-head"><div class="home-card-name">${escapeHtml(lg.name)}</div></div>
      <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}</div>
      <div class="home-card-error">${escapeHtml(state.error)}</div>
      <button class="home-card-retry" type="button" data-home-retry="${escapeHtml(lg.league_key)}">Retry</button>
    </div>`;
  }

  const d = state.data;
  const hasFlags = d.flags && Object.keys(d.flags).length > 0;
  const record = d.record ? `${d.record.wins}-${d.record.losses}${d.record.ties ? `-${d.record.ties}` : ""}` : "";

  let rankHtml = "";
  if (d.power_rank) {
    const delta = d.power_rank.delta_vs_standings;
    const deltaText = delta == null ? "" : (delta > 0 ? `+${delta}` : `${delta}`);
    const deltaClass = delta != null && delta < 0 ? "neg" : "";
    rankHtml = `<div class="home-rank-row">
      <span class="home-rank-big">${ordinal(d.power_rank.value)}</span>
      <span class="home-rank-label">of ${d.power_rank.of} &middot; power rank</span>
      ${deltaText ? `<span class="home-rank-delta ${deltaClass}">${deltaText}</span>` : ""}
    </div>`;
  }

  let matchupHtml = "";
  if (d.matchup) {
    matchupHtml = `<div class="home-matchup">
      <span class="home-matchup-score">${d.matchup.your_projected.toFixed(1)}</span>
      <span class="home-matchup-vs">vs</span>
      <span class="home-matchup-opp">${d.matchup.opponent_projected.toFixed(1)}<br>${escapeHtml(d.matchup.opponent_name)}</span>
    </div>`;
  }

  const rosterRows = (d.starters || []).map(s => {
    const isSwap = s.status === "suggested_swap";
    const nameHtml = isSwap
      ? `${escapeHtml(s.name || "empty")} &rarr; ${escapeHtml(s.swap_to || "")}`
      : escapeHtml(s.name || "empty");
    return `<div class="home-roster-row${isSwap ? " swap-out" : ""}">
      <span class="pos">${escapeHtml(s.slot_label)}</span>
      <span class="nm">${nameHtml}</span>
      <span class="val">${s.status === "match" || isSwap ? s.value.toFixed(1) : ""}</span>
    </div>`;
  }).join("");

  const flagRows = [];
  if (d.flags.lineup_swaps) {
    flagRows.push(`<span class="home-flag swap" data-home-flag="lineup">${d.flags.lineup_swaps} lineup swap${d.flags.lineup_swaps === 1 ? "" : "s"} suggested</span>`);
  }
  if (d.flags.waiver_adds) {
    flagRows.push(`<span class="home-flag waiver" data-home-flag="waivers">${d.flags.waiver_adds} waiver add${d.flags.waiver_adds === 1 ? "" : "s"} recommended</span>`);
  }
  if (d.flags.trade_targets) {
    flagRows.push(`<span class="home-flag target" data-home-flag="targets">${d.flags.trade_targets} trade target${d.flags.trade_targets === 1 ? "" : "s"} found</span>`);
  }

  return `<div class="home-card${hasFlags ? " attn" : ""}" data-home-card="${escapeHtml(lg.league_key)}">
    <div class="home-card-head"><div class="home-card-name">${escapeHtml(d.name)}</div></div>
    <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}${record ? ` &middot; ${record}` : ""}</div>
    ${rankHtml}
    ${matchupHtml}
    <div class="home-roster-list">${rosterRows}</div>
    ${hasFlags ? `<div class="home-flags">${flagRows.join("")}</div>` : ""}
  </div>`;
}

function ordinal(n) {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}
