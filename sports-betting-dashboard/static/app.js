"use strict";

const state = { status: null, settings: null, group: "", rows: [], events: {}, pendingBet: null };
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ---------- helpers ----------

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

const cur = () => (state.settings ? state.settings.currency : "£");
const money = (v) => (v == null ? "–" : `${v < 0 ? "-" : ""}${cur()}${Math.abs(v).toFixed(2)}`);
const pct = (v, dp = 1) => (v == null ? "–" : `${(v * 100).toFixed(dp)}%`);
const odds = (v) => (v == null || !isFinite(v) ? "–" : v.toFixed(2));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const signCls = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");

function kickoff(iso) {
  const d = new Date(iso);
  const now = new Date();
  const day = d.toDateString() === now.toDateString() ? "Today"
    : d.toDateString() === new Date(now.getTime() + 864e5).toDateString() ? "Tomorrow"
    : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return `${day} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

function ago(ts) {
  if (!ts) return "never";
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

// ---------- tabs ----------

$$("#tabs button").forEach((b) => b.addEventListener("click", () => {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${b.dataset.tab}`));
  if (b.dataset.tab === "tracker") loadBets();
  if (b.dataset.tab === "settings") renderSettings();
}));

// ---------- status ----------

async function loadStatus() {
  state.status = await api("/api/status");
  const s = state.status;
  const badge = $("#mode-badge");
  badge.textContent = s.demo ? "DEMO DATA" : "LIVE";
  badge.className = `badge ${s.demo ? "demo" : "live"}`;
  badge.title = s.demo ? "No ODDS_API_KEY set: showing synthetic markets" : "Using The Odds API";
  const b = s.budget;
  $("#credits").innerHTML = s.demo
    ? "Demo mode: no credits used"
    : `<b>${b.remaining}</b> credits${b.known ? "" : " (est.)"} · ${b.daily_allowance}/day for ${b.days_to_reset}d`;
}

// ---------- value bets ----------

function renderGroupChips() {
  const groups = { "": "All sports", ...state.status.groups };
  $("#group-chips").innerHTML = Object.entries(groups).map(([k, v]) =>
    `<button class="chip ${k === state.group ? "on" : ""}" data-g="${k}">${esc(v)}</button>`).join("");
  $$("#group-chips .chip").forEach((c) => c.addEventListener("click", () => {
    state.group = c.dataset.g;
    renderGroupChips();
    loadValue();
  }));
}

async function loadValue() {
  const params = new URLSearchParams();
  if (state.group) params.set("group", state.group);
  const m = $("#f-market").value;
  if (m) params.set("market", m);
  const e = $("#f-edge").value;
  if (e !== "") params.set("min_edge", Number(e) / 100);
  if ($("#f-all").checked) params.set("show_all", "true");
  if ($("#f-confirmed").checked) params.set("confirmed_only", "true");
  const data = await api(`/api/value?${params}`);
  state.rows = data.rows;
  state.events = data.events;
  renderValue(data);
  renderPropsList();
}

function basisTags(r) {
  const t = [];
  if (r.source === "sharp") t.push(`<span class="tag sharp" title="Fair price from Pinnacle/exchanges with the margin removed">Sharp</span>`);
  else if (r.source === "consensus") t.push(`<span class="tag" title="No sharp book: fair price from the average of all books">Consensus</span>`);
  else t.push(`<span class="tag model" title="No market price: our model only. Treat with caution.">Model only</span>`);
  if (r.model_prob != null && r.source !== "model") t.push(`<span class="tag model" title="Model ${pct(r.model_prob)} vs market ${pct(r.market_prob)}, weight ${pct(r.model_weight, 0)}">Model ${pct(r.model_weight, 0)}</span>`);
  if (r.promo_bonus > 0.0005) t.push(`<span class="tag promo" title="bet365 2-Up early payout adds ${pct(r.promo_bonus)} win probability">2-Up +${pct(r.promo_bonus)}</span>`);
  return t.join("");
}

