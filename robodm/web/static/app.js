"use strict";
// RoboDM Table — a chat-first table: the conversation with the DM in the middle, everything else in a drawer
// (scene & battle map, party, world, codex, settings). Live updates over server-sent events. No build step.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
};

let view = store.get("robodm-view", "player");
let panel = store.get("robodm-panel", "");
let codexTab = store.get("robodm-codex", "quests");
let state = null;
let mapStamp = Date.now();
let live = null;            // the DM message being streamed: {el, body, chips, text}
let sentHere = false;       // this page sent the message the DM is answering
let loreOpen = null;
let personOpen = null;
let phrasings = [""];
let wasFighting = false;

const PANELS = { scene: "Scene", party: "Party", world: "World", codex: "Codex", settings: "Settings" };
const CODEX_TABS = [["quests", "Quests"], ["companions", "Companions"], ["people", "People"], ["memory", "Memory"], ["lore", "Lore"], ["rules", "Rules"], ["log", "Log"]];

// ------------------------------------------------------------------ helpers
async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  const ct = r.headers.get("Content-Type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || `${r.status}`);
  return data;
}
const withView = p => p + (p.includes("?") ? "&" : "?") + "view=" + view;
const isHost = () => !state || state.host !== false;

function inline(t) {
  return esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$|[.,;:!?)])/g, "$1<em>$2</em>");
}

// A small Markdown subset: headings, lists, code blocks, paragraphs, bold/italic/code.
function md(src) {
  const out = [];
  let para = [], list = null, code = null;
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const endList = () => { if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; } };
  for (const raw of String(src || "").replace(/\r/g, "").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (code !== null) {
      if (line.startsWith("```")) { out.push(`<pre>${esc(code.join("\n"))}</pre>`); code = null; } else code.push(raw);
      continue;
    }
    if (line.startsWith("```")) { flush(); endList(); code = []; continue; }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) { flush(); endList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    const li = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (li) { flush(); (list = list || []).push(`<li>${inline(li[1])}</li>`); continue; }
    if (!line.trim()) { flush(); endList(); continue; }
    endList(); para.push(line.trim());
  }
  if (code !== null) out.push(`<pre>${esc(code.join("\n"))}</pre>`);
  flush(); endList();
  return out.join("");
}

function bar(hp, max) {
  if (hp == null || !max) return "";
  const pct = Math.max(0, Math.min(100, Math.round((hp / max) * 100)));
  return `<div class="bar ${pct < 30 ? "low" : pct < 60 ? "mid" : ""}"><i style="width:${pct}%"></i></div>`;
}
const pips = (left, max) => `<span class="pips">${Array.from({ length: max }, (_, i) => `<i class="${i < left ? "on" : ""}"></i>`).join("")}</span>`;
const fmtMod = n => (n >= 0 ? "+" : "") + n;
function colName(x) { let s = ""; x += 1; while (x) { const r = (x - 1) % 26; s = String.fromCharCode(65 + r) + s; x = Math.floor((x - 1) / 26); } return s; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
// A Blades-style progress clock: a ring of `n` wedges, `f` of them filled.
function clockRing(f, n, size = 44) {
  const c = size / 2, r = c - 2;
  if (n <= 1) return `<svg class="clock-ring" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}"><circle cx="${c}" cy="${c}" r="${r}" class="seg ${f >= 1 ? "on" : ""}"/></svg>`;
  let paths = "";
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * 2 * Math.PI - Math.PI / 2, a1 = ((i + 1) / n) * 2 * Math.PI - Math.PI / 2;
    const p = a => `${(c + r * Math.cos(a)).toFixed(2)},${(c + r * Math.sin(a)).toFixed(2)}`;
    paths += `<path d="M${c},${c} L${p(a0)} A${r},${r} 0 0 1 ${p(a1)} Z" class="seg ${i < f ? "on" : ""}"/>`;
  }
  return `<svg class="clock-ring" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-label="${f} of ${n}">${paths}</svg>`;
}
function stance(rep) {
  const pct = Math.abs(rep) * 10;
  return `<div class="stance"><i class="${rep < 0 ? "neg" : "pos"}" style="width:${pct}%;${rep < 0 ? "right:50%" : "left:50%"}"></i></div>`;
}

// ------------------------------------------------------------------ state
async function loadState() {
  try { state = await api(withView("/api/state")); } catch (e) { console.warn(e); return; }
  renderHeader();
  renderSeat();
  renderStrip();
  renderRound();
  renderWhispers();
  const fighting = !!state.combat;
  if (fighting && !wasFighting && !panel && window.innerWidth >= 1180) openPanel("scene");
  wasFighting = fighting;
  const editing = (panel === "party" && (forgeOpen || levelOpen)) || panel === "settings" || (panel === "scene" && movePreview);
  if (panel && !editing && ["scene", "party", "world"].includes(panel)) renderPanel();
  if (panel === "codex" && ["quests", "companions", "log"].includes(codexTab)) renderPanel();
}

function renderHeader() {
  const c = state.campaign;
  document.body.classList.toggle("dm-view", view === "dm");
  $("#view-toggle").textContent = view === "dm" ? "🕯 DM view" : "👁 Player view";
  if (!c) { $("#camp-name").textContent = "RoboDM: no campaign yet"; return; }
  $("#camp-name").textContent = c.name;
  document.title = `${c.name} · RoboDM`;
  $("#session-pill").textContent = c.session_active ? `Session ${c.session}` : `Next: session ${c.session + 1}`;
  const wd = state.world || {};
  $("#time-pill").textContent = wd.tracking ? `🕰 Day ${wd.day} · ${wd.word}` : "";
  $("#time-pill").title = wd.time || "In-game time isn't tracked yet";
  const u = state.usage;
  $("#cost-pill").textContent = u && u.turns ? (u.engine && u.engine !== "claude" ? `🖥 local · ${u.turns} turn${u.turns > 1 ? "s" : ""}`
    : `≈$${u.cost.toFixed(2)} · ${u.turns} turn${u.turns > 1 ? "s" : ""}`) : "";
  const audit = state.audit || {};
  $("#audit-pill").textContent = audit.open ? `⚖️ ${audit.open} to audit` : "";
  $("#audit-pill").className = "pill quiet" + (audit.open ? " warn" : "");
  if (!state.seat) {
    const sel = $("#as-select"), want = store.get("robodm-as", "");
    const opts = ['<option value="">—</option>'].concat(state.party.map(p => `<option ${p.name === want ? "selected" : ""}>${esc(p.name)}</option>`));
    if (sel.dataset.sig !== opts.join()) { sel.innerHTML = opts.join(""); sel.dataset.sig = opts.join(); }
  }
  const rw = $("#rewind"), undo = state.undo;
  rw.classList.toggle("hidden", view !== "dm" || !undo);
  if (undo) rw.title = `Rewind to the start of turn ${undo.turn}${undo.label ? ` (“${undo.label}”)` : ""}`;
  const dm = state.dm || {};
  const st = $("#dm-status");
  st.className = "dm-status " + (!dm.available ? "offline" : dm.busy ? "busy" : "ready");
  $("b", st).textContent = !dm.available ? "DM offline" : dm.busy ? "DM is thinking…" : "DM ready";
  const model = dm.model && dm.model !== "default" ? dm.model : "";
  $("#dm-engine").textContent = dm.engine === "claude" ? `Claude${model ? " · " + model : ""}${dm.effort && dm.effort !== "default" ? " · " + dm.effort : ""}`
    : dm.engine ? `${model || dm.engine_name}${dm.effort ? " · " + dm.effort : ""}` : "";
  const banner = $("#dm-banner");
  banner.classList.toggle("hidden", !!dm.available);
  if (!dm.available) banner.innerHTML = "<b>The DM isn't connected.</b> " + inline(dm.help || "") + (isHost() ? ' <button type="button" class="ghost small" data-open="settings">⚙ Settings</button>' : "");
  setBusy(!!dm.busy);
  $$(".top-actions [data-open]").forEach(b => b.classList.toggle("active", b.dataset.open === panel && b.classList.contains("icon")));
}

function renderSeat() {
  const seat = state.seat;
  document.body.classList.toggle("seat", !!seat);
  $("#view-toggle").classList.toggle("hidden", !!seat);
  const sel = $("#as-select");
  if (seat) { sel.innerHTML = `<option>${esc(seat.name)}</option>`; sel.disabled = true; sel.dataset.sig = ""; }
  else sel.disabled = false;
  $("#round-start").classList.toggle("hidden", !isHost() || (state.round && state.round.active));
}

