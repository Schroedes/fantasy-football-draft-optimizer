// The board's own DOM. Lifted verbatim out of board/index.html so the shell
// (src/ffdo/web/app.js) can mount the board into #league-body without loading
// a second HTML document. index.html is now just a thin host that imports
// mount() and calls it.
const BOARD_MARKUP = `
<header id="strip">
  <div class="brand">
    <span class="brand-name">FFDO</span>
    <span id="brand-tag" class="brand-tag">/ AUCTION</span>
    <span id="mock-badge" class="mock-badge" hidden>MOCK</span>
  </div>
  <div class="strip-stats">
    <div><span class="label">Inflation</span><b id="inflation">&mdash;</b></div>
    <div><span class="label">Spent</span><b id="spent">&mdash;</b></div>
    <div id="your-dollars-stat"><span class="label">Your $ left</span><b id="your-dollars">&mdash;</b></div>
    <div id="your-slots-stat"><span class="label">Your slots left</span><b id="your-slots">&mdash;</b></div>
    <div id="your-per-slot-stat"><span class="label">$/slot vs avg</span><b id="your-per-slot">&mdash;</b></div>
    <div><span class="label">Picks</span><b id="picks">&mdash;</b></div>
    <div><span class="label">Updated</span><b id="updated">&mdash;</b></div>
  </div>
</header>

<section id="cow" hidden>
  <div class="cow-head">
    <h2>Cost of waiting</h2>
    <span class="cow-sub">what you give up per position if you pass now and take it at your next pick instead</span>
  </div>
  <div id="cow-rows"></div>
</section>

<section id="position-budget" hidden>
  <div class="posbudget-head">
    <h2>Position budget</h2>
    <span class="posbudget-sub">recommended split of your remaining dollars to fill every slot</span>
  </div>
  <div id="posbudget-rows"></div>
</section>

<div id="layout">
  <div id="sidebar">
  <aside id="nominated" class="empty">
    <div class="nom-empty-msg">Click a player row to pin them here while they're on the block.</div>
    <div class="nom-body" hidden>
      <div class="nom-head">
        <span id="nom-kicker" class="nom-kicker">On the block</span>
        <span id="nom-tier" class="tier-chip"></span>
      </div>
      <div class="nom-name-block">
        <span id="nom-name" class="nom-name"></span>
        <span id="nom-meta" class="nom-meta"></span>
      </div>
      <div class="nom-stats">
        <div class="nom-stat">
          <span id="nom-baseline-label" class="label">Baseline</span>
          <b id="nom-baseline">&mdash;</b>
        </div>
        <div class="nom-stat">
          <span id="nom-adjusted-label" class="label">Adjusted</span>
          <b id="nom-adjusted" class="accent">&mdash;</b>
        </div>
        <div class="nom-stat">
          <span class="label">VOR</span>
          <b id="nom-vor">&mdash;</b>
        </div>
        <div class="nom-stat" id="nom-maxbid-stat" hidden>
          <span class="label">Your max</span>
          <b id="nom-maxbid" class="accent">&mdash;</b>
        </div>
        <div class="nom-stat" id="nom-posbudget-stat" hidden>
          <span class="label">Pos budget</span>
          <b id="nom-posbudget">&mdash;</b>
        </div>
      </div>
      <div id="nom-next-best" class="nom-next-best" hidden>
        <span class="label">Next best available</span>
        <div class="nom-next-best-row">
          <span id="nom-next-best-name" class="nom-next-best-name">&mdash;</span>
          <span id="nom-next-best-gap" class="nom-next-best-gap"></span>
        </div>
      </div>
      <button id="lean-badge" class="lean-badge" disabled title="This board never names a pick — you decide.">
        MODEL LEAN &middot; DISABLED
      </button>
      <div id="nom-hr" class="hr"></div>
      <div id="bid-block" class="bid-block">
        <div class="bid-head">
          <span class="label">Your bid</span>
          <span id="nom-surplus" class="surplus"></span>
        </div>
        <div class="bid-controls">
          <button data-step="-5">&minus;5</button>
          <button data-step="-1">&minus;1</button>
          <span id="nom-bid" class="bid-amount">$0</span>
          <button data-step="1">+1</button>
          <button data-step="5">+5</button>
        </div>
      </div>
    </div>
  </aside>

  <aside id="rosters">
    <div class="rosters-head">
      <h2>Roster power rankings</h2>
      <span class="rosters-sub">starting-lineup VOR, live as rosters are built</span>
    </div>
    <div id="rosters-rows"></div>
  </aside>

  <aside id="history">
    <div class="history-head">
      <h2>Pick history</h2>
      <span class="history-sub">grades how each pick's value compared to what was still on the board</span>
    </div>
    <div id="history-rows"></div>
  </aside>

  <aside id="optimal-plan">
    <div class="plan-head">
      <h2>Optimal plan</h2>
      <span class="plan-sub">the best affordable roster within your remaining budget</span>
    </div>
    <div class="plan-totals">
      <span><b id="plan-vor">&mdash;</b> VOR</span>
      <span><b id="plan-cost">&mdash;</b> spent</span>
      <span><b id="plan-left">&mdash;</b> left</span>
    </div>
    <div id="plan-rows"></div>
  </aside>

  <aside id="snake-plan">
    <div class="snakeplan-head">
      <h2>Draft plan</h2>
      <span class="snakeplan-sub">most likely pick at each of your remaining turns, simulated forward</span>
    </div>
    <div class="snakeplan-totals">
      <span><b id="snakeplan-vor">&mdash;</b> expected starting VOR</span>
    </div>
    <div id="snakeplan-rows"></div>
  </aside>
  </div>

  <main id="board-panel">
    <nav id="filters">
      <button data-pos="ALL" class="on">All</button>
      <button data-pos="QB">QB</button>
      <button data-pos="RB">RB</button>
      <button data-pos="WR">WR</button>
      <button data-pos="TE">TE</button>
      <button data-pos="DEF">DEF</button>
      <button data-pos="K">K</button>
      <label class="hide-drafted"><input type="checkbox" id="hide-drafted" checked> Hide drafted</label>
      <input id="search" type="search" placeholder="Search players&hellip;" autocomplete="off">
    </nav>
    <div id="board-scroll">
      <table id="board">
        <thead><tr>
          <th></th><th>Player</th><th>Pos</th><th>Tm</th><th>Age</th>
          <th data-sort="tier">Tier</th><th data-sort="vor">VOR</th>
          <th id="th-lineup-value" data-sort="lineup_value" hidden title="How much this player would add to your starting lineup right now, given who you've already drafted">Lineup Val</th>
          <th id="th-baseline" data-sort="baseline">Fair $</th>
          <th id="th-money" data-sort="adjusted">Adj $</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </main>
</div>`;