function valueRow(r) {
  const price = r.manual_price || r.bet365_price || "";
  const est = r.ref_price && !r.confirmed ? `est ${odds(r.ref_price)}` : "";
  const best = r.best_price ? `Best ${odds(r.best_price)} (${esc(r.best_book)})` : "";
  return `<tr data-id="${esc(r.sel_id)}">
    <td>${kickoff(r.commence_time)}</td>
    <td class="event">${esc(r.event_name)}<div class="sub">${esc(r.sport_title)}</div></td>
    <td><b>${esc(r.label)}</b><div class="sub">${esc(r.market_label)}</div></td>
    <td class="num">${odds(r.fair_odds)}<div class="sub">${pct(r.fair_prob)}</div></td>
    <td class="num"><input class="price ${r.confirmed ? "confirmed" : ""}" type="number" step="0.01" min="1.01"
        value="${price}" placeholder="${est.replace("est ", "")}" title="${esc(best)}">
        <div class="sub">${r.bet365_price && !r.manual_price ? "from feed" : r.manual_price ? "you entered" : esc(est)}</div></td>
    <td class="num">${odds(r.min_price)}</td>
    <td class="num ${signCls(r.edge)}">${pct(r.edge)}</td>
    <td class="num">${r.stake ? money(r.stake) : "–"}</td>
    <td>${basisTags(r)}</td>
    <td><button class="btn small" data-log>Log</button></td>
  </tr>`;
}

function renderValue(data) {
  const rows = data.rows;
  const tbody = $("#value-table tbody");
  const priced = rows.filter((r) => r.edge != null && r.edge > 0);
  const stake = priced.reduce((a, r) => a + (r.stake || 0), 0);
  const avg = priced.length ? priced.reduce((a, r) => a + r.edge, 0) / priced.length : null;
  const conf = rows.filter((r) => r.confirmed).length;
  const last = Object.values(state.status.last_refresh || {});
  $("#value-tiles").innerHTML = `
    <div class="tile"><div class="k">${$("#f-all").checked ? "Selections" : "Value bets"}</div><div class="v">${data.total}</div><div class="s">${conf} with a confirmed bet365 price</div></div>
    <div class="tile"><div class="k">Average edge</div><div class="v ${signCls(avg)}">${pct(avg)}</div><div class="s">min edge ${pct(data.min_edge)}</div></div>
    <div class="tile"><div class="k">Suggested stakes</div><div class="v">${money(stake)}</div><div class="s">${pct(state.settings.kelly_fraction, 0)} Kelly, max ${pct(state.settings.max_stake_pct, 1)}/bet</div></div>
    <div class="tile"><div class="k">Odds updated</div><div class="v">${last.length ? ago(Math.max(...last)) : "never"}</div><div class="s">${state.status.demo ? "demo data" : "The Odds API"}</div></div>`;
  const note = $("#value-note");
  if (!Object.keys(state.events).length) {
    note.className = "note warn";
    note.textContent = "No odds loaded yet. Hit “Refresh odds” to scan markets.";
  } else {
    note.className = "note";
    note.innerHTML = "Edges use the bet365 price when known: <b>from the feed</b> or <b>typed in by you</b>. " +
      "Otherwise they use an <i>estimate</i> from other UK/EU books. Always check bet365 shows at least the <b>Need ≥</b> price before betting.";
  }
  tbody.innerHTML = rows.length ? rows.map(valueRow).join("")
    : `<tr><td colspan="10" class="empty">No selections meet your filters. Try price-check mode or a lower min edge.</td></tr>`;
}

$("#value-table tbody").addEventListener("change", async (ev) => {
  if (!ev.target.classList.contains("price")) return;
  const tr = ev.target.closest("tr");
  const v = ev.target.value.trim();
  const price = v === "" ? null : Number(v);
  if (price !== null && !(price > 1)) return toast("Enter decimal odds above 1.00");
  try {
    const res = await api("/api/manual_price", { method: "POST", body: { sel_id: tr.dataset.id, price } });
    if (res.selection) {
      const i = state.rows.findIndex((r) => r.sel_id === tr.dataset.id);
      if (i >= 0) state.rows[i] = res.selection;
      tr.outerHTML = valueRow(res.selection);
      const e = res.selection.edge;
      toast(price ? `Edge at ${odds(price)}: ${pct(e)}${e > 0 ? `, stake ${money(res.selection.stake)}` : ", no bet"}` : "Cleared your price");
    }
  } catch (e) { toast(e.message); }
});