function renderStrip() {
  const cb = state.combat, strip = $("#combat-strip");
  strip.classList.toggle("hidden", !cb);
  if (!cb) { strip.innerHTML = ""; return; }
  strip.innerHTML = `<span class="round">⚔️ Round ${cb.round}</span>`
    + cb.order.map(c => `<span class="who-chip side-${esc(c.side)} ${c.current ? "current" : ""} ${c.dead ? "dead" : ""}" title="${esc(c.name)}${c.pos ? " · " + esc(c.pos) : ""}">
        <span>${c.current ? "▶ " : ""}${esc(c.name)}</span>${c.hp != null ? bar(c.hp, c.max_hp) : `<span class="meta">${esc(c.health)}</span>`}</span>`).join("")
    + (cb.unseen_turn ? '<span class="meta">▶ someone unseen…</span>' : "")
    + `<button type="button" class="ghost small" data-open="scene">🗺 Map</button>`;
}

function setBusy(busy) {
  $("#chat-send").disabled = busy || !(state && state.dm && state.dm.available);
  $("#chat-cancel").classList.toggle("hidden", !busy);
}

// ------------------------------------------------------------------ the drawer
function openPanel(name) {
  if (panel === name && document.body.classList.contains("drawer-open")) { closePanel(); return; }
  panel = name; store.set("robodm-panel", name);
  if (name !== "party") { forgeOpen = false; levelOpen = null; }
  document.body.classList.add("drawer-open");
  $("#drawer").setAttribute("aria-hidden", "false");
  renderPanel();
  if (state) renderHeader();
}

function closePanel() {
  panel = ""; store.set("robodm-panel", "");
  movePreview = null; forgeOpen = false; levelOpen = null;
  document.body.classList.remove("drawer-open");
  $("#drawer").setAttribute("aria-hidden", "true");
  if (state) renderHeader();
}

async function renderPanel() {
  if (!panel || !state) return;
  if (!state.campaign && panel !== "settings") { $("#drawer-body").innerHTML = '<p class="empty">No campaign yet. Create one with <code>./dm campaign new "Name"</code>.</p>'; return; }
  $("#drawer-title").textContent = panel === "scene" && state.combat ? (state.combat.name || "Combat") : PANELS[panel];
  const tabs = $("#drawer-tabs");
  tabs.classList.toggle("hidden", panel !== "codex");
  if (panel === "codex") tabs.innerHTML = CODEX_TABS.map(([k, label]) => `<button type="button" data-codex="${k}" class="${k === codexTab ? "active" : ""}">${label}</button>`).join("");
  const body = $("#drawer-body");
  const scroll = body.scrollTop;
  if (panel === "scene") await renderScene(body);
  else if (panel === "party") body.innerHTML = PANES.party();
  else if (panel === "world") body.innerHTML = PANES.world();
  else if (panel === "settings") await renderSettings(body);
  else if (panel === "codex") {
    body.innerHTML = PANES[codexTab]();
    if (codexTab === "lore") await renderLore();
    if (codexTab === "people") await renderPeople();
    if (codexTab === "log") await renderLog();
  }
  body.scrollTop = scroll;
  if (panel === "party" && forgeOpen) forgeRefresh();
}

// ------------------------------------------------------------------ chat
function bubble(role, text, who) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const label = who || (role === "dm" ? "Dungeon Master" : role === "player" ? "You" : role === "system" ? "RoboDM" : "Error");
  el.innerHTML = `<div class="who">${esc(label)}</div><div class="body"></div><div class="chips"></div>`;
  $(".body", el).innerHTML = role === "player" ? esc(text).replace(/\n/g, "<br>") : md(text);
  $("#messages").appendChild(el);
  scrollChat();
  return el;
}
const scrollChat = () => { const m = $("#messages"); m.scrollTop = m.scrollHeight; };

const seenWhispers = new Set();
let historyLoaded = false;

function whisperBubble(w) {
  const toMe = w.from === "dm";
  const el = bubble("dm", w.text, toMe ? `🔒 DM → ${w.name} (whisper)` : `🔒 ${w.name} → DM`);
  el.classList.add("private");
}

function renderWhispers() {
  for (const w of state.whispers || []) {
    if (seenWhispers.has(w.id)) continue;
    seenWhispers.add(w.id);
    if (historyLoaded) whisperBubble(w);
  }
}

async function loadHistory() {
  try {
    const { messages } = await api("/api/chat/history");
    $("#messages").innerHTML = "";
    if (!messages.length) bubble("dm", "_Welcome to the table. Say what you do, or ask the DM to pick up where you left off._");
    for (const m of messages) {
      const who = m.role === "dm" ? "Dungeon Master" : (m.text.split(":")[0].length < 30 && m.text.includes(":") ? m.text.split(":")[0] : "You");
      const el = bubble(m.role === "dm" ? "dm" : "player", m.text, (m.private_to ? "🔒 " : "") + who);
      if (m.private_to) el.classList.add("private");
    }
    for (const w of (state && state.whispers) || []) { seenWhispers.add(w.id); whisperBubble(w); }
    historyLoaded = true;
  } catch (e) { /* no campaign yet */ }
}

// The reply arrives whole when the turn is done (the hooks can send the DM back to work after it first writes);
// until then the bubble shows what the DM is doing.
function working(label) {
  if (!live || live.text) return;
  live.body.innerHTML = `<p class="working">The DM is working… <span>${esc(label || "")}</span></p>`;
}

let renderPending = false;
function renderLive() {
  if (renderPending || !live) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; if (live) { live.body.innerHTML = md(live.text); live.body.classList.add("typing"); scrollChat(); } });
}

function onChat(ev) {
  if (ev.kind === "start") {
    if (!sentHere) {
      const b = bubble("player", ev.text, ev.audience ? "🔒 Private" : (ev.text.includes(":") ? ev.text.split(":")[0] : "Player"));
      if (ev.audience) b.classList.add("private");
    }
    sentHere = false;
    const el = bubble("dm", "", ev.audience ? "🔒 Dungeon Master (private)" : "Dungeon Master");
    if (ev.audience) el.classList.add("private");
    live = { el, body: $(".body", el), chips: $(".chips", el), text: "" };
    live.body.classList.add("typing");
    working("Reading the table…");
    setBusy(true);
  } else if (ev.kind === "delta" && live) {
    live.text += ev.text; renderLive();
  } else if (ev.kind === "replace" && live) {
    live.text = ev.text || ""; renderLive();
  } else if (ev.kind === "tool" && live) {
    const chip = document.createElement("span");
    chip.className = "chip" + (ev.sub ? " sub" : "");
    chip.textContent = `${ev.icon} ${ev.label}`;
    chip.title = ev.label;
    live.chips.appendChild(chip);
    if (!live.text) working(ev.label.startsWith("consulting rules-lawyer") ? "Checking the rules…" : ev.label);
    scrollChat();
  } else if (ev.kind === "meta" && live) {
    const bits = [];
    if (ev.ms) bits.push(`⏱ ${Math.round(ev.ms / 1000)}s`);
    if (ev.engine && ev.engine !== "claude") bits.push(`🖥 ${ev.model || ev.engine}`);
    else if (ev.cost != null && isHost()) bits.push(`≈$${Number(ev.cost).toFixed(3)}`);
    if (ev.turns) bits.push(`${ev.turns} steps`);
    if (bits.length) { const c = document.createElement("span"); c.className = "chip sub"; c.textContent = bits.join(" · "); live.chips.appendChild(c); }
  } else if (ev.kind === "done" && live) {
    if (ev.text) live.text = ev.text;
    live.body.innerHTML = md(live.text || "_(no reply)_");
    live.body.classList.remove("typing");
  } else if (ev.kind === "error") {
    bubble("error", ev.text);
  } else if (ev.kind === "idle") {
    if (live) live.body.classList.remove("typing");
    live = null;
    setBusy(false);
    loadState();
  }
}

async function sendChat(text) {
  text = text.trim();
  if (!text) return;
  const as = $("#as-select").value;
  if (state && state.round && state.round.active) {           // round mode: declare, don't send
    $("#chat-input").value = "";
    try { await api("/api/round/declare", { text, as }); loadState(); } catch (e) { bubble("error", e.message); }
    return;
  }
  const priv = $("#chat-private").checked, ooc = $("#chat-ooc").checked;
  const el = bubble("player", text, `${priv ? "🔒 " : ""}${as || "You"}${priv ? " → DM" : ""}${ooc ? " (OOC)" : ""}`);
  if (priv) el.classList.add("private");
  sentHere = true;
  $("#chat-input").value = "";
  $("#chat-private").checked = false;
  $("#chat-ooc").checked = false;
  try { await api("/api/chat", { message: text, as, private: priv, ooc }); setBusy(true); }
  catch (e) { sentHere = false; bubble("error", e.message); }
}

