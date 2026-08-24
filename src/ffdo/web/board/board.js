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
};

async function refresh() {
  try {
    const res = await fetch("/api/board");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    applyLiveNomination();
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    render();
  } catch (err) {
    document.getElementById("updated").textContent = "error";
    console.error("board refresh failed", err);
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
  renderPositionBudget();
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

  const positions = ["QB", "RB", "WR", "TE"];
  const maxAmount = Math.max(1, byPos.flex_bench_reserve,
    ...positions.map(pos => byPos[pos].recommended));

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
      <span>Flex/bench reserve</span>
      <span>$${byPos.flex_bench_reserve} · ${byPos.flex_bench_slots_open} slot${byPos.flex_bench_slots_open === 1 ? "" : "s"} open</span>
    </div>`;
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
    const posCells = ["QB", "RB", "WR", "TE"].map(pos => {
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

refresh();
setInterval(refresh, 1000);