// Set by mount(); every league-scoped fetch and the season-mode switch read
// these. Only one board is mounted at a time (the shell tears #league-body
// down on route change), so module-level singletons are safe.
let _leagueKey = null;
let _container = null;
let _meta = null;

let state = {
  pos: "ALL",
  hideDrafted: true,
  search: "",
  sortKey: "vor",
  sortDir: "desc",
  // True until the user manually clicks a sort header. While true, the
  // snake board's default sort follows lineup_value instead of raw vor --
  // the nudge toward whatever position you're actually thin at -- without
  // permanently overriding a sort the user picked for themselves.
  sortKeyIsDefault: true,
  nominatedId: null,
  bid: 0,
  // The player_id of the last live nomination we auto-applied from Sleeper
  // (distinct from `nominatedId`, which also changes on a manual row click).
  // Lets us tell "a new nomination just happened" from "still the same one".
  liveNominatedId: null,
  // True while `bid` should keep following Sleeper's live high offer on
  // every poll. A manual bid nudge (or inspecting a different player) sets
  // this false; it resumes once the next real nomination comes in.
  bidIsLive: true,
  expandedRoster: null,
  data: null,
  // setInterval handles, owned by mount(); cleared when the draft completes
  // and the screen switches to season mode.
  pollId: null,
  livePollId: null,
};