$("#value-table tbody").addEventListener("click", (ev) => {
  if (!ev.target.matches("[data-log]")) return;
  const id = ev.target.closest("tr").dataset.id;
  const r = state.rows.find((x) => x.sel_id === id);
  openBetDialog({
    kind: "single", description: `${r.label} · ${r.market_label} · ${r.event_name}`, sport_group: r.group,
    commence_time: r.commence_time, odds: r.ref_price, stake: r.stake, fair_prob: r.eff_prob,
    legs: [{ sel_id: r.sel_id, label: r.label, event: r.event_name, odds: r.ref_price, fair_prob: r.fair_prob }],
  }, r.ref_price && !r.confirmed ? "That price is an estimate. Enter the odds you actually took at bet365." : "");
});

["#f-market", "#f-edge", "#f-all", "#f-confirmed"].forEach((s) => $(s).addEventListener("change", loadValue));

function renderPropsList() {
  const evs = Object.values(state.events).filter((e) => e.prop_cost != null && new Date(e.commence_time) > new Date())
    .filter((e) => !state.group || e.group === state.group)
    .sort((a, b) => a.commence_time.localeCompare(b.commence_time));
  $("#props-list").innerHTML = evs.length ? evs.map((e) => `
    <div class="props-row"><span>${esc(e.name)} <span class="muted">· ${kickoff(e.commence_time)}</span></span>
      <button class="btn small" data-props="${e.id}">${e.has_props ? "Reload" : "Load"} (${e.prop_cost} credits)</button></div>`).join("")
    : `<p class="muted">No events with extra markets loaded.</p>`;
}

$("#props-list").addEventListener("click", async (ev) => {
  const id = ev.target.dataset.props;
  if (!id) return;
  ev.target.disabled = true;
  try {
    let res = await api(`/api/props/${id}`, { method: "POST" });
    if (!res.ok && res.cost && confirm(`${res.reason}\n\nSpend ${res.cost} credits anyway?`)) {
      res = await api(`/api/props/${id}?force=true`, { method: "POST" });
    }
    toast(res.ok ? `Loaded ${res.markets.length} markets (${res.cost} credits)` : res.reason);
    await Promise.all([loadStatus(), loadValue()]);
  } catch (e) { toast(e.message); } finally { ev.target.disabled = false; }
});

// ---------- refresh ----------

let refreshGroups = null;

function renderRefreshChips() {
  refreshGroups = refreshGroups || new Set(Object.keys(state.status.groups));
  $("#r-groups").innerHTML = Object.entries(state.status.groups).map(([k, v]) =>
    `<button type="button" class="chip ${refreshGroups.has(k) ? "on" : ""}" data-g="${k}">${esc(v)}</button>`).join("");
  $$("#r-groups .chip").forEach((c) => c.addEventListener("click", () => {
    refreshGroups.has(c.dataset.g) ? refreshGroups.delete(c.dataset.g) : refreshGroups.add(c.dataset.g);
    renderRefreshChips();
    $("#r-plan").innerHTML = "";
  }));
}

$("#btn-refresh").addEventListener("click", () => {
  renderRefreshChips();
  $("#r-plan").innerHTML = "";
  $("#refresh-dialog").showModal();
  estimate();
});
$("#r-cancel").addEventListener("click", () => $("#refresh-dialog").close());
$("#r-estimate").addEventListener("click", estimate);

async function estimate() {
  $("#r-plan").innerHTML = `<p class="muted">Checking schedules (free)…</p>`;
  try {
    const p = await api("/api/refresh", { method: "POST", body: { groups: [...refreshGroups], dry_run: true } });
    const rows = p.plan.map((x) => `<tr><td>${esc(x.title)}</td><td>${x.events == null ? "?" : x.events}</td>
      <td>${esc(x.markets.join(", "))}</td><td>${x.events === 0 ? "skip" : x.cost}</td></tr>`).join("");
    $("#r-plan").innerHTML = `<table><tr><th>Competition</th><th>Games</th><th>Markets</th><th>Credits</th></tr>${rows}</table>
      <p><b>Total: ${p.cost} credits.</b> ${p.affordable ? "" : `<span class="neg">${esc(p.reason)}</span>`}</p>`;
    $("#r-go").textContent = p.cost ? `Refresh (${p.cost} credits)` : "Refresh";
  } catch (e) { $("#r-plan").textContent = e.message; }
}