async function rewind() {
  const u = state && state.undo;
  if (!u) return;
  if (!confirm(`Rewind the campaign to the start of turn ${u.turn}${u.label ? `\n(“${u.label}”)` : ""}?\n\nHP, positions, memories and the clock go back to how they were. The DM is told on your next message. You can redo it.`)) return;
  try {
    const r = await api("/api/command", { argv: ["undo"] });
    bubble(r.ok ? "system" : "error", (r.out || r.err || "").trim());
  } catch (e) { bubble("error", e.message); }
  loadState();
}

function renderRound() {
  const r = state.round || {}, el = $("#round-bar");
  el.classList.toggle("hidden", !r.active);
  $("#chat-input").placeholder = r.active ? "Declare your action for this round…" : "What do you do?";
  if (!r.active) { el.innerHTML = ""; return; }
  const done = Object.entries(r.actions || {});
  el.innerHTML = `<div><b>⏱ Round:</b> ${r.waiting.length ? `waiting for ${esc(r.waiting.join(", "))}` : "everyone has declared"}</div>
    ${done.map(([who, t]) => `<div class="meta">✔ <b>${esc(who)}</b>: ${esc(t)}</div>`).join("")}
    ${isHost() ? `<div class="actions"><button type="button" class="small" id="round-send" ${done.length ? "" : "disabled"}>Send to the DM now</button>
      <button type="button" class="ghost small" id="round-stop">End round mode</button></div>` : ""}`;
}

// ------------------------------------------------------------------ scene: the battle map, or a recap
let movePreview = null;          // {who, to} while a click-to-move preview is shown