async function refresh() {
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(_leagueKey)}/board`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    // The draft finished: stop polling the board and hand the screen to the
    // season-mode placeholder. Only the heavy refresh() poll carries
    // draft_status (see Ruling 1) -- refreshLive() never does -- so this is
    // the one place the switch can happen.
    if (state.data.draft_status === "complete") {
      clearInterval(state.pollId); clearInterval(state.livePollId);
      renderSeasonMode(_container, _meta);
      return;
    }
    applyLiveNomination();
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    render();
  } catch (err) {
    document.getElementById("updated").textContent = "error";
    console.error("board refresh failed", err);
  }
}

// `/api/board` recomputes VOR/baseline/rosters for the whole player pool on
// every call, which is too slow to poll at auction speed -- see `refresh`
// above, kept on its own slower interval for that data. Nomination and bid
// don't need any of that: they come straight off Sleeper's draft metadata,
// so `/api/board/live` fetches just that and this poll can run fast without
// paying for the heavy rebuild. Skipped until the first full `refresh()`
// lands, since it only patches fields onto `state.data` rather than
// populating it.
async function refreshLive() {
  if (!state.data) return;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(_leagueKey)}/board/live`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const live = await res.json();
    state.data.live_nomination = live.live_nomination;
    state.data.picks_made = live.picks_made;
    applyLiveNomination();
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    document.getElementById("picks").textContent = state.data.picks_made;
    renderNominated();
  } catch (err) {
    console.error("live refresh failed", err);
  }
}

function defaultBidGuess(p) {
  return p.baseline !== undefined ? Math.max(1, Math.round(p.baseline * 0.9)) : 0;
}

// Follows whoever Sleeper says is actually on the block right now, so the
// sidebar tracks the live auction without needing a click. A manual row
// click or bid nudge holds its own state in between polls -- see `nominate`
// and the bid-controls handler -- but the moment an actual new nomination
// comes in from Sleeper, it takes back over.
function applyLiveNomination() {
  const live = state.data && state.data.live_nomination;
  if (!live) return;
  if (live.player_id !== state.liveNominatedId) {
    state.liveNominatedId = live.player_id;
    state.nominatedId = live.player_id;
    state.bidIsLive = true;
  }
  if (state.bidIsLive) {
    const p = state.data.players.find(x => x.player_id === live.player_id);
    state.bid = live.bid ?? (p ? defaultBidGuess(p) : 0);
  }
}

// A human scanning a live draft board never needs to see all 3,000+ rows at
// once -- sort/filter already narrows what matters. Capping the rendered
// rows keeps the 3-second poll and every keystroke/filter/sort click cheap
// regardless of pool size.
const MAX_RENDERED_ROWS = 300;

function visibleRows() {
  const d = state.data;
  if (!d) return [];
  const q = state.search.trim().toLowerCase();
  let rows = d.players.filter(p =>
    (state.pos === "ALL" || p.position === state.pos) &&
    !(state.hideDrafted && p.drafted) &&
    (!q || p.name.toLowerCase().includes(q)));

  const key = state.sortKey;
  const dir = state.sortDir === "desc" ? -1 : 1;
  rows = rows.slice().sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av === bv) return 0;
    return av < bv ? -1 * dir : 1 * dir;
  });
  return rows.slice(0, MAX_RENDERED_ROWS);
}