$("#r-go").addEventListener("click", async () => {
  $("#r-go").disabled = true;
  try {
    let res = await api("/api/refresh", { method: "POST", body: { groups: [...refreshGroups] } });
    if (!res.ok && res.reason && confirm(`${res.reason}\n\nRefresh anyway?`)) {
      res = await api("/api/refresh", { method: "POST", body: { groups: [...refreshGroups], force: true } });
    }
    if (res.fetched) {
      const n = res.fetched.reduce((a, f) => a + f.events, 0);
      toast(`Loaded ${n} events (${res.cost} credits)${res.errors.length ? `, ${res.errors.length} errors` : ""}`);
      if (res.errors.length) console.warn(res.errors);
    }
    $("#refresh-dialog").close();
    await loadStatus();
    await loadValue();
    // Models fit in the background after a refresh; re-price once they're ready.
    setTimeout(loadValue, 8000);
  } catch (e) { toast(e.message); } finally { $("#r-go").disabled = false; }
});

// ---------- bet dialog ----------

function openBetDialog(bet, hint = "") {
  state.pendingBet = bet;
  $("#b-desc").textContent = bet.description;
  $("#b-odds").value = bet.odds ? Number(bet.odds).toFixed(2) : "";
  $("#b-stake").value = bet.stake || "";
  $("#b-hint").textContent = hint;
  $("#bet-dialog").showModal();
}
$("#b-cancel").addEventListener("click", () => $("#bet-dialog").close());
$("#b-save").addEventListener("click", async () => {
  const b = { ...state.pendingBet, odds: Number($("#b-odds").value), stake: Number($("#b-stake").value) };
  if (b.kind === "single" && b.legs.length) b.legs[0].odds = b.odds;
  try {
    await api("/api/bets", { method: "POST", body: b });
    $("#bet-dialog").close();
    toast("Bet logged");
  } catch (e) { toast(e.message); }
});

// ---------- acca builder ----------

function renderAccaGroups() {
  const on = new Set($$("#a-groups .chip.on").map((c) => c.dataset.v));
  const first = !$("#a-groups").children.length;
  $("#a-groups").innerHTML = Object.entries(state.status.groups).map(([k, v]) =>
    `<button type="button" class="chip ${first || on.has(k) ? "on" : ""}" data-v="${k}">${esc(v)}</button>`).join("");
}
document.addEventListener("click", (ev) => {
  const chip = ev.target.closest("#a-groups .chip, #a-markets .chip");
  if (chip) chip.classList.toggle("on");
});
$$(".stepper button").forEach((b) => b.addEventListener("click", () => {
  const i = $("#a-legs");
  i.value = Math.min(14, Math.max(2, Number(i.value) + Number(b.dataset.step)));
}));

$("#acca-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const toIso = (v) => (v ? new Date(v).toISOString() : null);
  const body = {
    legs: Number($("#a-legs").value),
    groups: $$("#a-groups .chip.on").map((c) => c.dataset.v),
    markets: $$("#a-markets .chip.on").map((c) => c.dataset.v),
    min_odds: Number($("#a-min").value) || 1.01,
    max_odds: Number($("#a-max").value) || 1000,
    start: toIso($("#a-start").value),
    end: toIso($("#a-end").value),
    use_boost: $("#a-boost").checked,
    use_early_payout: $("#a-2up").checked,
    confirmed_only: $("#a-confirmed").checked,
  };
  if (!body.groups.length) return toast("Pick at least one sport");
  if (!body.markets.length) return toast("Pick at least one market");
  $("#acca-results").innerHTML = `<p class="muted">Searching combinations…</p>`;
  try {
    renderAccas(await api("/api/acca", { method: "POST", body }));
  } catch (e) { $("#acca-results").innerHTML = `<p class="neg">${esc(e.message)}</p>`; }
});

