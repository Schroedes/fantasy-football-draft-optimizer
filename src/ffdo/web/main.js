let state = {
  session: null,
  format: null,
  connecting: false,
  readinessTimer: null,
  provider: "sleeper",
};

async function fetchSession() {
  try {
    const res = await fetch("/api/session");
    const session = await res.json();
    if (session) {
      state.session = session;
      state.format = session.draft_type;
      showConnected();
      pollReadiness();
    }
  } catch (err) {
    console.error("fetchSession failed", err);
  }
}

function showConnected() {
  document.getElementById("connect-form").hidden = true;
  document.getElementById("connected-view").hidden = false;
  renderConnected();
}

function renderConnected() {
  const s = state.session;
  if (!s) return;

  document.getElementById("league-name").textContent = s.league_name || "(unnamed league)";
  document.getElementById("league-id-tag").textContent = `league_id ${s.league_id}`;
  document.getElementById("status-badge").textContent = (s.draft_status || "").toUpperCase();
  document.getElementById("stat-teams").textContent = s.num_teams;
  document.getElementById("stat-budget").textContent = s.budget !== null ? `$${s.budget}` : "—";
  document.getElementById("stat-rounds").textContent = s.rounds || "—";
  document.getElementById("stat-format").textContent = s.draft_type === "auction" ? "Auction" : "Snake";

  const positions = s.roster_positions || [];
  const starters = positions.filter(p => p !== "BN");
  const bench = positions.filter(p => p === "BN");
  document.getElementById("roster-chips").innerHTML =
    starters.map(p => `<span class="chip chip-starter">${p}</span>`).join("") +
    bench.map(p => `<span class="chip chip-bench">${p}</span>`).join("");

  const scoringCount = s.scoring_settings ? Object.keys(s.scoring_settings).length : 0;
  document.getElementById("scoring-note").textContent = `${scoringCount} scoring keys synced`;

  renderFormatToggle();
}

function renderFormatToggle() {
  document.querySelectorAll("#format-toggle button").forEach(btn => {
    btn.classList.toggle("on", btn.dataset.format === state.format);
  });
  const notes = {
    auction: "This league's draft is scored as an auction — the board leads with budget, live inflation, and your max bid.",
    snake: "This league's draft is a snake draft — the board leads with Cost of Waiting instead of dollars.",
  };
  document.getElementById("format-note").textContent = notes[state.format] || "";
}

document.querySelectorAll("#format-toggle button").forEach(btn =>
  btn.addEventListener("click", () => {
    state.format = btn.dataset.format;
    renderFormatToggle();
  }));

document.getElementById("enter-btn").addEventListener("click", () => {
  window.location.href = "/board";
});

async function pollReadiness() {
  if (state.readinessTimer) clearTimeout(state.readinessTimer);

  try {
    const res = await fetch("/api/readiness");
    const r = await res.json();
    renderReadiness(r);

    const allSynced = r.league_draft === "synced" && r.players === "synced" && r.projections === "synced";
    if (!allSynced) {
      state.readinessTimer = setTimeout(pollReadiness, 1500);
    }
  } catch (err) {
    console.error("pollReadiness failed", err);
    // A transient network failure must not permanently kill the
    // self-rescheduling poll loop -- keep retrying just like a
    // successful-but-still-pending poll would.
    state.readinessTimer = setTimeout(pollReadiness, 1500);
  }
}

function renderReadiness(r) {
  const rows = [
    { label: "League + draft settings", status: r.league_draft },
    { label: "Players", status: r.players },
    { label: "Projections & ADP", status: r.projections },
  ];
  document.getElementById("readiness-rows").innerHTML = rows.map(row => `
    <div class="readiness-row">
      <span class="dot ${row.status}"></span>
      <span class="readiness-label">${row.label}</span>
      <span class="readiness-status">${row.status === "synced" ? "Synced" : "Syncing…"}</span>
    </div>`).join("");
}

function connectPayload() {
  if (state.provider === "espn") {
    return {
      provider: "espn",
      league_id: document.getElementById("espn-league-id-input").value.trim(),
      season: document.getElementById("espn-season-input").value.trim(),
      espn_s2: document.getElementById("espn-s2-input").value.trim(),
      swid: document.getElementById("espn-swid-input").value.trim(),
    };
  }
  return {
    provider: "sleeper",
    league_id: document.getElementById("league-id-input").value.trim(),
    username: document.getElementById("username-input").value.trim(),
  };
}

function connectPayloadIsComplete(payload) {
  if (payload.provider === "espn") {
    return payload.league_id && payload.season && payload.espn_s2 && payload.swid;
  }
  return payload.league_id && payload.username;
}

async function connect() {
  const payload = connectPayload();
  const errorEl = document.getElementById("connect-error");
  errorEl.hidden = true;

  if (!connectPayloadIsComplete(payload)) {
    errorEl.textContent = payload.provider === "espn"
      ? "League ID, season, espn_s2, and SWID are all required."
      : "League ID and username are both required.";
    errorEl.hidden = false;
    return;
  }
  if (state.connecting) return;

  state.connecting = true;
  const btn = document.getElementById("connect-btn");
  btn.disabled = true;
  btn.textContent = "Connecting…";

  try {
    const res = await fetch("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      errorEl.textContent = body.detail || "Could not connect to that league.";
      errorEl.hidden = false;
      return;
    }
    state.session = body;
    state.format = body.draft_type;
    showConnected();
    pollReadiness();
  } catch (err) {
    errorEl.textContent = "Network error — check your connection and try again.";
    errorEl.hidden = false;
    console.error("connect failed", err);
  } finally {
    state.connecting = false;
    btn.disabled = false;
    btn.textContent = "Connect";
  }
}

document.getElementById("connect-btn").addEventListener("click", connect);

document.querySelectorAll("#provider-toggle button").forEach(btn =>
  btn.addEventListener("click", () => {
    state.provider = btn.dataset.provider;
    document.querySelectorAll("#provider-toggle button").forEach(b =>
      b.classList.toggle("on", b === btn));
    document.getElementById("sleeper-fields").hidden = state.provider !== "sleeper";
    document.getElementById("espn-fields").hidden = state.provider !== "espn";
    document.getElementById("connect-error").hidden = true;
  }));

fetchSession();