function render() {
  const d = state.data;
  if (!d) return;

  if (state.sortKeyIsDefault) {
    state.sortKey = d.format === "snake" ? "lineup_value" : "vor";
  }

  document.getElementById("inflation").textContent =
    d.inflation !== undefined ? `${d.inflation.toFixed(2)}x` : "—";
  document.getElementById("spent").textContent =
    d.budget ? `$${d.budget.spent}/${d.budget.total}` : "—";
  document.getElementById("picks").textContent = d.picks_made;
  document.getElementById("brand-tag").textContent = `/ ${d.format === "snake" ? "SNAKE" : "AUCTION"}`;
  document.getElementById("mock-badge").hidden = !d.is_mock;

  const hasYourBudget = d.format !== "snake" && d.budget &&
    d.budget.your_slots_left !== undefined;
  ["your-dollars-stat", "your-slots-stat", "your-per-slot-stat"].forEach(id => {
    document.getElementById(id).hidden = !hasYourBudget;
  });
  if (hasYourBudget) {
    document.getElementById("your-dollars").textContent = `$${d.budget.your_dollars_left}`;
    document.getElementById("your-slots").textContent = d.budget.your_slots_left;
    const yours = d.budget.your_dollars_per_slot;
    const avg = d.budget.league_dollars_per_slot;
    const diff = Math.round((yours - avg) * 10) / 10;
    const sign = diff >= 0 ? "+" : "−";
    document.getElementById("your-per-slot").textContent = `$${yours} (${sign}$${Math.abs(diff)})`;
  }

  renderCow();
  renderSnakePlan();
  renderPositionBudget();
  renderOptimalPlan();
  renderMoneyHeader();
  renderTable();
  renderNominated();
  renderRosters();
  renderHistory();
  renderSortHeaders();
}

function renderMoneyHeader() {
  const th = document.getElementById("th-money");
  const thBaseline = document.getElementById("th-baseline");
  const d = state.data;
  if (d.format === "snake") {
    th.textContent = "Survives";
    th.dataset.sort = "survival";
    th.colSpan = 2;
    thBaseline.hidden = true;
  } else {
    th.textContent = "Adj $";
    th.dataset.sort = "adjusted";
    th.colSpan = 1;
    thBaseline.hidden = false;
  }
  document.getElementById("th-lineup-value").hidden = d.format !== "snake";
}

function renderCow() {
  const cowEl = document.getElementById("cow");
  const d = state.data;
  if (d.format !== "snake" || !d.cost_of_waiting) {
    cowEl.hidden = true;
    return;
  }
  cowEl.hidden = false;

  const entries = Object.entries(d.cost_of_waiting).sort((a, b) => b[1].cost - a[1].cost);
  const maxCost = Math.max(1, ...entries.map(([, c]) => c.cost));

  document.getElementById("cow-rows").innerHTML = entries.map(([pos, c]) => {
    const hot = c.cost >= maxCost * 0.6;
    const deep = c.cost <= maxCost * 0.15;
    const color = hot ? "var(--red)" : deep ? "var(--green)" : "var(--amber)";
    const tag = hot ? "CLIFF" : deep ? "DEEP" : "";
    const posColor = `var(--${pos.toLowerCase()}, var(--muted))`;
    return `<div class="cow-row">
      <span class="cow-pos" style="color:${posColor}">${pos}</span>
      <div class="cow-stat">
        <span class="label">Best now</span>
        <b>${c.best_now}</b>
      </div>
      <div class="cow-stat next">
        <span class="label">Best next pick</span>
        <b>${c.expected_next}</b>
      </div>
      <div class="cow-cost">
        <div class="cow-cost-line">
          <span class="cow-cost-num" style="color:${color}">${c.cost}</span>
          <span class="cow-cost-label">pts cost of waiting</span>
        </div>
        <div class="cow-bar-track">
          <div class="cow-bar-fill" style="width:${Math.min(100, (c.cost / maxCost) * 100)}%;background:${color}"></div>
        </div>
      </div>
      <span class="cow-tag" style="color:${tag ? color : "transparent"}">${tag}</span>
    </div>`;
  }).join("");
}

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

