const view = document.getElementById("view");
const bar = document.getElementById("switcher-bar");
const switcher = document.getElementById("league-switcher");

const LAST_LEAGUE_KEY = "ffdo:lastLeagueKey";

// Provider strings (league names, formats, statuses) are set by a league
// commissioner -- a different person than the user -- so they're untrusted
// input and must be escaped before reaching innerHTML. Same helper board.js
// uses (see src/ffdo/web/board/board.js).
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function loadLeagues() {
  try { return await getJSON("/api/leagues"); }
  catch { return []; }
}

function renderSwitcher(leagues, activeKey) {
  if (!leagues.length) { bar.hidden = true; return; }
  bar.hidden = false;
  switcher.innerHTML = leagues.map(l =>
    `<option value="${escapeHtml(l.league_key)}"${l.league_key === activeKey ? " selected" : ""}>` +
    `${escapeHtml(l.name)} — ${escapeHtml(l.resolved_format)}</option>`).join("");
  switcher.onchange = () => { location.hash = `#/league/${switcher.value}`; };
}

async function route() {
  const hash = location.hash || "#/";
  const leagues = await loadLeagues();

  if (hash === "#/connect") { renderSwitcher(leagues, null); return renderConnect(leagues); }

  const m = hash.match(/^#\/league\/(.+)$/);
  if (m) {
    const key = decodeURIComponent(m[1]);
    try { localStorage.setItem(LAST_LEAGUE_KEY, key); } catch {}
    renderSwitcher(leagues, key);
    return renderLeague(key);
  }

  // "#/" — go to last-viewed or first league, else connect
  if (!leagues.length) { location.hash = "#/connect"; return; }
  let last = null;
  try { last = localStorage.getItem(LAST_LEAGUE_KEY); } catch {}
  const target = leagues.find(l => l.league_key === last) || leagues[0];
  location.hash = `#/league/${target.league_key}`;
}

// --- connect / discovery -------------------------------------------------

function renderConnect(tracked) {
  view.innerHTML = `
    <section class="card">
      <h1>Connect a provider</h1>
      <div class="provider-toggle">
        <button data-provider="sleeper" class="on">Sleeper</button>
        <button data-provider="espn">ESPN</button>
      </div>
      <form id="connect-form">
        <div data-fields="sleeper">
          <label>Sleeper username <input name="username" autocomplete="off"></label>
        </div>
        <div data-fields="espn" hidden>
          <label>espn_s2 <input name="espn_s2" type="password" autocomplete="off"></label>
          <label>SWID <input name="swid" type="password" autocomplete="off"></label>
        </div>
        <label>Season <input name="season" value="${new Date().getFullYear()}"></label>
        <p class="error" id="connect-error" hidden></p>
        <button type="submit">Find my leagues</button>
      </form>
      <div id="discovered"></div>
    </section>`;

  let provider = "sleeper";
  view.querySelectorAll(".provider-toggle button").forEach(b => b.onclick = () => {
    provider = b.dataset.provider;
    view.querySelectorAll(".provider-toggle button").forEach(x => x.classList.toggle("on", x === b));
    view.querySelector('[data-fields="sleeper"]').hidden = provider !== "sleeper";
    view.querySelector('[data-fields="espn"]').hidden = provider !== "espn";
    // Switching providers: drop the previous provider's error and any
    // leagues discovered for it, so nothing stale lingers on screen.
    const staleErr = view.querySelector("#connect-error");
    staleErr.hidden = true;
    staleErr.textContent = "";
    view.querySelector("#discovered").innerHTML = "";
  });

  view.querySelector("#connect-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = { provider, season: Number(fd.get("season")) };
    if (provider === "sleeper") payload.username = fd.get("username");
    else { payload.espn_s2 = fd.get("espn_s2"); payload.swid = fd.get("swid"); }
    const err = view.querySelector("#connect-error");
    err.hidden = true;
    try {
      const { leagues } = await getJSON("/api/providers/connect",
        { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify(payload) });
      renderDiscovered(leagues, payload.season);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  };
}

function renderDiscovered(leagues, season) {
  const box = view.querySelector("#discovered");
  if (!leagues.length) { box.innerHTML = "<p>No leagues found for that season.</p>"; return; }
  box.innerHTML = `
    <h2>Leagues found</h2>
    <ul class="discovered-list">
      ${leagues.map((l, i) => `
        <li>
          <label>
            <input type="checkbox" data-i="${i}" ${l.already_tracked ? "checked disabled" : "checked"}>
            <span class="lg-name">${escapeHtml(l.name)}</span>
            <span class="badge">${escapeHtml(l.format)}</span>
            <span class="badge muted">${escapeHtml(l.draft_status || "—")}</span>
          </label>
        </li>`).join("")}
    </ul>
    <button id="track-selected">Track selected leagues</button>`;
  box.querySelector("#track-selected").onclick = async () => {
    const picks = [...box.querySelectorAll("input[type=checkbox]:checked:not(:disabled)")]
      .map(cb => leagues[Number(cb.dataset.i)])
      .map(l => ({ provider: l.provider, provider_league_id: l.provider_league_id,
                   season: l.season }));
    if (!picks.length) return;
    const { leagues: tracked } = await getJSON("/api/leagues/track",
      { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ leagues: picks }) });
    location.hash = `#/league/${tracked[0].league_key}`;
  };
}

// --- league detail ------------------------------------------------------

async function renderLeague(key) {
  let meta;
  try { meta = await getJSON(`/api/leagues/${encodeURIComponent(key)}`); }
  catch (ex) { view.innerHTML = `<p class="error">${escapeHtml(ex.message)}</p>`; return; }

  view.innerHTML = `<div id="league-body">Loading ${escapeHtml(meta.name)}…</div>`;
  // board.js (loaded by the board view) decides board-vs-season-mode from
  // the live board poll; see Task 13.
  window.__ffdoLeagueKey = key;
  window.__ffdoLeagueMeta = meta;
  // Task 13 replaces board.js with a real module exporting `mount`; until
  // then it's a plain auto-running script that throws on eval against this
  // shell's DOM, so the dynamic import rejects. Swallow it here.
  try {
    const mod = await import("./board/board.js");
    if (mod.mount) mod.mount(document.getElementById("league-body"), key, meta);
  } catch (e) {
    document.getElementById("league-body").textContent =
      "Board view loads in the next release.";
    console.warn("board module not ready", e);
  }
}

window.addEventListener("hashchange", route);
route();