async function renderScene(body) {
  const cb = state.combat;
  if (!cb) {
    try {
      const { text } = await api(withView("/api/recap"));
      body.innerHTML = `<div class="card prose">${md(text)}</div>`;
    } catch (e) { body.innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
    return;
  }
  const map = cb.has_map
    ? `<div class="map-wrap"><img id="map-img" alt="Battle map (click a square to plan a move)" title="Click a square to plan a move" src="${withView("/api/map.svg")}&t=${mapStamp}"></div>`
    : '<p class="empty">No battle map yet. The DM draws one before initiative.</p>';
  const cards = cb.order.map(c => `
    <div class="combatant side-${esc(c.side)} ${c.current ? "current" : ""} ${c.dead ? "dead" : ""}">
      <div class="line1"><span>${c.current ? "▶ " : ""}${esc(c.name)}</span><span class="init">${c.init ?? "–"}</span></div>
      ${c.hp != null ? bar(c.hp, c.max_hp) : ""}
      <div class="meta">${c.hp != null ? `HP ${c.hp}/${c.max_hp}${c.temp_hp ? ` +${c.temp_hp}` : ""}` : esc(c.health)}${c.ac != null ? ` · AC ${c.ac}` : ""}${c.pos ? ` · 📍${esc(c.pos)}` : ""}${c.current && c.speed != null ? ` · moved ${c.moved}/${c.speed} ft` : ""}</div>
      <div class="tags">${c.conditions.map(x => `<span class="tag">${esc(x)}</span>`).join("")}${c.concentration ? `<span class="tag conc">◎ ${esc(c.concentration)}</span>` : ""}${c.reaction_used ? '<span class="tag">reaction used</span>' : ""}</div>
    </div>`).join("");
  body.innerHTML = `<div class="meta">Round ${cb.round}${cb.fog ? " · 🌫 fog of war" : ""} · click a square to plan a move</div>` + map
    + '<div id="move-pop" class="move-pop hidden"></div>'
    + (cb.unseen_turn ? '<p class="meta">▶ Someone the party can\'t see is acting…</p>' : "")
    + `<div class="initiative">${cards}</div>`;
  const img = $("#map-img");
  if (img) img.addEventListener("click", mapClick);
}

async function mapClick(ev) {
  const cb = state && state.combat, g = cb && cb.geometry, img = ev.currentTarget;
  if (!g) return;
  const sx = ev.offsetX * g.total_w / img.clientWidth, sy = ev.offsetY * g.total_h / img.clientHeight;
  const x = Math.floor((sx - g.margin) / g.cell), y = Math.floor((sy - g.margin) / g.cell);
  if (x < 0 || y < 0 || x >= g.w || y >= g.h) { closeMove(); return; }
  const cell = `${colName(x)}${y + 1}`;
  const who = state.seat ? state.seat.combatant : (cb.current || "");
  const p = await api(`/api/path?to=${cell}${who && !state.seat ? `&who=${encodeURIComponent(who)}` : ""}`);
  movePreview = { who: p.id || who, to: cell };
  img.src = `${withView("/api/map.svg")}&preview=${encodeURIComponent((p.id || who) + ":" + cell)}&t=${Date.now()}`;
  const pop = $("#move-pop");
  pop.classList.remove("hidden");
  const bits = p.cost != null ? `${p.cost} ft (${p.left} ft left this turn)` : "";
  pop.innerHTML = `<div><b>🏃 ${esc(p.who || "Move")} → ${esc(cell)}</b> ${esc(bits)}</div>
    ${(p.opportunity || []).length ? `<div class="warn-line">⚠️ Leaves the reach of ${esc(p.opportunity.join(", "))}: opportunity attack${p.opportunity.length > 1 ? "s" : ""}</div>` : ""}
    ${(p.problems || []).map(x => `<div class="warn-line">⚠️ ${esc(x)}</div>`).join("")}
    <div class="actions"><button type="button" class="small" id="move-ask">Move here</button>
      ${isHost() && p.id ? `<button type="button" class="ghost small" id="move-now" title="Run combat move now">Move now (DM)</button>` : ""}
      <button type="button" class="ghost small" id="move-cancel">Cancel</button></div>`;
}

function closeMove() { movePreview = null; mapStamp = Date.now(); if (panel === "scene") renderPanel(); }

// ------------------------------------------------------------------ Character Forge & level-up
let forgeOpen = false, forgeOpts = null, levelOpen = null;
const ABS = ["str", "dex", "con", "int", "wis", "cha"];

function forgeForm() {
  const o = forgeOpts;
  if (!o) return '<div class="card forge"><p class="empty">Loading the rules data…</p></div>';
  const opt = (xs, sel) => xs.map(x => `<option ${x === sel ? "selected" : ""}>${esc(x)}</option>`).join("");
  return `<div class="card forge" id="forge">
    <h3>🛠 Character Forge <span class="meta">checked against the rules data</span></h3>
    <div class="grid2">
      <label>Name <input id="fg-name" autocomplete="off"></label>
      <label>Player <input id="fg-player" autocomplete="off"></label>
      <label>Race <select id="fg-race">${opt(o.races, "Human")}</select></label>
      <label>Class <select id="fg-class">${opt(o.classes.map(c => c.name), "Fighter")}</select></label>
      <label>Background <select id="fg-bg">${opt(o.backgrounds, "Soldier")}</select></label>
      <label>Ability scores <select id="fg-method"><option value="standard">Standard array (15 14 13 12 10 8)</option>
        <option value="pointbuy">Point buy (27 points)</option><option value="roll">Roll 4d6, drop lowest</option>
        <option value="manual">Manual (DM, flagged)</option></select></label>
    </div>
    <div class="abil-in">${ABS.map((a, i) => `<label>${a.toUpperCase()}<input type="number" id="fg-${a}" min="3" max="18" value="${o.standard[i]}"></label>`).join("")}</div>
    <div class="meta" id="fg-points"></div>
    <label>Racial choices <input id="fg-asi" placeholder="only if the race lets you choose, e.g. dex=1,wis=1"></label>
    <div id="fg-skills"></div>
    <label>Gear <input id="fg-gear" placeholder="e.g. Chain Mail, Shield, Longsword, Explorer's Pack"></label>
    <label class="tog"><input type="checkbox" id="fg-gold"> take starting gold instead</label>
    <div class="meta" id="fg-gear-opts"></div>
    <label>Feat <input id="fg-feat" placeholder="only if the race grants one (variant human)"></label>
    <div class="actions"><button type="button" class="ghost small" id="fg-roll">🎲 Roll scores</button>
      <button type="button" class="ghost" id="fg-preview">Preview</button><button type="button" id="fg-create">Create</button>
      <button type="button" class="ghost small" id="fg-close">Close</button></div>
    <div id="fg-out" class="prose"></div></div>`;
}

function forgeRefresh() {
  if (!forgeOpts || !$("#forge")) return;
  const cls = forgeOpts.classes.find(c => c.name === $("#fg-class").value) || {};
  const from = cls.skill_from || forgeOpts.skills;
  const had = new Set($$("#fg-skills input:checked").map(i => i.value));
  $("#fg-skills").innerHTML = `<div class="meta">Skills: choose ${cls.skill_count || 0} from the ${cls.skill_from ? "bold ones" : "list"}
    (plus any your race or background lets you pick). Your background's own skills are added automatically.</div>
    <div class="skill-grid">${forgeOpts.skills.map(s => `<label class="tog ${from.includes(s) ? "cls" : ""}"><input type="checkbox" value="${esc(s)}" ${had.has(s) ? "checked" : ""}> ${esc(s)}</label>`).join("")}</div>`;
  $("#fg-gear-opts").innerHTML = cls.gear ? `${esc(cls.name)} starting options: ${cls.gear.map(g => esc(g)).join(" · ")}${cls.gold ? ` · or ${esc(cls.gold)} gp` : ""}` : "";
  if ($("#fg-method").value === "pointbuy") {
    const cost = ABS.reduce((n, a) => n + (forgeOpts.point_cost[$("#fg-" + a).value] ?? 99), 0);
    $("#fg-points").textContent = cost > 90 ? "Point buy scores run 8–15." : `Point buy: ${cost}/${forgeOpts.budget} points`;
  } else $("#fg-points").textContent = "";
}

function forgeArgv(preview) {
  const v = id => ($("#" + id).value || "").trim();
  const argv = ["pc", "build", v("fg-name"), "--race", v("fg-race"), "--class", v("fg-class"), "--background", v("fg-bg"),
    "--scores", v("fg-method"), "--assign", ABS.map(a => `${a}=${v("fg-" + a)}`).join(",")];
  const skills = $$("#fg-skills input:checked").map(i => i.value);
  if (skills.length) argv.push("--skills", skills.join(","));
  if (v("fg-asi")) argv.push("--asi", v("fg-asi"));
  if (v("fg-feat")) argv.push("--feat", v("fg-feat"));
  if (v("fg-player")) argv.push("--player", v("fg-player"));
  if ($("#fg-gold").checked) argv.push("--gold"); else if (v("fg-gear")) argv.push("--gear", v("fg-gear"));
  if (preview) argv.push("--preview");
  return argv;
}

async function runInto(el, argv) {
  el.innerHTML = '<p class="meta">…</p>';
  try {
    const r = await api("/api/command", { argv });
    const text = (r.out || r.err || "").replace(/^Error: /, "").trim();
    el.innerHTML = text.split("\n").map(l => `<div class="${/^(⚠️|- )/.test(l) ? "warn-line" : ""}">${inline(l) || "&nbsp;"}</div>`).join("");
    return r.ok;
  } catch (e) { el.innerHTML = `<p class="meta">${esc(e.message)}</p>`; return false; }
}

function levelPanel(p) {
  return `<div class="card lv" id="lv-panel"><b>⬆ Level up ${esc(p.name)}</b>
    <div class="grid2">
      <label>Class <input id="lv-class" placeholder="${esc(p.classes)} (blank = same)"></label>
      <label>Hit points <select id="lv-hp"><option value="">average</option><option value="roll">roll</option></select></label>
      <label>Subclass <input id="lv-sub" placeholder="when the class gets one"></label>
      <label>Ability Score Improvement <input id="lv-asi" placeholder="str=2 · str=1,con=1 · later"></label>
      <label>Feat instead <input id="lv-feat" placeholder="feat name"></label>
    </div>
    <div class="actions"><button type="button" class="ghost" id="lv-preview">Preview</button><button type="button" id="lv-apply">Level up</button>
      <button type="button" class="ghost small" id="lv-close">Close</button></div>
    <div id="lv-out" class="prose"></div></div>`;
}

function levelArgv(preview) {
  const v = id => ($("#" + id).value || "").trim();
  const argv = ["pc", "levelup", levelOpen];
  if (v("lv-hp")) argv.push(v("lv-hp"));
  if (v("lv-class")) argv.push("--class", v("lv-class"));
  if (v("lv-sub")) argv.push("--subclass", v("lv-sub"));
  if (v("lv-asi")) argv.push("--asi", v("lv-asi"));
  if (v("lv-feat")) argv.push("--feat", v("lv-feat"));
  if (preview) argv.push("--preview");
  return argv;
}

async function showSeats() {
  const out = $("#seat-links");
  if (!out) return;
  try {
    const { seats, lan, port } = await api("/api/seats");
    const bases = (lan && lan.length ? lan.map(a => `http://${a}:${port}`) : []).concat(location.origin);
    out.innerHTML = `<p class="meta">Give each player their own link (phones on your Wi-Fi need <code>./dm web --lan</code>).
      A seat sees the player view, its own sheet and its own private messages. <code>./dm web --new-token</code> revokes them all.</p>`
      + seats.map(s => `<div class="seat-row"><b>${esc(s.name)}</b>
        <input readonly value="${esc(bases[0])}/?seat=${esc(s.slug)}&key=${esc(s.key)}" onclick="this.select()">
        <button type="button" class="ghost small" data-copy="${esc(bases[0])}/?seat=${esc(s.slug)}&key=${esc(s.key)}">Copy</button></div>`).join("");
  } catch (e) { out.textContent = e.message; }
}

// ------------------------------------------------------------------ panes
const PANES = {
  party() {
    const top = isHost() ? `<div class="row-actions"><button type="button" class="ghost small" id="seats-open">🔗 Player links</button>
        <button type="button" class="ghost small" id="forge-open">＋ New character</button></div><div id="seat-links"></div>`
      + (forgeOpen ? forgeForm() : "") : "";
    if (!state.party.length) return top + '<p class="empty">No characters yet.</p>';
    return top + state.party.map(p => p.public ? `
      <div class="card sheet public">
        <h3>${esc(p.name)} <span class="meta">${esc([p.race, p.classes].filter(Boolean).join(" · "))}</span></h3>
        ${bar(p.hp, p.max_hp)}
        <div class="kv"><span><b>HP</b> ${p.hp}/${p.max_hp}${p.temp_hp ? ` +${p.temp_hp}` : ""}</span><span><b>AC</b> ${p.ac}</span><span><b>Speed</b> ${p.speed}</span></div>
        ${p.conditions.length ? `<div class="tags">${p.conditions.map(x => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : ""}
      </div>` : `
      <div class="card sheet">
        <h3>${esc(p.name)} <span class="meta">${esc([p.race, p.classes].filter(Boolean).join(" · "))}${p.subclass ? ` (${esc(p.subclass)})` : ""}</span></h3>
        ${bar(p.hp, p.max_hp)}
        <div class="kv"><span><b>HP</b> ${p.hp}/${p.max_hp}${p.temp_hp ? ` +${p.temp_hp}` : ""}</span><span><b>AC</b> ${p.ac}</span><span><b>Speed</b> ${p.speed}</span>
          <span><b>Init</b> ${fmtMod(p.init)}</span><span><b>PP</b> ${p.passive}</span><span><b>Prof</b> ${fmtMod(p.prof)}</span>${p.spell_dc ? `<span><b>Spell DC</b> ${p.spell_dc}</span>` : ""}</div>
        ${p.conditions.length ? `<div class="tags">${p.conditions.map(x => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : ""}
        ${p.hp <= 0 ? `<div class="meta">Death saves: ${p.death_saves.success || 0}✓ ${p.death_saves.fail || 0}✗</div>` : ""}
        <div class="stats">${Object.entries(p.abilities).map(([k, a]) => `<div class="stat"><b>${k.toUpperCase()}</b><span>${fmtMod(a.mod)}</span><small>${a.score} · save ${fmtMod(a.save)}</small></div>`).join("")}</div>
        ${p.slots.length ? `<div class="kv">${p.slots.map(s => `<span><b>${esc(s.label)}</b> ${pips(s.left, s.max)}</span>`).join("")}</div>` : ""}
        ${p.resources.length ? `<div class="kv">${p.resources.map(r => `<span><b>${esc(r.name)}</b> ${pips(r.left, r.max)}</span>`).join("")}</div>` : ""}
        ${p.attacks.length ? `<div class="list" style="margin-top:6px">${p.attacks.map(a => `<button type="button" class="row-btn" data-say="I attack with my ${esc(a.name)}">⚔️ ${esc(a.name)} <span class="meta">${a.to_hit != null ? fmtMod(a.to_hit) : ""} · ${esc(a.damage)}${a.range ? ` · ${a.range.join("/")} ft` : ""}</span></button>`).join("")}</div>` : ""}
        <details><summary>Skills, spells, gear</summary>
          ${Object.keys(p.skills).length ? `<div class="kv">${Object.entries(p.skills).map(([s, v]) => `<span><b>${esc(s)}</b> ${fmtMod(v)}</span>`).join("")}</div>` : ""}
          ${p.spells.length ? `<p class="meta"><b>Spells:</b> ${esc(p.spells.join(", "))}</p>` : ""}
          ${p.features.length ? `<p class="meta"><b>Features:</b> ${esc(p.features.join(", "))}</p>` : ""}
          ${p.equipped.length || p.attuned.length ? `<p class="meta"><b>Wearing:</b> ${esc(p.equipped.join(", ") || "—")}${p.attuned.length ? ` · <b>Attuned</b> (${p.attuned.length}/3): ${esc(p.attuned.join(", "))}` : ""}</p>` : ""}
          ${p.inventory.length ? `<p class="meta"><b>Gear:</b> ${esc(p.inventory.join(", "))}</p>` : ""}
          <p class="meta"><b>Gold</b> ${p.gold} · <b>XP</b> ${p.xp} · <b>Hit dice</b> ${esc(p.hit_dice)}</p>
          ${p.notes ? `<p class="meta">${esc(p.notes)}</p>` : ""}
        </details>
        ${isHost() || (state.seat && state.seat.slug === p.slug) ? `<div class="row-actions"><button type="button" class="ghost small" data-level="${esc(p.slug)}">⬆ Level up</button></div>` : ""}
        ${levelOpen === p.slug ? levelPanel(p) : ""}
      </div>`).join("");
  },
  companions() {
    if (!state.companions.length) return '<p class="empty">No companions yet. The DM can make one with <code>/companion new</code>.</p>';
    return state.companions.map(c => `
      <div class="card">
        <h3>${esc(c.name)} <span class="status-badge ${esc(c.status)}">${esc(c.status)}</span>${c.wants_to_speak ? ' <span class="pill warn">wants to speak</span>' : ""}</h3>
        <p class="prose">${inline(c.public)}</p>
        ${Object.keys(c.mood).length ? `<div class="tags">${Object.entries(c.mood).map(([w, m]) => `<span class="tag">${esc(w)}: ${esc(m)}</span>`).join("")}</div>` : ""}
        ${c.voice ? `<details><summary>Voice & mannerisms</summary><div class="prose">${md(c.voice)}</div></details>` : ""}
        ${view === "dm" && c.agenda ? `
          <details><summary>Agenda (${c.agenda.filter(a => a.status === "open").length} open) · ${c.consults} consults · agent ${esc(c.agent)}</summary>
            <ul>${c.agenda.filter(a => a.status === "open").map(a => `<li>[${esc(a.id)}] ${esc(a.text)}</li>`).join("")}</ul></details>
          <details><summary>Recent memories</summary><ul>${c.memories.map(m => `<li>${esc(m.text)}</li>`).join("")}</ul></details>
          <details><summary>Persona (DM view)</summary><pre class="raw">${esc(c.persona)}</pre></details>` : ""}
      </div>`).join("");
  },
  quests() {
    if (!state.quests.length) return '<p class="empty">No quests.</p>';
    return state.quests.map(q => `<div class="card"><h3>${esc(q.title)} <span class="meta">${esc(q.status)}</span></h3>
      ${q.giver ? `<div class="meta">From ${esc(q.giver)}</div>` : ""}${q.notes ? `<p class="meta">${esc(q.notes)}</p>` : ""}</div>`).join("");
  },
  world() {
    const w = state.world || {};
    const parts = [];
    parts.push(`<div class="card"><h3>🕰 ${w.tracking ? esc(w.time) : "Time isn't tracked yet"}</h3>${w.place ? `<div class="meta">📍 ${esc(w.place)}</div>` : ""}
      ${w.cast && w.cast.length ? `<div class="list">${w.cast.map(p => `<div><b>${esc(p.name)}</b>${p.summary ? ` <span class="meta">${esc(p.summary)}</span>` : ""}${view === "dm" && p.mind ? `<div class="meta dm-only">🧠 ${esc(p.mind)}</div>` : ""}</div>`).join("")}</div>` : ""}
      ${!w.tracking ? '<p class="meta">The DM can start the clock with <code>./dm time set &lt;day&gt; &lt;HH:MM&gt;</code>.</p>' : ""}</div>`);
    if (w.timers && w.timers.length) parts.push(`<div class="card"><h3>Effects running</h3><ul>${w.timers.map(x => `<li>⌛ ${esc(x.text)} <span class="meta">${esc(x.left)}</span></li>`).join("")}</ul></div>`);
    if (w.clocks && w.clocks.length) parts.push(`<div class="card"><h3>Clocks</h3><div class="clocks">${w.clocks.map(c => `
      <div class="clock ${c.status}">${c.segments ? clockRing(c.filled, c.segments) : '<div class="clock-ring hourglass">⌛</div>'}
        <div><b>${esc(c.name)}</b>${c.status === "filled" ? ' <span class="pill warn">now</span>' : ""}${view === "dm" && !c.public ? ' <span class="tag">hidden</span>' : ""}
          <div class="meta">${c.segments ? `${c.filled}/${c.segments}` : ""}${c.per ? ` · +1 every ${esc(c.per)}` : ""}${c.left ? ` · ${esc(c.left)} left` : c.deadline ? ` · by ${esc(c.deadline)}` : ""}</div>
          ${view === "dm" && c.on_fill ? `<div class="meta dm-only">→ ${esc(c.on_fill)}</div>` : ""}</div></div>`).join("")}</div></div>`);
    if (w.factions && w.factions.length) parts.push(`<div class="card"><h3>Factions</h3><div class="list">${w.factions.map(f => `
      <div><div class="line1"><b>${esc(f.name)}</b> <span class="meta">${esc(f.word)} (${fmtMod(f.rep)})</span></div>${stance(f.rep)}
        ${view === "dm" && f.agenda && f.agenda.length ? `<div class="meta dm-only">Working on: ${esc(f.agenda.map(a => a.text).join("; "))}${f.pace !== "normal" ? ` · pace ${esc(f.pace)}` : ""}</div>` : ""}</div>`).join("")}</div></div>`);
    if (view === "dm" && w.beats && w.beats.length) parts.push(`<div class="card dm-only"><h3>🌍 Off-screen beats to resolve</h3><ul>${w.beats.map(b => `<li>[${esc(b.id)}] ${inline(b.text)}</li>`).join("")}</ul>
      <p class="meta">The DM resolves each with <code>./dm world resolve &lt;id&gt; "…"</code>.</p></div>`);
    return parts.join("");
  },
  memory() {
    return `<form class="list" id="recall-form">
        ${phrasings.map((p, i) => `<div class="search"><input name="q" data-i="${i}" value="${esc(p)}" placeholder="${i ? "another way to say it" : "Search everything you know…"}"></div>`).join("")}
        <div class="search"><button type="button" class="ghost small" id="add-phrasing">+ phrasing</button><button type="submit">Recall</button></div>
      </form><div id="recall-hits" class="list"></div>`;
  },
  lore() { return `<div class="search"><input id="lore-filter" placeholder="Filter lore…"></div><div id="lore-list" class="list"></div>`; },
  people() { return `<div class="search"><input id="people-filter" placeholder="Filter people…"></div><div id="people-list" class="list"></div>`; },
  rules() { return `<form class="search" id="rules-form"><input id="rules-q" placeholder="Spell, condition, monster, rule…"><button type="submit">Look up</button></form><div id="rules-out" class="card prose hidden"></div>`; },
  log() { return '<div id="log-body" class="list"><p class="empty">Loading…</p></div>'; },
};

async function renderLore() {
  if (loreOpen) {
    try {
      const e = await api(withView(`/api/lore/${encodeURIComponent(loreOpen)}`));
      $("#drawer-body").innerHTML = `<button type="button" class="ghost small" id="lore-back">← All lore</button>
        <div class="card prose"><h1>${esc(e.name)}</h1><p class="meta">${esc(e.type)} · ${inline(e.summary)}</p>${md(e.body)}</div>`;
      return;
    } catch { loreOpen = null; }
  }
  const { entries } = await api(withView("/api/lore"));
  const draw = () => {
    const f = ($("#lore-filter") || {}).value?.toLowerCase() || "";
    const rows = entries.filter(e => !f || (e.name + " " + e.summary + " " + e.type).toLowerCase().includes(f));
    $("#lore-list").innerHTML = rows.map(e => `<button type="button" class="row-btn" data-lore="${esc(e.slug)}">${esc(e.name)} <span class="meta">${esc(e.type)} — ${esc(e.summary)}</span></button>`).join("") || '<p class="empty">Nothing matches.</p>';
  };
  draw();
  $("#lore-filter").addEventListener("input", draw);
}

async function renderPeople() {
  if (personOpen) {
    try {
      const p = await api(withView(`/api/people/${encodeURIComponent(personOpen)}`));
      $("#drawer-body").innerHTML = `<button type="button" class="ghost small" id="people-back">← Everyone</button>
        <div class="card prose">${md(p.text)}</div>` + (p.statblock ? `<div class="card prose">${md(p.statblock)}</div>` : "");
      return;
    } catch { personOpen = null; }
  }
  const { people } = await api(withView("/api/people"));
  const draw = () => {
    const f = ($("#people-filter") || {}).value?.toLowerCase() || "";
    const rows = people.filter(x => !f || [x.name, x.race, x.role, x.tier].join(" ").toLowerCase().includes(f));
    $("#people-list").innerHTML = rows.map(x => `<button type="button" class="row-btn" data-person="${esc(x.slug)}">${x.companion ? "🧭 " : x.tier === "major" ? "★ " : ""}${esc(x.name)} <span class="meta">${esc([x.race, x.role].filter(Boolean).join(" · "))}${x.status && x.status !== "alive" ? " · " + esc(x.status) : ""}${x.mind ? " · 🗣 " + esc(x.mind) : ""}</span></button>`).join("")
      || '<p class="empty">No NPC records yet (<code>./dm npc check</code>).</p>';
  };
  draw();
  $("#people-filter").addEventListener("input", draw);
}

async function renderLog() {
  try {
    const j = await api(withView("/api/journal"));
    const rows = j.transcript.map(r => `<div class="hit"><span class="kind">${esc(r.who)} · ${esc(r.time)}</span><div class="${r.who === "DM" ? "prose" : ""}">${md(r.text)}</div></div>`).join("");
    $("#log-body").innerHTML = (rows || `<p class="empty">No transcript for session ${j.session} yet.</p>`)
      + (j.log ? `<details><summary>DM's running log</summary><pre class="raw">${esc(j.log)}</pre></details>` : "");
  } catch (e) { $("#log-body").innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ settings: the DM engine, model, effort; table settings
let settings = null, draft = null, tableDraft = null, engineModels = {};
const ENGINE_BLURB = {
  claude: "Skills, hooks, subagents · your Claude plan",
  ollama: "Local models, e.g. llama3.1:8b",
  kobold: "Local GGUF models via its OpenAI-compatible API",
  openai: "LM Studio, llama.cpp server, vLLM…",
};
const TABLE_LABELS = {
  turn_speed: ["Turn speed", "fast: routine engine-computed events skip the rules-lawyer, smaller brief · balanced: everything audited · thorough: opus subagents, bigger brief (a model chosen under Subagents wins)"],
  narration: ["Narration", "How much description per beat"],
  companion_agents: ["Companions", "Consult companion agents at decision points (key), every time (always), or let the DM voice them (off)"],
  dice_mode: ["Dice", "auto: the DM rolls everything · players: players roll their own PC dice and report totals"],
  hide_monster_hp: ["Hide monster HP", "Describe monsters as wounded/bloodied instead of showing numbers"],
  crit_rule: ["Critical hits", "double_dice (rules as written) or max_plus_roll (house rule)"],
  grid_diagonals: ["Diagonals", "5: every diagonal costs 5 ft (PHB) · 5-10-5: the DMG variant"],
};

async function renderSettings(body) {
  if (!isHost()) { body.innerHTML = '<p class="empty">Only the host can change the DM and table settings.</p>'; return; }
  body.innerHTML = '<p class="meta">Loading…</p>';
  try { settings = await api("/api/settings"); } catch (e) { body.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  draft = JSON.parse(JSON.stringify(settings.engine));
  tableDraft = Object.assign({}, settings.campaign || {});
  engineModels = { [settings.engine.engine]: settings.models };
  drawSettings();
}

function seg(name, values, current, labels = {}) {
  return `<div class="seg">${values.map(v => `<button type="button" data-${name}="${esc(String(v))}" class="${String(v) === String(current) ? "on" : ""}">${esc(labels[v] ?? (v === "" ? "default" : String(v)))}</button>`).join("")}</div>`;
}

const AGENT_INFO = {
  "rules-lawyer": ["⚖️", "Audits every turn: pre-checks the first use of a spell, feature or ruling, then post-audits the turn's rules events."],
  companion: ["🗣", "Plays each companion's own mind when the DM consults them."],
  "lore-keeper": ["📜", "Builds and checks the world's lore and canon."],
  "npc-creator": ["🧑", "Writes NPC records: race, alignment, backstory, relationships, stat block or character sheet."],
};

function agentDraft(eng) {
  draft.agents = draft.agents || {};
  draft.agents[eng] = draft.agents[eng] || {};
  return draft.agents[eng];
}

function agentHint(role, own, dmEffort, local) {
  const s = settings, effort = own || dmEffort;
  if (!local) return own ? `Written to the agent file as effort: ${own}.` : `Inherits the DM's effort${dmEffort ? ` (${dmEffort})` : ""}.`;
  if (role === "rules-lawyer") {
    const p = s.lawyer_profiles[effort] || {};
    return `Reviews ${p.scope === "all" ? "every rules event" : "judgement calls (routine rolls the engine computed pass on their own)"}${p.retries ? ` · asked again up to ${p.retries}× when its answer can't be read` : ""}.`;
  }
  const p = s.companion_profiles[effort] || {};
  return `Answers up to about ${p.max_tokens} tokens · temperature ${p.temperature}.`;
}

function agentCard(agentStatus) {
  const s = settings, eng = draft.engine, local = eng !== "claude";
  const roles = Object.keys(s.agent_roles).filter(r => !local || s.local_roles.includes(r));
  const mine = agentDraft(eng);
  const models = eng === "claude" ? s.claude_models : (engineModels[eng] || []);
  const dmModel = (draft[eng] || {}).model || "";
  const dmEffort = draft.effort || (local ? "medium" : "");
  const rows = roles.map(r => {
    const a = mine[r] || {}, eff = a.effort || "";
    const [icon, blurb] = AGENT_INFO[r] || ["🤖", ""];
    const def = local ? `same as the DM${dmModel ? ": " + dmModel : ""}` : `turn speed (${s.turn_speed}): ${s.agent_defaults[r]}`;
    return `<div class="agent-row">
      <div class="agent-head"><b>${icon} ${esc(s.agent_roles[r])}</b><span class="hint">${esc(blurb)}</span></div>
      <label>Model <input data-agent-model="${esc(r)}" list="st-agent-models" value="${esc(a.model || "")}" placeholder="${esc(def)}" autocomplete="off"></label>
      <label>Effort <div class="seg">${["", ...s.efforts].map(v => `<button type="button" data-agent="${esc(r)}" data-agent-effort="${esc(v)}" class="${v === eff ? "on" : ""}">${v === "" ? "default" : esc(v)}</button>`).join("")}</div>
        <span class="hint">${esc(agentHint(r, eff, dmEffort, local))}</span></label>
    </div>`;
  }).join("");
  const note = eng === "claude" ? "Written into .claude/agents/*.md as model: and effort:. A blank model follows the campaign's turn speed; a blank effort inherits the DM's."
    : eng === "kobold" ? "KoboldCpp serves the one model it was started with, so subagents use it whatever is set here; their effort still applies."
    : eng === "ollama" ? "A blank model uses the DM's. A different model means Ollama keeps both loaded (more VRAM) or swaps them each turn."
    : "A blank model uses the DM's. The server must have any model named here.";
  return `<div class="card settings">
      <h3>🤖 Subagents <span class="meta">(${esc(s.engines[eng])})</span></h3>
      ${rows}<datalist id="st-agent-models">${models.map(m => `<option value="${esc(m)}">`).join("")}</datalist>
      <p class="hint">${esc(note)}</p>
      ${agentStatus ? `<div class="status-line">${agentStatus}</div>` : ""}
      <div class="actions"><button type="button" id="st-save-agents">Save subagents</button></div>
    </div>`;
}

function drawSettings(status, agentStatus) {
  const s = settings, e = draft, eng = e.engine, sub = e[eng] || {};
  const local = eng !== "claude";
  const models = eng === "claude" ? s.claude_models : (engineModels[eng] || []);
  const prof = s.effort_profiles[e.effort || "medium"] || {};
  const savedHere = eng === s.engine.engine;
  const statusLine = status || (savedHere ? (s.reachable ? `<span class="ok-line">✔ ${esc(s.engines[eng])}: ${esc(s.detail || "ready")}</span>`
    : `<span class="warn-line">✘ ${esc(s.engines[eng])}: ${esc(s.detail || s.model_error || "not reachable")}</span>`) : '<span class="meta">Not saved yet: press Save to switch the DM.</span>');
  const tc = s.campaign ? Object.keys(TABLE_LABELS).filter(k => k in s.campaign).map(k => {
    const vals = k === "hide_monster_hp" ? [true, false] : (s.choices[k] || []);
    const labels = k === "hide_monster_hp" ? { true: "hide", false: "show" } : {};
    return `<label>${esc(TABLE_LABELS[k][0])}${seg("camp-" + k, vals, tableDraft[k], labels)}<span class="hint">${esc(TABLE_LABELS[k][1])}</span></label>`;
  }).join("") : '<p class="meta">No campaign yet.</p>';
  $("#drawer-body").innerHTML = `<div class="settings">
    <div class="card settings">
      <h3>🧠 Dungeon Master</h3>
      <div class="engine-grid">${Object.entries(s.engines).map(([k, label]) => `<button type="button" data-engine="${k}" class="${k === eng ? "on" : ""}"><b>${esc(label)}</b><small>${esc(ENGINE_BLURB[k])}</small></button>`).join("")}</div>
      <div class="status-line">${statusLine}</div>
      <label>Model <input id="st-model" list="st-models" value="${esc(sub.model || "")}" placeholder="${eng === "claude" ? "default (Claude Code's own setting)" : "e.g. llama3.1:8b"}" autocomplete="off">
        <datalist id="st-models">${models.map(m => `<option value="${esc(m)}">`).join("")}</datalist>
        <span class="hint">${eng === "claude" ? "fable, opus, sonnet, haiku, or a full model id. Passed to Claude Code as --model." : models.length ? `On this server: ${esc(models.join(", "))}` : "Press “Test connection” to list the server's models."}</span></label>
      <label>Effort ${seg("effort", eng === "claude" ? ["", ...s.efforts] : s.efforts, e.effort || (local ? "medium" : ""))}
        <span class="hint">${eng === "claude" ? "Passed to Claude Code as --effort (default: Claude Code's own setting)." :
          `Up to ${prof.rounds} tool rounds per turn · rules-lawyer reviews ${prof.lawyer === "all" ? "every rules event" : "judgement calls (routine rolls the engine computed pass)"} · temperature ${prof.temperature}${prof.review ? " · a final self-review pass" : ""}.`}</span></label>
      ${local ? `
      <div class="grid2">
        <label>Server URL <input id="st-url" value="${esc(sub.url || "")}" autocomplete="off"></label>
        <label>Context window (tokens) <input id="st-ctx" type="number" min="2048" step="1024" value="${esc(sub.num_ctx || 8192)}"></label>
        <label>Tool calling <select id="st-tools"><option value="native" ${sub.tool_mode !== "text" ? "selected" : ""}>native (the server's tool API)</option><option value="text" ${sub.tool_mode === "text" ? "selected" : ""}>text protocol (&lt;dm&gt;…&lt;/dm&gt;)</option></select></label>
        ${eng === "openai" ? `<label>API key <input id="st-key" type="password" value="${esc(sub.api_key || "")}" autocomplete="off"></label>` : ""}
      </div>
      <p class="hint">Local engines run RoboDM's own DM loop: the same ./dm tools, rules gate, receipt check, transcript and a local rules-lawyer pass. Small models (8B) follow instructions less reliably than Claude; RoboDM catches invented dice and contradictions, but expect a weaker DM. For Ollama, set the context window to what your GPU fits (12k–16k for an 8B model on 8 GB).</p>` : ""}
      <div class="actions">${local ? '<button type="button" class="ghost" id="st-test">Test connection</button>' : ""}<button type="button" id="st-save">Save</button></div>
    </div>
    ${agentCard(agentStatus)}
    <div class="card settings">
      <h3>🎲 Table <span class="meta">(this campaign)</span></h3>
      ${tc}
      ${s.campaign ? '<div class="actions"><button type="button" id="st-save-table">Save table settings</button></div>' : ""}
    </div></div>`;
}

function readEngineInputs() {
  const eng = draft.engine, sub = draft[eng];
  const m = $("#st-model"); if (m) sub.model = m.value.trim();
  const u = $("#st-url"); if (u) sub.url = u.value.trim();
  const c = $("#st-ctx"); if (c) sub.num_ctx = parseInt(c.value, 10) || sub.num_ctx;
  const t = $("#st-tools"); if (t) sub.tool_mode = t.value;
  const k = $("#st-key"); if (k) sub.api_key = k.value;
  const mine = agentDraft(eng);
  for (const inp of document.querySelectorAll("[data-agent-model]")) {
    const r = inp.dataset.agentModel;
    mine[r] = Object.assign({ model: "", effort: "" }, mine[r], { model: inp.value.trim() });
  }
}

function appliedLine(s) {
  return s.applied && s.applied.length ? `<span class="ok-line">✔ ${esc(s.applied.join(" · "))}</span>` : '<span class="ok-line">✔ Subagents saved.</span>';
}

async function settingsClick(e) {
  const t = e.target.closest("button");
  if (!t) return false;
  if (t.dataset.engine) {
    readEngineInputs(); draft.engine = t.dataset.engine;
    if (draft.engine !== "claude" && !draft.effort) draft.effort = "medium";
    if (draft.engine !== "claude" && !engineModels[draft.engine]) {
      try { const r = await api(`/api/settings/models?engine=${draft.engine}`); engineModels[draft.engine] = r.models; } catch { /* listed on test */ }
    }
    drawSettings(); return true;
  }
  if (t.dataset.effort !== undefined) { readEngineInputs(); draft.effort = t.dataset.effort; drawSettings(); return true; }
  if (t.dataset.agentEffort !== undefined) {
    readEngineInputs();
    const mine = agentDraft(draft.engine), r = t.dataset.agent;
    mine[r] = Object.assign({ model: "", effort: "" }, mine[r], { effort: t.dataset.agentEffort });
    drawSettings(); return true;
  }
  if (t.id === "st-save-agents") {
    readEngineInputs();
    const eng = draft.engine;
    try {
      settings = await api("/api/settings", { engine: { agents: { [eng]: agentDraft(eng) } } });
      const keep = draft.engine === settings.engine.engine ? null : draft.engine;
      const engDraft = keep ? draft[keep] : null;
      draft = Object.assign(JSON.parse(JSON.stringify(settings.engine)), keep ? { engine: keep, [keep]: engDraft } : {});
      drawSettings(undefined, appliedLine(settings));
    } catch (err) { drawSettings(undefined, `<span class="warn-line">✘ ${esc(err.message)}</span>`); }
    return true;
  }
  const campKey = Object.keys(t.dataset).find(k => k.startsWith("camp"));
  if (campKey) {
    readEngineInputs();
    const key = campKey.replace(/^camp/, "").replace(/^./, c => c.toLowerCase()).replace(/[A-Z]/g, c => "_" + c.toLowerCase());
    const raw = t.dataset[campKey];
    tableDraft[key] = raw === "true" ? true : raw === "false" ? false : raw;
    drawSettings(); return true;
  }
  if (t.id === "st-test") {
    readEngineInputs();
    const eng = draft.engine;
    const r = await api(`/api/settings/models?engine=${eng}&url=${encodeURIComponent(draft[eng].url || "")}`);
    engineModels[eng] = r.models;
    drawSettings(r.ok ? `<span class="ok-line">✔ Connected: ${r.models.length} model(s)${r.models.length ? " — " + esc(r.models.slice(0, 6).join(", ")) : ""}</span>`
      : `<span class="warn-line">✘ ${esc(r.error)}</span>`);
    return true;
  }
  if (t.id === "st-save") {
    readEngineInputs();
    const eng = draft.engine;
    const patch = { engine: eng, effort: draft.effort || "", [eng]: draft[eng], agents: { [eng]: agentDraft(eng) } };
    try {
      settings = await api("/api/settings", { engine: patch });
      draft = JSON.parse(JSON.stringify(settings.engine));
      engineModels[settings.engine.engine] = settings.models;
      drawSettings(undefined, settings.applied ? appliedLine(settings) : undefined);
      bubble("system", `DM engine: **${settings.engines[eng]}**${draft[eng].model ? " · " + draft[eng].model : ""}${draft.effort ? " · effort " + draft.effort : ""}.`);
    } catch (err) { drawSettings(`<span class="warn-line">✘ ${esc(err.message)}</span>`); }
    loadState();
    return true;
  }
  if (t.id === "st-save-table") {
    readEngineInputs();
    const changes = {};
    for (const [k, v] of Object.entries(tableDraft)) if (settings.campaign[k] !== v) changes[k] = v;
    if (!Object.keys(changes).length) { drawSettings('<span class="meta">Nothing changed.</span>'); return true; }
    try {
      settings = await api("/api/settings", { campaign: changes });
      tableDraft = Object.assign({}, settings.campaign);
      drawSettings(settings.problems ? `<span class="warn-line">✘ ${esc(settings.problems.join("; "))}</span>` : '<span class="ok-line">✔ Table settings saved.</span>');
    } catch (err) { drawSettings(`<span class="warn-line">✘ ${esc(err.message)}</span>`); }
    loadState();
    return true;
  }
  return false;
}

// ------------------------------------------------------------------ dice
async function roll(expr) {
  try {
    const r = await api("/api/roll", { expr, adv: $("#dice-adv").checked, dis: $("#dice-dis").checked });
    const row = document.createElement("div");
    row.className = "roll";
    const txt = r.text.replace(/^🎲\s*/, "");
    row.innerHTML = `<span>🎲 ${inline(txt)}</span><button type="button" class="ghost small" title="Tell the DM">→ chat</button>`;
    $("button", row).onclick = () => { const i = $("#chat-input"); i.value = (i.value ? i.value + " " : "") + `(I rolled ${txt.replace(/\*\*/g, "")})`; i.focus(); };
    $("#dice-log").prepend(row);
  } catch (e) { bubble("error", e.message); }
}

// ------------------------------------------------------------------ wiring
function wire() {
  $("#chat-form").addEventListener("submit", e => { e.preventDefault(); sendChat($("#chat-input").value); });
  $("#chat-input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(e.target.value); } });
  $("#chat-cancel").onclick = () => api("/api/chat/cancel", {}).catch(() => {});
  $("#rewind").onclick = rewind;
  $("#round-start").onclick = async () => { await api("/api/round", { action: "start" }); loadState(); };
  $("#chat-reset").onclick = async () => {
    if (!confirm("Start a new conversation with the DM? The campaign itself is unaffected; the DM reloads it from the files.")) return;
    await api("/api/chat/reset", {}); bubble("system", "_New conversation. The DM will reload the campaign on your next message._");
  };
  $("#as-select").onchange = e => store.set("robodm-as", e.target.value);
  $("#view-toggle").onclick = () => {
    if (view === "player" && !confirm("DM view shows secrets: lore DM secrets, companions' private thoughts, hidden monster HP. Switch?")) return;
    view = view === "dm" ? "player" : "dm"; store.set("robodm-view", view); loadState().then(renderPanel);
  };
  document.addEventListener("click", e => {
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); openPanel(o.dataset.open); }
  });
  $("#drawer-close").onclick = closePanel;
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!$("#dice-pop").classList.contains("hidden")) { $("#dice-pop").classList.add("hidden"); return; }
    if (panel) closePanel();
  });
  $("#dice-btn").onclick = () => {
    const pop = $("#dice-pop"); pop.classList.toggle("hidden");
    $("#dice-btn").setAttribute("aria-expanded", String(!pop.classList.contains("hidden")));
  };
  $$(".die").forEach(b => b.onclick = () => { const x = "1" + b.dataset.die; $("#dice-expr").value = x; roll(x); });
  $("#dice-roll").onclick = () => roll($("#dice-expr").value || "1d20");
  $("#dice-expr").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); roll(e.target.value || "1d20"); } });
  $("#round-bar").addEventListener("click", async e => {
    if (e.target.id === "round-send") { try { await api("/api/round", { action: "send" }); } catch (err) { bubble("error", err.message); } loadState(); }
    if (e.target.id === "round-stop") { await api("/api/round", { action: "stop" }); loadState(); }
  });
  $("#drawer-tabs").addEventListener("click", e => {
    const b = e.target.closest("button[data-codex]"); if (!b) return;
    codexTab = b.dataset.codex; store.set("robodm-codex", codexTab); loreOpen = null; personOpen = null; renderPanel();
  });
  const body = $("#drawer-body");
  body.addEventListener("click", async e => {
    if (panel === "settings" && await settingsClick(e)) return;
    const say = e.target.closest("[data-say]");
    if (say) { const i = $("#chat-input"); i.value = say.dataset.say; i.focus(); return; }
    const l = e.target.closest("[data-lore]");
    if (l) { loreOpen = l.dataset.lore; renderLore(); return; }
    if (e.target.id === "lore-back") { loreOpen = null; renderPanel(); return; }
    const pr = e.target.closest("[data-person]");
    if (pr) { personOpen = pr.dataset.person; renderPeople(); return; }
    if (e.target.id === "people-back") { personOpen = null; renderPanel(); return; }
    if (e.target.id === "add-phrasing") { phrasings = $$("#recall-form input").map(i => i.value).concat(""); renderPanel(); return; }
    if (e.target.id === "seats-open") { showSeats(); return; }
    const cp = e.target.closest("[data-copy]");
    if (cp) { try { await navigator.clipboard.writeText(cp.dataset.copy); cp.textContent = "Copied"; } catch { /* select instead */ } return; }
    if (movePreview && ["move-ask", "move-now", "move-cancel"].includes(e.target.id)) {
      const { who, to } = movePreview;
      if (e.target.id === "move-ask") { closeMove(); sendChat(`I move to ${to}`); }
      if (e.target.id === "move-now") {
        try { const r = await api("/api/command", { argv: ["combat", "move", who, to] }); if (!r.ok) bubble("error", (r.err || r.out).replace(/^Error: /, "")); }
        catch (err) { bubble("error", err.message); }
        closeMove();
      }
      if (e.target.id === "move-cancel") closeMove();
      return;
    }
    if (e.target.id === "forge-open") {
      forgeOpen = true; levelOpen = null; renderPanel();
      if (!forgeOpts) { try { forgeOpts = await api("/api/forge"); } catch (err) { forgeOpts = null; } renderPanel(); }
      return;
    }
    if (e.target.id === "fg-close") { forgeOpen = false; renderPanel(); return; }
    if (e.target.id === "fg-roll") {
      if (!$("#fg-name").value.trim()) { $("#fg-out").textContent = "Name the character first."; return; }
      $("#fg-method").value = "roll";
      await runInto($("#fg-out"), ["pc", "build", $("#fg-name").value.trim(), "--scores", "roll"]); return;
    }
    if (e.target.id === "fg-preview") { await runInto($("#fg-out"), forgeArgv(true)); return; }
    if (e.target.id === "fg-create") {
      if (await runInto($("#fg-out"), forgeArgv(false))) {
        const text = $("#fg-out").innerText; forgeOpen = false; await loadState(); renderPanel(); bubble("system", text);
      }
      return;
    }
    const lv = e.target.closest("[data-level]");
    if (lv) { levelOpen = lv.dataset.level; forgeOpen = false; renderPanel(); await runInto($("#lv-out"), levelArgv(true)); return; }
    if (e.target.id === "lv-close") { levelOpen = null; renderPanel(); return; }
    if (e.target.id === "lv-preview") { await runInto($("#lv-out"), levelArgv(true)); return; }
    if (e.target.id === "lv-apply") {
      if (await runInto($("#lv-out"), levelArgv(false))) {
        const text = $("#lv-out").innerText; levelOpen = null; await loadState(); renderPanel(); bubble("system", text);
      }
    }
  });
  body.addEventListener("change", e => { if (e.target.closest("#forge")) forgeRefresh(); });
  body.addEventListener("input", e => { if (e.target.closest(".abil-in")) forgeRefresh(); });
  body.addEventListener("submit", async e => {
    e.preventDefault();
    if (e.target.id === "recall-form") {
      phrasings = $$("#recall-form input").map(i => i.value);
      const qs = phrasings.filter(Boolean).map(p => "q=" + encodeURIComponent(p)).join("&");
      if (!qs) return;
      const { hits } = await api(withView("/api/recall?" + qs));
      $("#recall-hits").innerHTML = hits.map(h => `<div class="hit"><span class="kind">${esc(h.kind)}</span>${inline(h.show)}${h.snippet ? `<div class="snip">… ${inline(h.snippet)}</div>` : ""}</div>`).join("") || '<p class="empty">Nothing recorded matches. Try other words.</p>';
    } else if (e.target.id === "rules-form") {
      const q = $("#rules-q").value.trim(); if (!q) return;
      const { text } = await api("/api/rules?q=" + encodeURIComponent(q));
      const out = $("#rules-out"); out.classList.remove("hidden"); out.innerHTML = md(text);
    }
  });
}

function connect() {
  const es = new EventSource("/api/events");
  const refresh = debounce(() => { mapStamp = Date.now(); loadState(); }, 250);
  es.addEventListener("changed", refresh);
  es.addEventListener("chat", e => onChat(JSON.parse(e.data)));
  es.addEventListener("whisper", () => loadState());
  es.addEventListener("hello", () => loadState());
}

wire();
loadState().then(() => {
  if (panel && PANELS[panel]) { document.body.classList.add("drawer-open"); renderPanel(); renderHeader(); } else panel = "";
  loadHistory();
});
connect();