function renderOptimalPlan() {
  const el = document.getElementById("optimal-plan");
  const d = state.data;
  const plan = d && d.optimal_plan;
  if (d.format === "snake" || !plan) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  document.getElementById("plan-vor").textContent = plan.total_plan_vor;
  document.getElementById("plan-cost").textContent = `$${plan.total_plan_cost}`;
  document.getElementById("plan-left").textContent = `$${plan.dollars_left_after_plan}`;

  document.getElementById("plan-rows").innerHTML = plan.slots.map(slot => {
    const posColor = `var(--${slot.eligible_position.toLowerCase()}, var(--muted))`;
    return `<div class="plan-row">
      <span class="plan-category">${slot.category}</span>
      <span class="plan-name" style="color:${posColor}">${escapeHtml(slot.name)}</span>
      <span class="plan-price">$${slot.target_price}</span>
      <span class="plan-vor-val">${slot.vor} VOR</span>
    </div>`;
  }).join("");
}

function renderSnakePlan() {
  const el = document.getElementById("snake-plan");
  const d = state.data;
  const plan = d && d.snake_plan;
  if (d.format !== "snake" || !plan) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  document.getElementById("snakeplan-vor").textContent = plan.expected_starting_vor;

  document.getElementById("snakeplan-rows").innerHTML = plan.picks.map(p => {
    const posColor = `var(--${p.most_likely_position.toLowerCase()}, var(--muted))`;
    return `<div class="snakeplan-row">
      <span class="snakeplan-pickno">#${p.picks_from_now}</span>
      <span class="snakeplan-pos" style="color:${posColor}">${p.most_likely_position}</span>
      <span class="snakeplan-name">${escapeHtml(p.most_likely_player_name)}</span>
      <span class="snakeplan-rate">${Math.round(p.player_hit_rate * 100)}%</span>
    </div>`;
  }).join("");
}

