let state = {
  pos: "ALL",
  hideDrafted: true,
  search: "",
  sortKey: "vor",
  sortDir: "desc",
  nominatedId: null,
  bid: 0,
  data: null,
};

async function refresh() {
  try {
    const res = await fetch("/api/board");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    render();
  } catch (err) {
    document.getElementById("updated").textContent = "error";
    console.error("board refresh failed", err);
  }
}

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
  return rows;
}

function render() {
  const d = state.data;
  if (!d) return;

  document.getElementById("inflation").textContent = `${d.inflation.toFixed(2)}x`;
  document.getElementById("spent").textContent = `$${d.budget.spent}/${d.budget.total}`;
  document.getElementById("picks").textContent = d.picks_made;

  renderTable();
  renderNominated();
  renderSortHeaders();
}

function renderTable() {
  const rows = visibleRows();
  const tbody = document.querySelector("#board tbody");

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty-msg">No players match the current filters.</td></tr>`;
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
    return `<tr class="${classes}" data-id="${p.player_id}">
      <td></td>
      <td class="name-cell">${escapeHtml(p.name)}</td>
      <td class="pos-cell">${p.position}</td>
      <td>${p.team ?? ""}</td>
      <td>${p.age ?? ""}</td>
      <td>${p.tier}</td>
      <td>${p.vor}</td>
      <td>$${p.baseline}</td>
      <td class="adj-cell">$${p.adjusted}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll("tr[data-id]").forEach(tr => {
    tr.addEventListener("click", () => {
      const p = state.data.players.find(x => x.player_id === tr.dataset.id);
      if (!p || p.drafted) return;
      nominate(p);
    });
  });
}

function nominate(p) {
  state.nominatedId = p.player_id;
  state.bid = Math.max(1, Math.round(p.baseline * 0.9));
  render();
}

function renderNominated() {
  const el = document.getElementById("nominated");
  const body = el.querySelector(".nom-body");
  const p = state.data && state.data.players.find(x => x.player_id === state.nominatedId);

  if (!p) {
    el.classList.remove("pinned");
    body.hidden = true;
    return;
  }

  el.classList.add("pinned");
  body.hidden = false;
  document.getElementById("nom-tier").textContent = `TIER ${p.tier}`;
  document.getElementById("nom-name").textContent = p.name;
  document.getElementById("nom-meta").textContent = `${p.position} · ${p.team ?? "FA"} · age ${p.age ?? "?"}`;
  document.getElementById("nom-baseline").textContent = `$${p.baseline}`;
  document.getElementById("nom-adjusted").textContent = `$${p.adjusted}`;
  document.getElementById("nom-vor").textContent = p.vor;
  document.getElementById("nom-bid").textContent = `$${state.bid}`;

  const surplus = Math.round((p.adjusted - state.bid) * 10) / 10;
  const surplusEl = document.getElementById("nom-surplus");
  const positive = surplus >= 0;
  surplusEl.textContent = (positive ? "+$" : "−$") + Math.abs(surplus) + (positive ? " bargain" : " over value");
  surplusEl.className = "surplus " + (positive ? "bargain" : "over");
}

function renderSortHeaders() {
  document.querySelectorAll("#board th[data-sort]").forEach(th => {
    th.classList.toggle("sorted", th.dataset.sort === state.sortKey);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

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
    renderNominated();
  }));

refresh();
setInterval(refresh, 3000);