function renderAccas(res) {
  state.accas = res.accas;
  let html = `<p class="muted">Searched ${res.candidates} selections across ${res.events} events.</p>`;
  if (res.message) html += `<p class="note warn">${esc(res.message)}</p>`;
  html += res.accas.map((a, i) => `
    <div class="acca-card ${i === 0 && a.positive ? "best" : ""}">
      <div class="acca-head">
        <span class="big">${i === 0 ? "Best" : `#${i + 1}`}: ${a.legs.length}-fold @ ${odds(a.odds)}</span>
        ${a.boost ? `<span class="tag promo">Acca Boost +${pct(a.boost, 0)} → pays ${odds(a.payout_odds)}</span>` : ""}
        <span>Win chance <b>${pct(a.win_prob)}</b> <span class="muted">(fair ${odds(a.fair_odds)})</span></span>
        <span>EV <b class="${signCls(a.ev)}">${pct(a.ev)}</b></span>
        <span>Stake <b>${a.stake ? money(a.stake) : "–"}</b></span>
        <button class="btn small" data-log-acca="${i}">Log acca</button>
      </div>
      <table class="acca-legs">${a.legs.map((l) => `<tr>
        <td>${kickoff(l.commence_time)}</td>
        <td>${esc(l.event_name)}<div class="sub muted">${esc(l.sport_title)}</div></td>
        <td><b>${esc(l.label)}</b> <span class="muted">${esc(l.market_label)}</span>
          ${l.promo_bonus > 0.0005 ? `<span class="tag promo">2-Up</span>` : ""}</td>
        <td class="num">${odds(l.ref_price)} <span class="sub muted">${esc(l.ref_source)}</span></td>
        <td class="num" title="Fair ${odds(l.fair_odds)}${l.promo_bonus > 0.0005 ? ", lower with 2-Up" : ""}">break-even ${odds(1 / l.eff_prob)}</td>
        <td class="num ${signCls(l.leg_ev)}">${pct(l.leg_ev)}</td></tr>`).join("")}
      </table>
      ${a.legs.some((l) => !l.confirmed) ? `<p class="sub muted">Legs marked "est" use estimated prices. Check each on bet365. A leg is +EV when bet365's price beats its break-even price.</p>` : ""}
    </div>`).join("");
  $("#acca-results").innerHTML = html;
}

$("#acca-results").addEventListener("click", (ev) => {
  const i = ev.target.dataset.logAcca;
  if (i == null) return;
  const a = state.accas[Number(i)];
  openBetDialog({
    kind: "acca", description: `${a.legs.length}-fold: ${a.legs.map((l) => l.label).join(" / ")}`,
    sport_group: null, commence_time: a.legs[0].commence_time, odds: a.payout_odds, stake: a.stake,
    fair_prob: a.win_prob,
    legs: a.legs.map((l) => ({ sel_id: l.sel_id, label: l.label, event: l.event_name, odds: l.ref_price, fair_prob: l.fair_prob })),
  }, "Odds include the Acca Boost. Enter the final boosted price bet365 shows.");
});

// ---------- tracker ----------

async function loadBets() {
  const { bets, summary: s } = await api("/api/bets");
  $("#tracker-tiles").innerHTML = `
    <div class="tile"><div class="k">Bets</div><div class="v">${s.bets}</div><div class="s">${s.open} open · ${money(s.open_stake)} at risk</div></div>
    <div class="tile"><div class="k">Profit</div><div class="v ${signCls(s.profit)}">${money(s.profit)}</div><div class="s">expected ${money(s.expected_profit)}</div></div>
    <div class="tile"><div class="k">ROI</div><div class="v ${signCls(s.roi)}">${pct(s.roi)}</div><div class="s">on ${money(s.staked)} staked</div></div>
    <div class="tile"><div class="k">Avg CLV</div><div class="v ${signCls(s.avg_clv)}">${pct(s.avg_clv)}</div><div class="s">${s.clv_positive}/${s.clv_count} beat the close</div></div>
    <div class="tile"><div class="k">Win rate</div><div class="v">${s.settled ? pct(s.won / s.settled, 0) : "–"}</div><div class="s">${s.won}/${s.settled} settled</div></div>`;
  renderChart(s.series);
  const opts = ["open", "won", "lost", "void", "half_won", "half_lost"];
  $("#bets-table tbody").innerHTML = bets.length ? bets.map((b) => `<tr data-id="${b.id}">
    <td>${new Date(b.created_at * 1000).toLocaleDateString()}</td>
    <td class="event">${esc(b.description)}<div class="sub">${b.kind === "acca" ? "Acca" : "Single"}${b.commence_time ? ` · ${kickoff(b.commence_time)}` : ""}</div></td>
    <td class="num">${odds(b.odds)}</td><td class="num">${money(b.stake)}</td>
    <td class="num ${signCls(b.clv)}">${pct(b.clv)}</td>
    <td><select data-settle>${opts.map((o) => `<option value="${o}" ${o === b.status ? "selected" : ""}>${o.replace("_", " ")}</option>`).join("")}</select></td>
    <td class="num ${signCls(b.profit)}">${b.profit == null ? "–" : money(b.profit)}</td>
    <td><button class="btn small" data-del>✕</button></td></tr>`).join("")
    : `<tr><td colspan="8" class="empty">No bets logged yet. Use “Log” on a value bet or acca.</td></tr>`;
}

$("#bets-table tbody").addEventListener("change", async (ev) => {
  if (!ev.target.matches("[data-settle]")) return;
  const id = ev.target.closest("tr").dataset.id;
  await api(`/api/bets/${id}/settle`, { method: "POST", body: { status: ev.target.value } });
  loadBets();
});
$("#bets-table tbody").addEventListener("click", async (ev) => {
  if (!ev.target.matches("[data-del]")) return;
  if (!confirm("Delete this bet?")) return;
  await api(`/api/bets/${ev.target.closest("tr").dataset.id}`, { method: "DELETE" });
  loadBets();
});

function renderChart(series) {
  const el = $("#pnl-chart");
  if (series.length < 2) {
    el.innerHTML = `<p class="muted">Settle at least two bets to see the chart.</p>`;
    return;
  }
  const W = 800, H = 220, P = 30;
  const ys = [0, ...series.map((p) => p.pnl)];
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const x = (i) => P + (i * (W - 2 * P)) / series.length;
  const y = (v) => H - P - ((v - lo) * (H - 2 * P)) / span;
  const pts = [[x(0), y(0)], ...series.map((p, i) => [x(i + 1), y(p.pnl)])];
  const last = series[series.length - 1].pnl;
  const color = last >= 0 ? "var(--accent)" : "var(--bad)";
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <line x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}" stroke="var(--border)" stroke-dasharray="4 4"/>
    <polyline fill="none" stroke="${color}" stroke-width="2.5" points="${pts.map((p) => p.join(",")).join(" ")}"/>
    <text x="${W - P}" y="${y(last) - 8}" fill="${color}" font-size="13" text-anchor="end">${money(last)}</text>
    <text x="${P}" y="${y(0) - 6}" fill="var(--muted)" font-size="11">0</text></svg>`;
}

// ---------- settings ----------

const SETTINGS_FORM = [
  ["Bankroll & staking", [
    ["bankroll", "Bankroll", "number", 10],
    ["kelly_fraction", "Kelly fraction (0.25 = quarter-Kelly)", "number", 0.05],
    ["max_stake_pct", "Max stake per single (fraction of bankroll)", "number", 0.005],
    ["acca_max_stake_pct", "Max stake per acca (fraction of bankroll)", "number", 0.005],
    ["min_edge", "Minimum edge (0.03 = 3%)", "number", 0.005],
  ]],
  ["Pricing", [
    ["proxy_price", "Estimate bet365 price from", "select", ["median", "best"]],
    ["model_weight_sharp", "Model weight when sharp books exist", "number", 0.05],
    ["model_weight_soft", "Model weight with soft books only", "number", 0.05],
    ["early_payout", "Count bet365 2-Up early payout", "checkbox"],
    ["acca_boost", "Use bet365 Acca Boost", "checkbox"],
    ["acca_boost_min_leg_odds", "Acca Boost min odds per leg", "number", 0.05],
    ["acca_boost_table", "Acca Boost table (legs → bonus)", "json"],
  ]],
  ["Odds API & credits", [
    ["regions", "Regions for bulk odds (eu = Pinnacle; add uk for UK books, 2× cost)", "text"],
    ["props_regions", "Regions for props/BTTS", "text"],
    ["monthly_credits", "Monthly credits on your plan", "number", 1],
    ["credit_reset_day", "Credits reset on day of month", "number", 1],
    ["credit_reserve", "Credits to keep in reserve", "number", 1],
    ["lookahead_hours", "Only fetch games within (hours)", "number", 6],
    ["markets_by_group", "Markets per sport group", "json"],
  ]],
  ["Auto-refresh & sports", [
    ["auto_refresh", "Auto-refresh enabled", "checkbox"],
    ["refresh_interval_hours", "Every (hours)", "number", 1],
    ["refresh_groups", "Groups to auto-refresh", "json"],
    ["other_sport_keys", "'Other' sport keys", "json"],
    ["include_active_tennis", "Include all active tennis tournaments", "checkbox"],
  ]],
];

function renderSettings() {
  const s = state.settings;
  const field = ([key, label, type, extra]) => {
    const v = s[key];
    if (type === "checkbox") return `<label class="check"><input type="checkbox" name="${key}" ${v ? "checked" : ""}> ${esc(label)}</label>`;
    if (type === "select") return `<label>${esc(label)}<select name="${key}">${extra.map((o) => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("")}</select></label>`;
    if (type === "json") return `<label>${esc(label)}<textarea name="${key}" data-json>${esc(JSON.stringify(v, null, 1))}</textarea></label>`;
    return `<label>${esc(label)}<input type="${type}" name="${key}" value="${esc(v)}" ${extra ? `step="${extra}"` : ""}></label>`;
  };
  $("#settings-form").innerHTML = SETTINGS_FORM.map(([title, fields]) =>
    `<fieldset><legend>${esc(title)}</legend>${fields.map(field).join("")}</fieldset>`).join("") +
    `<div class="settings-actions"><button class="btn primary" type="submit">Save settings</button></div>`;
  const m = state.status.models;
  const fb = Object.entries(m.football).map(([k, v]) => `<li>Football ${k}: ${v.teams} teams, home adv ${v.home_adv}, ρ ${v.rho}, fitted ${ago(v.fitted)}</li>`);
  const us = Object.entries(m.ratings).map(([k, v]) => `<li>${k}: ${v.teams} teams, HFA ${v.hfa} pts, fitted ${ago(v.fitted)}</li>`);
  const errs = Object.entries(m.errors).map(([k, v]) => `<li class="neg">${esc(k)}: ${esc(v)}</li>`);
  $("#model-status").innerHTML = m.demo ? `<p class="muted">Demo mode: the model is simulated.</p>`
    : `<ul>${[...fb, ...us, ...errs].join("") || "<li class='muted'>No models fitted yet. They fit automatically after a refresh.</li>"}</ul>`;
}

$("#settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const out = {};
  for (const el of $$("[name]", ev.target)) {
    const key = el.name;
    try {
      if (el.type === "checkbox") out[key] = el.checked;
      else if (el.dataset.json !== undefined) out[key] = JSON.parse(el.value);
      else if (el.type === "number") out[key] = Number(el.value);
      else out[key] = el.value.trim();
    } catch (_) { return toast(`Invalid JSON in ${key}`); }
  }
  state.settings = await api("/api/settings", { method: "POST", body: out });
  toast("Settings saved");
  await loadStatus();
  loadValue();
});

$("#btn-rebuild").addEventListener("click", async () => {
  toast("Refitting models…");
  await api("/api/models/rebuild", { method: "POST" });
  await loadStatus();
  renderSettings();
  toast("Models refitted");
});

// ---------- boot ----------

(async function init() {
  try {
    [state.settings] = await Promise.all([api("/api/settings"), loadStatus()]);
    $("#f-edge").value = (state.settings.min_edge * 100).toFixed(1);
    $("#a-legs").value = state.settings.acca_default_legs;
    renderGroupChips();
    renderAccaGroups();
    await loadValue();
    setInterval(loadStatus, 60000);
  } catch (e) { toast(`Failed to load: ${e.message}`, 8000); }
})();