function renderTable() {
  const rows = visibleRows();
  const tbody = document.querySelector("#board tbody");
  const isSnake = state.data.format === "snake";

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="${isSnake ? 10 : 9}" class="empty-msg">No players match the current filters.</td></tr>`;
    return;
  }

  let lastTier = null;
  tbody.innerHTML = rows.map(p => {
    const brk = state.sortKey === "tier" && p.tier !== lastTier && lastTier !== null ? " tier-break" : "";
    lastTier = p.tier;
    const classes = [
      `pos-${p.position}`,
      p.drafted ? "drafted" : "",
      p.player_id === state.nominatedId ? "nominated-row" : "",
      brk,
    ].filter(Boolean).join(" ");
    const money = p.baseline !== undefined
      ? `<td>$${p.baseline}</td><td class="adj-cell">$${p.adjusted}</td>`
      : `<td colspan="2">${survivalCell(p.survival)}</td>`;
    const lineupValue = isSnake ? `<td class="lineup-value-cell">${p.lineup_value}</td>` : "";
    return `<tr class="${classes}" data-id="${p.player_id}">
      <td></td>
      <td class="name-cell">${escapeHtml(p.name)}</td>
      <td class="pos-cell">${p.position}</td>
      <td>${p.team ?? ""}</td>
      <td>${p.age ?? ""}</td>
      <td>${p.tier}</td>
      <td>${p.vor}</td>
      ${lineupValue}
      ${money}
    </tr>`;
  }).join("");
}

function nominate(p) {
  state.nominatedId = p.player_id;
  state.bid = defaultBidGuess(p);
  // Clicking the player who's actually live keeps following Sleeper's bid;
  // clicking anyone else is inspection, so stop tracking until the next
  // real nomination comes in.
  state.bidIsLive = p.player_id === state.liveNominatedId;
  render();
}

function renderNominated() {
  const el = document.getElementById("nominated");
  const body = el.querySelector(".nom-body");
  const d = state.data;
  const p = d && d.players.find(x => x.player_id === state.nominatedId);

  if (!p) {
    el.classList.remove("pinned");
    body.hidden = true;
    return;
  }

  el.classList.add("pinned");
  body.hidden = false;
  const isTrackingLive = p.player_id === state.liveNominatedId && state.bidIsLive;
  document.getElementById("nom-kicker").textContent =
    isTrackingLive ? "On the block · LIVE" : "On the block";
  document.getElementById("nom-tier").textContent = `TIER ${p.tier}`;
  document.getElementById("nom-name").textContent = p.name;
  document.getElementById("nom-meta").textContent = `${p.position} · ${p.team ?? "FA"} · age ${p.age ?? "?"}`;
  document.getElementById("nom-vor").textContent = p.vor;

  const bidBlock = document.getElementById("bid-block");
  const hr = document.getElementById("nom-hr");
  const maxBidStat = document.getElementById("nom-maxbid-stat");
  const posBudgetStat = document.getElementById("nom-posbudget-stat");
  const nextBestEl = document.getElementById("nom-next-best");
  const isAuction = p.baseline !== undefined;

  document.getElementById("nom-baseline-label").textContent = isAuction ? "Baseline" : "Survival";
  document.getElementById("nom-adjusted-label").textContent = isAuction ? "Adjusted" : "Pos.";
  bidBlock.hidden = !isAuction;
  hr.hidden = !isAuction;
  maxBidStat.hidden = !isAuction || p.max_bid === undefined;

  if (isAuction) {
    document.getElementById("nom-baseline").textContent = `$${p.baseline}`;
    document.getElementById("nom-adjusted").textContent = `$${p.adjusted}`;
    document.getElementById("nom-bid").textContent = `$${state.bid}`;
    if (p.max_bid !== undefined) {
      document.getElementById("nom-maxbid").textContent = `$${p.max_bid}`;
    }

    const surplus = Math.round((p.adjusted - state.bid) * 10) / 10;
    const surplusEl = document.getElementById("nom-surplus");
    const positive = surplus >= 0;
    surplusEl.textContent = (positive ? "+$" : "−$") + Math.abs(surplus) + (positive ? " bargain" : " over value");
    surplusEl.className = "surplus " + (positive ? "bargain" : "over");

    const posBudget = d.budget && d.budget.by_position && d.budget.by_position[p.position];
    if (posBudget) {
      posBudgetStat.hidden = false;
      const ratio = posBudget.recommended > 0
        ? `${Math.round((state.bid / posBudget.recommended) * 100)}%` : "—";
      document.getElementById("nom-posbudget").textContent =
        `$${posBudget.recommended} / ${posBudget.slots_open} slot${posBudget.slots_open === 1 ? "" : "s"} · ${ratio}`;
    } else {
      posBudgetStat.hidden = true;
    }

    const nextBest = d.players
      .filter(x => x.position === p.position && !x.drafted && x.player_id !== p.player_id)
      .sort((a, b) => b.vor - a.vor)[0];
    if (nextBest) {
      nextBestEl.hidden = false;
      document.getElementById("nom-next-best-name").textContent = nextBest.name;
      const vorGap = Math.round((p.vor - nextBest.vor) * 10) / 10;
      const dollarGap = Math.round((p.adjusted - nextBest.adjusted) * 10) / 10;
      const vorWord = vorGap >= 0 ? "lower" : "higher";
      const dollarWord = dollarGap >= 0 ? "cheaper" : "pricier";
      document.getElementById("nom-next-best-gap").textContent =
        `${Math.abs(vorGap)} VOR ${vorWord} · $${Math.abs(dollarGap)} ${dollarWord}`;
    } else {
      nextBestEl.hidden = true;
    }
  } else {
    document.getElementById("nom-baseline").textContent = `${Math.round((p.survival ?? 0) * 100)}%`;
    document.getElementById("nom-adjusted").textContent = p.position;
    posBudgetStat.hidden = true;
    nextBestEl.hidden = true;
  }
}

function renderRosters() {
  const d = state.data;
  const el = document.getElementById("rosters-rows");
  if (!d || !d.rosters) { el.innerHTML = ""; return; }

  el.innerHTML = d.rosters.map((r, i) => {
    const posCells = ["QB", "RB", "WR", "TE", "DEF", "K"].map(pos => {
      const v = r.by_position[pos];
      const posColor = `var(--${pos.toLowerCase()}, var(--muted))`;
      return `<span class="roster-pos" style="color:${posColor}">${v !== undefined ? Math.round(v) : "—"}</span>`;
    }).join("");

    const expanded = state.expandedRoster === r.roster_id;
    const detail = !expanded ? "" : `<div class="roster-detail">${
      r.players.length === 0
        ? '<div class="roster-detail-player bench">No picks yet.</div>'
        : r.players.map(p => `<div class="roster-detail-player${p.starter ? "" : " bench"}">
            <span>${escapeHtml(p.name)} · ${p.position}</span>
            <span>${p.vor}</span>
          </div>`).join("")
    }</div>`;

    const bench = Math.round(r.bench_vor);
    const benchLabel = (bench >= 0 ? "+" : "") + bench;

    return `<div>
      <div class="roster-row${r.is_you ? " you" : ""}" data-roster-id="${r.roster_id}">
        <div class="roster-line1">
          <span class="roster-rank">${i + 1}</span>
          <span class="roster-name">${escapeHtml(r.team_name)}</span>
          <span class="roster-total">${Math.round(r.starting_vor)}</span>
        </div>
        <div class="roster-line2">
          <div class="roster-positions">${posCells}</div>
          <span class="roster-bench">${benchLabel}</span>
        </div>
      </div>
      ${detail}
    </div>`;
  }).join("");
}

function renderHistory() {
  const d = state.data;
  const el = document.getElementById("history-rows");
  if (!d || !d.history) { el.innerHTML = ""; return; }

  if (d.history.length === 0) {
    el.innerHTML = `<div class="history-empty">No picks yet.</div>`;
    return;
  }

  const prevScrollTop = el.scrollTop;
  el.innerHTML = d.history.map(h => {
    const posColor = `var(--${(h.position ?? "muted").toLowerCase()}, var(--muted))`;
    const amount = h.amount !== null && h.amount !== undefined
      ? `<span class="history-amount">$${h.amount}</span>` : "";
    const badge = h.grade
      ? `<span class="history-badge ${h.grade.toLowerCase()}">${h.grade}</span>` : "";
    return `<div class="history-row">
      <span class="history-pickno">R${h.round} P${h.pick_no}</span>
      <div class="history-main">
        <span class="history-name">${escapeHtml(h.name)}</span>
        <span class="history-meta" style="color:${posColor}">${escapeHtml(h.position ?? "")} &middot; ${escapeHtml(h.team_name)}</span>
      </div>
      ${amount}
      ${badge}
    </div>`;
  }).join("");
  el.scrollTop = prevScrollTop;
}

function renderSortHeaders() {
  document.querySelectorAll("#board th[data-sort]").forEach(th => {
    th.classList.toggle("sorted", th.dataset.sort === state.sortKey);
  });
}

function survivalCell(survival) {
  const pct = Math.round((survival ?? 0) * 100);
  const color = pct >= 60 ? "var(--green)" : pct <= 30 ? "var(--red)" : "var(--amber)";
  return `<div class="survival-cell">
    <div class="survival-bar-track"><div class="survival-bar-fill" style="width:${pct}%;background:${color}"></div></div>
    <span style="color:${color}">${pct}%</span>
  </div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Swaps the whole board out for the post-draft placeholder once the draft
// completes. `meta` is the tracked-league record from
// GET /api/leagues/{key}; its provider-set `name` is escaped with the same
// helper the rest of this module uses, the format <option> values are
// literals and safe unescaped.
function renderSeasonMode(container, meta) {
  container.innerHTML = `
    <section class="card season-mode">
      <span class="badge">DRAFT COMPLETE</span>
      <h1>Season mode</h1>
      <p>${escapeHtml(meta.name)} has drafted. Roster analysis, standings, weekly lineups
         and waivers arrive in upcoming releases — each reads this league's
         own scoring and format.</p>
      <div class="format-override">
        <label>Format
          <select id="fmt-override">
            ${["redraft", "keeper", "dynasty"].map(f =>
              `<option value="${f}"${meta.resolved_format === f ? " selected" : ""}>${f}</option>`).join("")}
          </select>
        </label>
      </div>
    </section>`;
  container.querySelector("#fmt-override").onchange = (e) => {
    fetch(`/api/leagues/${encodeURIComponent(meta.league_key)}`, {
      method: "PATCH", headers: { "content-type": "application/json" },
      body: JSON.stringify({ format_override: e.target.value }),
    });
  };
}

// All the board's DOM wiring. Split out of module top-level so it runs
// against the markup mount() just injected, not at import time. The
// document.getElementById / querySelector calls stay document-scoped: the
// injected markup lives in the document, its IDs are unique, and only one
// board mounts at a time.
function wireBoard() {
  // Event delegation: ONE listener on the stable tbody ancestor, rather than
  // one per row re-attached on every render() call (every 3s poll, every
  // keystroke, every filter/sort click). The row's data-id is read off
  // whichever <tr> was actually clicked via e.target.closest.
  document.querySelector("#board tbody").addEventListener("click", e => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr || !state.data) return;
    const p = state.data.players.find(x => x.player_id === tr.dataset.id);
    if (!p || p.drafted) return;
    nominate(p);
  });

  document.querySelectorAll("#filters button[data-pos]").forEach(b =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#filters button[data-pos]").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      state.pos = b.dataset.pos;
      render();
    }));

  document.getElementById("hide-drafted").addEventListener("change", e => {
    state.hideDrafted = e.target.checked;
    render();
  });

  document.getElementById("search").addEventListener("input", e => {
    state.search = e.target.value;
    render();
  });

  document.querySelectorAll("#board th[data-sort]").forEach(th =>
    th.addEventListener("click", () => {
      state.sortKeyIsDefault = false;
      const key = th.dataset.sort;
      if (state.sortKey === key) {
        state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
      } else {
        state.sortKey = key;
        state.sortDir = "desc";
      }
      render();
    }));

  document.querySelectorAll(".bid-controls button[data-step]").forEach(b =>
    b.addEventListener("click", () => {
      const delta = Number(b.dataset.step);
      state.bid = Math.max(1, state.bid + delta);
      state.bidIsLive = false;
      renderNominated();
    }));

  document.getElementById("rosters-rows").addEventListener("click", e => {
    const row = e.target.closest(".roster-row");
    if (!row) return;
    const rid = Number(row.dataset.rosterId);
    state.expandedRoster = state.expandedRoster === rid ? null : rid;
    renderRosters();
  });

  // Named (not an inline arrow) so re-mounting -- e.g. switching leagues in
  // the shell, which calls mount() again -- re-registers the *same* handler
  // reference and the DOM dedupes it, rather than stacking a new closure per
  // mount on the document.
  document.addEventListener("visibilitychange", onVisibilityChange);
}

// Chrome/Edge/Firefox all clamp setInterval hard in a background tab (often to
// a small fraction of the real rate, sometimes far less) to save power -- this
// board is typically backgrounded behind Sleeper's own draft room, so the two
// intervals silently crawl while it's hidden. There's no way to lift that
// throttling from a normal tab, but the moment it's looked at is exactly when
// a bid decision is being made, so catch up immediately on regaining focus
// rather than waiting for the next (possibly minutes-away) throttled tick.
function onVisibilityChange() {
  if (document.visibilityState === "visible") {
    refresh();
    refreshLive();
  }
}

// Entry point. The shell (src/ffdo/web/app.js) calls this after routing to a
// league; board/index.html calls it too, as a thin standalone host.
export function mount(container, leagueKey, meta) {
  if (!document.querySelector('link[href$="board/board.css"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/board/board.css";
    document.head.appendChild(link);
  }
  // Defensive: if a previous board is still mounted (e.g. the shell swapped
  // leagues without a full reload), stop its pollers before starting ours so
  // they don't run in parallel against the module-level `state`.
  clearInterval(state.pollId);
  clearInterval(state.livePollId);
  _leagueKey = leagueKey;
  _container = container;
  _meta = meta;
  container.innerHTML = BOARD_MARKUP;
  wireBoard();
  refresh();
  state.pollId = setInterval(refresh, 3000);
  state.livePollId = setInterval(refreshLive, 1000);
}
