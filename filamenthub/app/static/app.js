"use strict";
// Spool Sync – mobile web UI (no build step, no dependencies).

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtG = (g) => `${Math.round(Number(g) || 0).toLocaleString("da-DK")} g`;
const fmtG1 = (g) => `${(Number(g) || 0).toLocaleString("da-DK", { maximumFractionDigits: 1 })} g`;
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString("da-DK", { dateStyle: "short", timeStyle: "short" }) : "–";
const fmtIso = (iso) => iso ? new Date(iso).toLocaleString("da-DK", { dateStyle: "short", timeStyle: "short" }) : "–";

const S = { tab: "printer", status: null, spools: null, meta: null, history: null, timer: null, spoolFilter: "", spoolMaterial: "", showArchived: false };

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (init.method !== "GET") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body || {});
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (res.status === 401 && !path.startsWith("/api/auth")) { showLogin(); throw new Error("Log ind"); }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => { const f = (d.loc || []).slice(1).join("."); return f ? `${f}: ${d.msg}` : d.msg; }).join(", ");
    throw new Error(msg || `Fejl ${res.status}`);
  }
  return data;
}

function toast(msg, isErr = false) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), isErr ? 5000 : 2500);
}

async function run(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
}

// ------------------------------------------------------------------ helpers
function swatch(color, colors, cls = "") {
  const list = (colors && colors.length > 1 ? colors : [color]).filter(Boolean);
  if (!list.length) return `<span class="swatch ${cls}"></span>`;
  const bg = list.length === 1 ? `#${list[0]}` : `conic-gradient(${list.map((c, i) => `#${c} ${i * 100 / list.length}% ${(i + 1) * 100 / list.length}%`).join(",")})`;
  return `<span class="swatch ${cls}"><i style="background:${esc(bg)}"></i></span>`;
}
function bar(sp) {
  const pct = sp.initial > 0 ? Math.max(0, Math.min(100, (sp.remaining / sp.initial) * 100)) : 0;
  const cls = sp.remaining <= 0 ? "empty" : pct < 15 ? "low" : "";
  return `<div class="bar ${cls}"><i style="width:${pct.toFixed(1)}%"></i></div>`;
}
const spoolTitle = (sp) => `${esc(sp.vendor)} ${esc(sp.material)} · ${esc(sp.name)}`;
function sheet(html, onMount) {
  const root = $("#sheet-root");
  root.innerHTML = `<div class="sheet-bg"><div class="sheet"><div class="handle"></div>${html}</div></div>`;
  const bg = $(".sheet-bg", root);
  bg.addEventListener("click", (e) => { if (e.target === bg) closeSheet(); });
  if (onMount) onMount($(".sheet", root));
}
const closeSheet = () => { $("#sheet-root").innerHTML = ""; };

// ------------------------------------------------------------------ auth
async function boot() {
  try {
    const st = await api("/api/auth/state");
    if (st.authenticated) return startApp();
    showLogin(st);
  } catch (e) {
    showLogin({ password_set: true });
  }
}

function showLogin(st) {
  stopPolling();
  $("#shell").classList.add("hidden");
  $("#login").classList.remove("hidden");
  const setup = st && st.password_set === false;
  $("#login-pw2").classList.toggle("hidden", !setup);
  $("#login-btn").textContent = setup ? "Opret adgangskode" : "Log ind";
  $("#login-text").textContent = setup
    ? (st.setup_allowed ? "Første gang: vælg en adgangskode til appen (mindst 6 tegn)." : "Opsætning skal ske fra dit hjemmenetværk første gang.")
    : "Log ind for at se dine spoler.";
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const pw = $("#login-pw").value;
    const err = $("#login-err");
    err.classList.add("hidden");
    try {
      if (setup) {
        if (pw !== $("#login-pw2").value) throw new Error("Adgangskoderne er ikke ens");
        await api("/api/auth/setup", { method: "POST", body: { password: pw } });
      } else {
        await api("/api/auth/login", { method: "POST", body: { password: pw } });
      }
      $("#login-pw").value = ""; $("#login-pw2").value = "";
      startApp();
    } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  };
}

function startApp() {
  $("#login").classList.add("hidden");
  $("#shell").classList.remove("hidden");
  document.querySelectorAll("nav button").forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });
  if (!openPickFromHash()) switchTab(location.hash.slice(1) || "printer");
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

function openPickFromHash() {
  // Link from a phone notification: #pick=<slot> opens the spool picker for that slot.
  const pick = location.hash.startsWith("#pick=") ? decodeURIComponent(location.hash.slice(6)) : null;
  if (!pick) return false;
  switchTab("printer");
  const t = setInterval(() => { if (S.status) { clearInterval(t); pickSpool(pick); } }, 300);
  setTimeout(() => clearInterval(t), 15000);
  return true;
}
// ... also when the link is opened in the app while it is already running.
window.addEventListener("hashchange", () => { if (S.timer) openPickFromHash(); });

function switchTab(tab) {
  if (!["printer", "spools", "history", "stats", "settings"].includes(tab)) tab = "printer";
  S.tab = tab;
  history.replaceState(null, "", `#${tab}`);
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("#title").textContent = { printer: "Printer", spools: "Spoler", history: "Historik", stats: "Statistik", settings: "Indstillinger" }[tab];
  $("#view").innerHTML = `<p class="muted">Henter…</p>`;
  refresh(true);
  startPolling();
}

function startPolling() {
  stopPolling();
  S.timer = setInterval(() => { if (!document.hidden && !$("#sheet-root").innerHTML) refresh(false); }, 5000);
}
function stopPolling() { if (S.timer) clearInterval(S.timer); S.timer = null; }
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.timer) refresh(false); });

let viewBroken = false;   // the view shows an error instead of the tab: the next good refresh draws the tab again

async function refresh(full) {
  if (viewBroken) full = true;
  try {
    S.status = await api("/api/status");
    renderHeader();
    if (S.tab === "printer") renderPrinter();
    if (S.tab === "spools" && (full || !S.spools)) { await loadSpools(); renderSpools(); }
    if (S.tab === "history" && full) { S.history = await api("/api/history"); renderHistory(); }
    if (S.tab === "stats" && full) { S.stats = await api("/api/stats"); renderStats(); }
    if (S.tab === "settings" && full) renderSettings();
    viewBroken = false;
  } catch (e) {
    if (e.message === "Log ind") return;
    const msg = e instanceof TypeError ? "Ingen forbindelse til Spool Sync – prøver igen" : e.message;
    if (full || !S.status) {
      viewBroken = true;
      $("#view").innerHTML = `<div class="alert err">${esc(msg)}</div>`;
    } else {
      // A background poll failed (phone waking up, Wi-Fi blip, restart): keep the page and what is typed in it.
      const dot = $("#conn-dot");
      dot.className = "dot bad";
      dot.title = msg;
    }
  }
}

async function loadSpools() {
  S.spools = await api(`/api/spools${S.showArchived ? "?archived=true" : ""}`);
}

function renderHeader() {
  const st = S.status;
  const dot = $("#conn-dot");
  const m = st.bambu.mqtt;
  dot.className = "dot " + (m === "connected" ? "ok" : ["auth_failed", "not_logged_in", "error"].includes(m) ? "bad" : "wait");
  dot.title = { connected: "Forbundet til printeren", not_logged_in: "Ikke logget ind på Bambu", auth_failed: "Bambu-login udløbet", no_device: "Vælg printer" }[m] || m;
  const p = st.print;
  const chip = $("#print-chip");
  if (p && ["RUNNING", "PAUSE", "PREPARE"].includes(p.state)) {
    chip.textContent = `${p.state === "PAUSE" ? "Pause" : "Printer"} ${p.percent}%`;
    chip.classList.remove("hidden");
  } else chip.classList.add("hidden");
  const badge = $("#pending-badge");
  badge.textContent = st.pending;
  badge.classList.toggle("hidden", !st.pending);
}

// ------------------------------------------------------------------ printer tab
const STATE_TXT = { IDLE: "Klar", PREPARE: "Forbereder", RUNNING: "Printer", PAUSE: "Pause", FINISH: "Færdig", FAILED: "Afbrudt", SLICING: "Slicer", UNKNOWN: "Ukendt" };

function renderPrinter() {
  const st = S.status;
  let html = "";
  if (!st.bambu.logged_in) {
    html += `<div class="card"><h2>Kom i gang</h2><p>Log ind med din Bambu Lab-konto, så appen kan se din printer og dit forbrug.</p>
      <button class="btn primary" onclick="switchTab('settings')">Gå til Indstillinger</button></div>`;
  } else if (!st.device) {
    html += `<div class="card"><h2>Vælg printer</h2><button class="btn primary" onclick="switchTab('settings')">Vælg printer</button></div>`;
  } else if (st.bambu.mqtt === "auth_failed") {
    html += `<div class="alert err">Bambu-login er udløbet. <a href="#settings" onclick="switchTab('settings')">Log ind igen</a> – forbrug hentes automatisk bagefter.</div>`;
  }
  if (!st.spoolman_ok) html += `<div class="alert err">Spoolman svarer ikke: ${esc(st.spoolman_error || "")}</div>`;
  if (st.bambu.cloud_error) html += `<div class="alert warn">${esc(st.bambu.cloud_error)}</div>`;
  if (st.pending) html += `<div class="alert warn">${st.pending} fratrækning(er) venter på dig. <a href="#history" onclick="switchTab('history')">Se historik</a></div>`;

  const p = st.print;
  if (st.device) {
    html += `<div class="card"><div class="row between"><h2 class="grow">${esc(st.device.name)}</h2>
      <span class="state">${esc(p ? (STATE_TXT[p.state] || p.state) : (st.bambu.mqtt === "connected" ? "Venter på data" : "Ikke forbundet"))}</span></div>`;
    if (p && p.name) html += `<div class="small">${esc(p.name)}</div>`;
    if (p && ["RUNNING", "PAUSE", "PREPARE"].includes(p.state)) {
      html += `<div class="progress"><i style="width:${p.percent}%"></i></div>
        <div class="row between tiny muted"><span>${p.percent}% · lag ${p.layer}/${p.total_layers}</span><span>${p.remaining_min} min tilbage</span></div>`;
    }
    html += `</div>`;
  }

  const groups = {};
  for (const s of st.slots) (groups[s.kind === "ext" ? "ext" : `u${s.unit}`] ||= []).push(s);
  const byId = Object.fromEntries((S.spools || []).map((s) => [s.id, s]));
  for (const [gk, slots] of Object.entries(groups)) {
    const first = slots[0];
    const title = gk === "ext" ? "Ekstern spoleholder" : `${esc(first.unit_model)} ${first.kind === "ht" ? "" : String.fromCharCode(65 + first.unit)}`;
    html += `<div class="card"><h3>${title}</h3>`;
    for (const s of slots) html += slotHtml(s, byId);
    html += `</div>`;
  }
  if (st.device && !st.slots.length && st.bambu.mqtt === "connected") html += `<p class="muted">Venter på AMS-data fra printeren…</p>`;
  for (const o of st.orphans || []) {
    html += `<div class="alert info">${esc(o.label)} rapporteres ikke af printeren lige nu, men har spole #${o.spool ? esc(o.spool.id) : "?"} tildelt.
      <button class="btn small" data-unassign="${esc(o.slot)}">Fjern</button></div>`;
  }
  $("#view").innerHTML = html;
  bindSlotButtons();
}

function slotHtml(s, byId) {
  const a = s.assignment;
  const sp = a && a.spool;
  let body = "";
  if (!s.present && s.kind !== "ext") {
    body = `<div class="muted">Tom</div>`;
  } else if (!s.present && !sp) {
    body = `<div class="muted small">Ikke i brug. Vælg en spole her, hvis du printer fra den eksterne holder.</div>`;
  } else if (sp && sp.unavailable) {
    body = `<div><b>Spole #${esc(sp.id)}</b></div><div class="small muted">Detaljer utilgængelige (Spoolman svarer ikke)</div>`;
  } else if (sp && !sp.missing) {
    body = `<div class="row between"><div class="grow"><div><b>${spoolTitle(sp)}</b></div>
      <div class="small muted">Spole #${sp.id} · ${fmtG(sp.remaining)} tilbage</div></div></div>${bar(sp)}`;
  } else if (sp && sp.missing) {
    body = `<div class="alert warn">Spole #${esc(sp.id)} findes ikke i Spoolman længere.</div>`;
  } else {
    body = `<div><b>${esc(s.material || "Ukendt materiale")}</b> <span class="muted small">${esc(s.sub_brand)}</span></div>
      <div class="small muted">Ingen spole valgt – forbrug gemmes til du vælger</div>`;
    if (s.suggestions.length && S.status && S.spools) {
      body += `<div class="row wrap" style="margin-top:6px">${s.suggestions.map((id) => byId[id]).filter(Boolean).map((x) =>
        `<span class="chip" data-quick="${esc(s.key)}" data-spool="${x.id}">${swatch(x.color, x.colors, "sm")} #${x.id} ${esc(x.name)} · ${fmtG(x.remaining)}</span>`).join("")}</div>`;
    }
  }
  if (s.unknown_rfid) {
    body += `<div class="alert info small">Bambu-spole med RFID genkendt${s.remain >= 0 ? ` (${s.remain}% tilbage)` : ""}.
      <div class="row wrap" style="margin-top:6px"><button class="btn small primary" data-create="${esc(s.key)}">Opret i lager</button>
      <button class="btn small" data-pick="${esc(s.key)}">Knyt til eksisterende</button></div></div>`;
  }
  for (const m of (s.mismatch || [])) body += `<div class="alert warn small">Tjek: ${esc(m)}</div>`;
  const canPick = s.present || s.kind === "ext";
  const btns = `${canPick ? `<button class="btn small" data-pick="${esc(s.key)}">${sp ? "Skift" : "Vælg"}</button>` : ""}
    ${sp ? `<button class="btn small danger" data-unassign="${esc(s.key)}">Fjern</button>` : ""}`;
  return `<div class="slot">${swatch(s.color, s.colors)}
    <div class="grow stack"><div class="row"><span class="label">${esc(s.label)}</span>
      ${s.present && s.material ? `<span class="small muted">${esc(s.material)}</span>` : ""}
      ${s.active ? `<span class="active-tag">● I brug</span>` : ""}</div>${body}
      <div class="row wrap">${btns}</div></div></div>`;
}

function bindSlotButtons() {
  const v = $("#view");
  v.querySelectorAll("[data-pick]").forEach((b) => b.onclick = () => pickSpool(b.dataset.pick));
  v.querySelectorAll("[data-unassign]").forEach((b) => b.onclick = () => run(b, async () => {
    await api(`/api/slots/${encodeURIComponent(b.dataset.unassign)}/unassign`, { method: "POST" });
    toast("Spole fjernet fra slot"); refresh(false);
  }));
  v.querySelectorAll("[data-quick]").forEach((b) => b.onclick = () => run(null, async () => {
    await api(`/api/slots/${encodeURIComponent(b.dataset.quick)}/assign`, { method: "POST", body: { spool_id: Number(b.dataset.spool) } });
    toast("Spole valgt"); refresh(false);
  }));
  v.querySelectorAll("[data-create]").forEach((b) => b.onclick = () => run(b, async () => {
    await api(`/api/slots/${encodeURIComponent(b.dataset.create)}/create-from-tray`, { method: "POST" });
    S.spools = null; toast("Spole oprettet og valgt"); await loadSpools(); refresh(false);
  }));
  if (!S.spools) loadSpools().then(() => { if (S.tab === "printer") renderPrinter(); }).catch(() => {});
}

async function pickSpool(slotKey, deductionId = null, grams = null, preferredSpool = null) {
  if (!S.spools) await loadSpools();
  const st = S.status;
  const slot = st.slots.find((s) => s.key === slotKey);
  const assignedTo = {};
  for (const s of st.slots) if (s.assignment) assignedTo[s.assignment.spool.id] = s.label;
  const sugg = new Set(slot && !deductionId ? slot.suggestions : []);
  if (preferredSpool) sugg.add(preferredSpool);
  const title = deductionId ? "Vælg spole at trække fra" : `Vælg spole til ${esc(slot ? slot.label : slotKey)}`;
  sheet(`<h2>${title}</h2>
    ${deductionId ? `<label>Forbrug i gram${grams === "" ? " (se Bambu Studio)" : ""}</label>
      <input id="pick-grams" type="number" inputmode="decimal" step="0.1" min="0" value="${esc(grams)}" placeholder="fx 42,5">
      ${grams === "" ? `<label>… eller vej spolen nu: vægt på vægten i gram (spole + filament)</label>
      <input id="pick-gross" type="number" inputmode="decimal" step="1" min="0" placeholder="fx 1012">` : ""}
      <label>Tryk på spolen der skal trækkes fra</label>` : ""}
    <input id="pick-q" type="search" placeholder="Søg (mærke, farve, materiale, #nr)">
    <div id="pick-list" style="margin-top:8px"></div>
    <div class="row" style="margin-top:12px"><button class="btn grow" id="pick-close">Luk</button>
    ${deductionId ? "" : `<button class="btn primary grow" id="pick-new">+ Ny spole</button>`}</div>`, (el) => {
    const draw = () => {
      const q = $("#pick-q", el).value.trim().toLowerCase();
      let list = S.spools.filter((x) => !x.archived);
      if (q) list = list.filter((x) => `${x.vendor} ${x.material} ${x.name} #${x.id} ${x.location}`.toLowerCase().includes(q));
      list.sort((a, b) => (sugg.has(b.id) - sugg.has(a.id)) || (!!assignedTo[a.id] - !!assignedTo[b.id]) ||
        (slot && slot.material ? ((b.material.toUpperCase() === slot.material.toUpperCase()) - (a.material.toUpperCase() === slot.material.toUpperCase())) : 0) ||
        a.material.localeCompare(b.material) || a.name.localeCompare(b.name));
      $("#pick-list", el).innerHTML = list.map((x) => `<div class="list-item" data-id="${x.id}">${swatch(x.color, x.colors)}
        <div class="grow"><div><b>${spoolTitle(x)}</b> ${sugg.has(x.id) ? `<span class="chip on">Forslag</span>` : ""}</div>
        <div class="small muted">#${x.id} · ${fmtG(x.remaining)}${assignedTo[x.id] ? ` · sidder i ${esc(assignedTo[x.id])}` : ""}</div>${bar(x)}</div></div>`).join("")
        || `<p class="muted">Ingen spoler matcher.</p>`;
      el.querySelectorAll(".list-item").forEach((it) => it.onclick = () => run(null, async () => {
        const id = Number(it.dataset.id);
        if (deductionId) {
          const g = Number(String($("#pick-grams", el).value).replace(",", "."));
          const grossEl = $("#pick-gross", el);
          const gross = grossEl && grossEl.value !== "" ? Number(String(grossEl.value).replace(",", ".")) : null;
          if (!(g > 0) && !(gross >= 0 && gross !== null)) {
            $("#pick-grams", el).focus(); throw new Error("Indtast forbruget i gram, eller vægten på vægten");
          }
          const body = gross !== null && !(g > 0) ? { spool_id: id, gross_weight: gross } : { spool_id: id, grams: g };
          try {
            await api(`/api/deductions/${deductionId}/resolve`, { method: "POST", body });
          } catch (e) {
            if (!/kun .* g tilbage|vejet efter printet/.test(e.message) || !confirm(`${e.message}\n\nTræk fra alligevel?`)) throw e;
            await api(`/api/deductions/${deductionId}/resolve`, { method: "POST", body: { ...body, force: true } });
          }
          toast(body.grams ? `${fmtG1(g)} trukket fra #${id}` : `Spole #${id} vejet – forbruget er trukket fra`);
          S.history = await api("/api/history"); renderHistory();
        } else {
          try {
            await api(`/api/slots/${encodeURIComponent(slotKey)}/assign`, { method: "POST", body: { spool_id: id } });
          } catch (e) {
            if (!/RFID/.test(e.message) || !confirm(`${e.message}. Overskriv RFID-tilknytningen?`)) throw e;
            await api(`/api/slots/${encodeURIComponent(slotKey)}/assign`, { method: "POST", body: { spool_id: id, force: true } });
          }
          toast("Spole valgt");
        }
        closeSheet(); await loadSpools(); refresh(false);
      }));
    };
    $("#pick-q", el).oninput = draw;
    $("#pick-close", el).onclick = closeSheet;
    const nb = $("#pick-new", el);
    if (nb) nb.onclick = () => newSpoolSheet(slotKey, slot);
    draw();
  });
}

// ------------------------------------------------------------------ spools tab
function renderSpools() {
  const list = S.spools || [];
  const materials = [...new Set(list.filter((x) => S.showArchived || !x.archived).map((x) => x.material))].sort();
  const q = S.spoolFilter.toLowerCase();
  const shown = list.filter((x) => (S.showArchived || !x.archived) && (!S.spoolMaterial || x.material === S.spoolMaterial)
    && (!q || `${x.vendor} ${x.material} ${x.name} #${x.id} ${x.location}`.toLowerCase().includes(q)));
  const total = shown.filter((x) => !x.archived).reduce((a, x) => a + x.remaining, 0);
  $("#view").innerHTML = `
    <div class="row" style="margin-bottom:10px"><input id="sp-q" type="search" placeholder="Søg" value="${esc(S.spoolFilter)}">
      <button class="btn" id="sp-scan" title="Scan stregkode eller Spoolman-label">Scan</button><button class="btn primary" id="sp-new">+ Ny</button></div>
    <div class="row wrap" style="margin-bottom:10px">
      <span class="chip ${S.spoolMaterial ? "" : "on"}" data-mat="">Alle</span>
      ${materials.map((m) => `<span class="chip ${S.spoolMaterial === m ? "on" : ""}" data-mat="${esc(m)}">${esc(m)}</span>`).join("")}
      <span class="chip ${S.showArchived ? "on" : ""}" id="sp-arch">Arkiverede</span></div>
    <div class="small muted" style="margin-bottom:6px">${shown.length} spoler · ${fmtG(total)} filament i alt</div>
    <div class="card">${shown.map((x) => `<div class="list-item" data-id="${x.id}" ${x.archived ? 'style="opacity:.55"' : ""}>${swatch(x.color, x.colors)}
      <div class="grow"><div><b>${spoolTitle(x)}</b></div>
      <div class="small muted">#${x.id} · ${fmtG(x.remaining)}${x.location ? ` · ${esc(x.location)}` : ""}${x.tag ? " · RFID" : ""}${x.archived ? " · arkiveret" : ""}</div>${bar(x)}</div></div>`).join("")
      || `<p class="muted">Ingen spoler.</p>`}</div>`;
  const qi = $("#sp-q");
  qi.oninput = () => { S.spoolFilter = qi.value; const pos = qi.selectionStart; renderSpools(); const n = $("#sp-q"); n.focus(); n.setSelectionRange(pos, pos); };
  $("#sp-new").onclick = () => newSpoolSheet(null, null);
  $("#sp-scan").onclick = () => scanForNewSpool(null, null);
  document.querySelectorAll("[data-mat]").forEach((c) => c.onclick = () => { S.spoolMaterial = c.dataset.mat; renderSpools(); });
  $("#sp-arch").onclick = async () => { S.showArchived = !S.showArchived; await loadSpools(); renderSpools(); };
  document.querySelectorAll("#view .list-item").forEach((it) => it.onclick = () => spoolSheet(Number(it.dataset.id)));
}

function spoolSheet(id) {
  const x = (S.spools || []).find((s) => s.id === id);
  if (!x) return;
  const slots = (S.status && S.status.slots) || [];
  const tare = x.spool_weight != null ? Number(x.spool_weight) : null;
  sheet(`<div class="row">${swatch(x.color, x.colors)}<h2 class="grow" style="margin:0">${spoolTitle(x)}</h2></div>
    <dl class="kv" style="margin:12px 0">
      <dt>Nummer</dt><dd>#${x.id}</dd>
      <dt>Tilbage</dt><dd><b>${fmtG1(x.remaining)}</b> af ${fmtG(x.initial)} (brugt ${fmtG1(x.used)})</dd>
      <dt>Tom spole vejer</dt><dd>${tare != null ? fmtG(tare) : "ukendt"}</dd>
      <dt>Placering</dt><dd>${esc(x.location || "–")}</dd>
      <dt>RFID</dt><dd>${x.tag ? `${esc(x.tag.slice(0, 8))}… <button class="btn small" id="sp-untag">Fjern</button>` : "–"}</dd>
      <dt>Sidst brugt</dt><dd>${x.last_used ? fmtIso(x.last_used) : "–"}</dd>
    </dl>
    ${x.comment ? `<p class="small">${esc(x.comment)}</p>` : ""}
    <div class="card"><h3>Ret vægt</h3>
      <label>Filament tilbage (gram)</label>
      <div class="row"><input id="sp-rem" type="number" step="1" min="0" value="${Math.round(x.remaining)}"><button class="btn primary" id="sp-rem-save">Gem</button></div>
      <label>…eller vej hele spolen på en vægt (brutto, gram)</label>
      <div class="row"><input id="sp-gross" type="number" step="1" min="0" placeholder="${tare != null ? `fx ${Math.round(x.remaining + tare)}` : "tom spolevægt ukendt"}" ${tare == null ? "disabled" : ""}>
      <button class="btn" id="sp-gross-save" ${tare == null ? "disabled" : ""}>Gem</button></div></div>
    <div class="card"><h3>Sæt i printer</h3>
      <div class="row"><select id="sp-slot"><option value="">Vælg slot…</option>${slots.filter((s) => s.present || s.kind === "ext").map((s) =>
        `<option value="${esc(s.key)}">${esc(s.label)} ${s.material ? `(${esc(s.material)})` : ""}${s.assignment ? ` – optaget af #${s.assignment.spool.id}` : ""}</option>`).join("")}</select>
      <button class="btn primary" id="sp-slot-save">Sæt i</button></div></div>
    <div class="row"><button class="btn grow" id="sp-close">Luk</button>
      <button class="btn grow ${x.archived ? "" : "danger"}" id="sp-arch-btn">${x.archived ? "Gendan" : "Arkivér (tom)"}</button></div>`, (el) => {
    const done = async (msg) => { toast(msg); closeSheet(); await loadSpools(); refresh(false); if (S.tab === "spools") renderSpools(); };
    $("#sp-close", el).onclick = closeSheet;
    $("#sp-rem-save", el).onclick = (e) => run(e.target, async () => {
      const raw = $("#sp-rem", el).value.trim();
      if (raw === "") throw new Error("Indtast vægten");
      if (Number(raw) === 0 && !confirm("Sæt spolen til 0 g (tom)?")) return;
      await api(`/api/spools/${id}`, { method: "PATCH", body: { remaining_weight: Number(raw) } }); await done("Vægt gemt");
    });
    $("#sp-gross-save", el).onclick = (e) => run(e.target, async () => {
      const g = Number($("#sp-gross", el).value);
      if (!g) throw new Error("Indtast vægt");
      await api(`/api/spools/${id}`, { method: "PATCH", body: { gross_weight: g } }); await done("Vægt gemt");
    });
    $("#sp-slot-save", el).onclick = (e) => run(e.target, async () => {
      const slot = $("#sp-slot", el).value;
      if (!slot) throw new Error("Vælg et slot");
      await api(`/api/slots/${encodeURIComponent(slot)}/assign`, { method: "POST", body: { spool_id: id } }); await done("Spole sat i");
    });
    $("#sp-arch-btn", el).onclick = (e) => run(e.target, async () => {
      if (!x.archived && !confirm("Arkivér spolen? Den fjernes også fra printerens slot.")) return;
      await api(`/api/spools/${id}`, { method: "PATCH", body: { archived: !x.archived } }); await done(x.archived ? "Gendannet" : "Arkiveret");
    });
    const ut = $("#sp-untag", el);
    if (ut) ut.onclick = (e) => run(e.target, async () => {
      await api(`/api/spools/${id}`, { method: "PATCH", body: { clear_tag: true } }); await done("RFID fjernet");
    });
  });
}

// ------------------------------------------------------------------ add spool: catalogue / barcode / manual
async function newSpoolSheet(slotKey, slot, barcode = null, vendorHint = null) {
  if (!S.meta) S.meta = await api("/api/meta");
  const f = { manufacturer: vendorHint || "", material: slot && slot.present && slot.material ? slot.material : "", q: "" };
  sheet(`<h2>Tilføj spole${slotKey ? ` til ${esc(slot ? slot.label : slotKey)}` : ""}</h2>
    ${barcode ? `<div class="alert info small">Stregkode <b>${esc(barcode)}</b>: vælg filamentet herunder, så huskes koden – næste gang udfyldes alt automatisk.</div>` : `<button class="btn primary block" id="a-scan">Scan stregkode</button>`}
    <p class="tiny muted center" style="margin:6px 0 10px">…eller find filamentet i databasen (SpoolmanDB – opdateres automatisk)</p>
    <input id="a-q" type="search" placeholder="Søg, fx “elegoo rapid petg black”" autocomplete="off">
    <div class="grid2"><div><label>Producent</label><input id="a-man" type="text" list="a-mans" placeholder="Alle" autocomplete="off" value="${esc(vendorHint || "")}"></div>
      <div><label>Materiale</label><select id="a-mat"><option value="">Alle</option></select></div></div>
    <datalist id="a-mans"></datalist>
    <div id="a-count" class="tiny muted" style="margin:8px 0">Henter…</div>
    <div id="a-list"></div>
    <div class="row" style="margin-top:12px"><button class="btn grow" id="a-cancel">Annullér</button>
      <button class="btn grow" id="a-manual">Ikke i listen – udfyld selv</button></div>`, async (el) => {
    $("#a-cancel", el).onclick = closeSheet;
    $("#a-manual", el).onclick = () => spoolForm(null, { slotKey, slot, barcode, source: "Manuelt" });
    if ($("#a-scan", el)) $("#a-scan", el).onclick = () => scanForNewSpool(slotKey, slot);
    try {
      if (!S.manufacturers) S.manufacturers = (await api("/api/catalog/manufacturers")).manufacturers;
      $("#a-mans", el).innerHTML = S.manufacturers.map((m) => `<option value="${esc(m.name)}">${m.mine ? "★ " : ""}${m.count} filamenter</option>`).join("");
    } catch (e) { /* search still works */ }
    let timer = null, seq = 0;
    const load = async () => {
      const my = ++seq;
      const qs = new URLSearchParams({ manufacturer: f.manufacturer, material: f.material, q: f.q, limit: "80" });
      let r;
      try { r = await api(`/api/catalog/search?${qs}`); } catch (e) { $("#a-count", el).textContent = e.message; return; }
      if (my !== seq || !document.body.contains(el)) return;
      const sel = $("#a-mat", el);
      sel.innerHTML = `<option value="">Alle</option>` + r.materials.map(([m, n]) => `<option value="${esc(m)}" ${m === f.material ? "selected" : ""}>${esc(m)} (${n})</option>`).join("")
        + (f.material && !r.materials.some(([m]) => m === f.material) ? `<option value="${esc(f.material)}" selected>${esc(f.material)}</option>` : "");
      $("#a-count", el).textContent = r.total ? `Viser ${r.items.length} af ${r.total.toLocaleString("da-DK")} filamenter` : "Ingen filamenter matcher – prøv færre ord, eller udfyld selv";
      $("#a-list", el).innerHTML = r.items.map((it, i) => `<div class="list-item" data-i="${i}">${swatch(it.color_hex, it.color_hexes)}
        <div class="grow"><div><b>${esc(it.manufacturer)} ${esc(it.material)} · ${esc(it.name)}</b> ${it.source === "mine" ? `<span class="chip on">Dine</span>` : ""}</div>
        <div class="small muted">${it.weight ? fmtG(it.weight) : "?"}${it.spool_weight != null ? ` · tom spole ${fmtG(it.spool_weight)}` : ""}${it.spool_type ? ` (${esc(it.spool_type === "cardboard" ? "pap" : it.spool_type === "plastic" ? "plast" : it.spool_type)})` : ""}${it.finish ? ` · ${esc(it.finish)}` : ""}</div></div></div>`).join("");
      el.querySelectorAll("#a-list .list-item").forEach((row) => row.onclick = () =>
        spoolForm(r.items[Number(row.dataset.i)], { slotKey, slot, barcode, source: r.items[Number(row.dataset.i)].source === "mine" ? "Dit filament" : "SpoolmanDB" }));
    };
    const later = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
    $("#a-q", el).oninput = (e) => { f.q = e.target.value; later(); };
    $("#a-man", el).onchange = (e) => { f.manufacturer = e.target.value.trim(); f.material = ""; load(); };
    $("#a-man", el).oninput = (e) => { if (!e.target.value) { f.manufacturer = ""; f.material = ""; later(); } };
    $("#a-mat", el).onchange = (e) => { f.material = e.target.value; load(); };
    load();
  });
}

// Filament + spool form. `item` is a catalogue item (or null for manual). opts: {slotKey, slot, source, barcode, notice}
function spoolForm(item, opts = {}) {
  const m = S.meta || { vendors: [], materials: [] };
  const it = item || {};
  const pre = !item && opts.slot && opts.slot.present ? { material: opts.slot.material, color_hex: opts.slot.color } : {};
  const v = (k, d = "") => (it[k] ?? pre[k] ?? d);
  const color = (v("color_hex") || (it.color_hexes || [])[0] || "808080").toUpperCase();
  sheet(`<h2>Ny spole${opts.slotKey ? ` til ${esc(opts.slot ? opts.slot.label : opts.slotKey)}` : ""}</h2>
    ${item ? `<div class="card row">${swatch(color, it.color_hexes)}<div class="grow"><b>${esc(it.manufacturer)} ${esc(it.material)} · ${esc(it.name)}</b>
      <div class="small muted">${esc(opts.source || "")}${it.extruder_temp ? ` · ${esc(it.extruder_temp)} °C / ${esc(it.bed_temp || "?")} °C` : ""}</div></div></div>` : ""}
    ${opts.notice ? `<div class="alert info small">${opts.notice}</div>` : ""}
    <details ${item ? "" : "open"} class="card"><summary>Filament-detaljer ${item ? "(udfyldt – ret hvis nødvendigt)" : ""}</summary>
      <div class="grid2">
        <div><label>Producent</label><input id="n-vendor" type="text" list="n-vendors" value="${esc(v("manufacturer"))}"></div>
        <div><label>Materiale</label><input id="n-mat" type="text" list="n-mats" value="${esc(v("material"))}"></div>
      </div>
      <datalist id="n-vendors">${m.vendors.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
      <datalist id="n-mats">${m.materials.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
      <label>Farve / navn</label><input id="n-name" type="text" placeholder="fx Matte Black" value="${esc(v("name"))}">
      <label>Farvekode</label><div class="row"><input id="n-color" type="color" value="#${esc(color)}"><input id="n-hex" type="text" value="${esc(color)}" maxlength="7"></div>
      <div class="grid2">
        <div><label>Filament ny (g)</label><input id="n-weight" type="number" min="1" value="${esc(v("weight", 1000) || 1000)}"></div>
        <div><label>Densitet (g/cm³)</label><input id="n-dens" type="number" step="0.01" placeholder="auto" value="${esc(v("density"))}"></div>
        <div><label>Diameter (mm)</label><input id="n-dia" type="number" step="0.01" value="${esc(v("diameter", 1.75) || 1.75)}"></div>
        <div><label>Dyse / plade °C</label><div class="row"><input id="n-et" type="number" placeholder="dyse" value="${esc(v("extruder_temp"))}"><input id="n-bt" type="number" placeholder="plade" value="${esc(v("bed_temp"))}"></div></div>
      </div></details>
    <div class="grid2">
      <div><label>Tom spole vejer (g)</label><input id="n-tare" type="number" min="0" placeholder="fx 250" value="${esc(v("spool_weight"))}"></div>
      <div><label>Tilbage nu (g)</label><input id="n-rem" type="number" min="0" placeholder="fuld"></div>
      <div><label>Antal spoler</label><input id="n-count" type="number" value="1" min="1" max="20"></div>
      <div><label>Pris pr. spole (kr)</label><input id="n-price" type="number" min="0" step="0.01"></div>
    </div>
    ${opts.barcode ? `<p class="tiny muted">Stregkode ${esc(opts.barcode)} huskes, så næste scanning udfylder alt med det samme.</p>` : ""}
    <div class="row" style="margin-top:14px"><button class="btn grow" id="n-back">Tilbage</button><button class="btn primary grow" id="n-save">Opret</button></div>`, (el) => {
    const setHex = (h) => { h = h.replace("#", "").toUpperCase(); if (/^[0-9A-F]{6}$/.test(h)) { $("#n-color", el).value = `#${h}`; $("#n-hex", el).value = h; } };
    $("#n-color", el).oninput = (e) => setHex(e.target.value);
    $("#n-hex", el).onchange = (e) => setHex(e.target.value);
    $("#n-back", el).onclick = () => newSpoolSheet(opts.slotKey, opts.slot, opts.barcode);
    $("#n-save", el).onclick = (e) => run(e.target, async () => {
      const num = (id) => { const x = String($(id, el).value).replace(",", "."); return x === "" ? null : Number(x); };
      const body = {
        vendor: $("#n-vendor", el).value.trim(), material: $("#n-mat", el).value.trim(), name: $("#n-name", el).value.trim(),
        color_hex: $("#n-hex", el).value.replace("#", ""), weight: num("#n-weight"), density: num("#n-dens"), diameter: num("#n-dia"),
        extruder_temp: num("#n-et"), bed_temp: num("#n-bt"), spool_weight: num("#n-tare"),
        remaining_weight: num("#n-rem"), count: num("#n-count") || 1, price: num("#n-price"), slot: opts.slotKey || null,
        barcode: opts.barcode || null,
      };
      if (!body.vendor || !body.material || !body.name) throw new Error("Udfyld producent, materiale og farve/navn");
      if (!(body.weight > 0)) throw new Error("Udfyld filamentets vægt");
      if (item) {
        // Unchanged filament definition -> reuse it (own filament) or link it to SpoolmanDB (external_id).
        const same = body.vendor === (it.manufacturer || "") && body.material === (it.material || "") && body.name === (it.name || "")
          && body.color_hex.toUpperCase() === color && Number(body.weight) === Number(it.weight || 1000);
        if (same && item.filament_id) body.filament_id = item.filament_id;
        if (same && item.external_id) body.external_id = item.external_id;
        if (same && it.color_hexes && it.color_hexes.length > 1) { body.color_hexes = it.color_hexes; body.multi_color_direction = it.multi_color_direction; }
      }
      const res = await api("/api/spools", { method: "POST", body });
      toast(`${res.length} spole(r) oprettet`);
      closeSheet(); S.meta = null; S.manufacturers = null; await loadSpools(); refresh(false); if (S.tab === "spools") renderSpools();
    });
  });
}

// ------------------------------------------------------------------ barcode scanning
// Returns the scanned text, or null if cancelled. Live camera needs https; the photo and typing options work everywhere.
function scanCode(title = "Scan stregkode") {
  return new Promise((resolve) => {
    let stream = null, done = false, raf = null;
    const finish = (val) => {
      if (done) return;
      done = true;
      if (raf) clearTimeout(raf);
      if (stream) stream.getTracks().forEach((t) => t.stop());
      closeSheet();
      resolve(val ? String(val).trim() : null);
    };
    const live = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    sheet(`<h2>${esc(title)}</h2>
      <div id="sc-live" class="${live ? "" : "hidden"}" style="position:relative;border-radius:12px;overflow:hidden;background:#000;aspect-ratio:4/3">
        <video id="sc-video" playsinline muted style="width:100%;height:100%;object-fit:cover"></video>
        <div style="position:absolute;left:10%;right:10%;top:40%;height:20%;border:2px solid rgba(255,255,255,.8);border-radius:8px"></div>
      </div>
      <p id="sc-msg" class="small muted center">${live ? "Hold stregkoden inden for rammen" : "Tag et billede af stregkoden – eller skriv tallene"}</p>
      <label class="btn block ${live ? "" : "primary"}" style="text-align:center">Tag billede af stregkoden
        <input id="sc-file" type="file" accept="image/*" capture="environment" hidden></label>
      <label>…eller skriv / scan koden</label>
      <div class="row"><input id="sc-text" type="text" inputmode="numeric" autocomplete="off" placeholder="fx 6970470250022">
        <button class="btn primary" id="sc-ok">OK</button></div>
      <button class="btn block" id="sc-cancel" style="margin-top:12px">Annullér</button>`, async (el) => {
      getDetector().catch(() => {});  // start downloading/compiling the decoder right away
      $("#sc-cancel", el).onclick = () => finish(null);
      $("#sc-ok", el).onclick = () => finish($("#sc-text", el).value);
      $("#sc-text", el).onkeydown = (e) => { if (e.key === "Enter") finish(e.target.value); };
      const bg = el.parentElement;
      bg.addEventListener("click", (e) => { if (e.target === bg) finish(null); });
      $("#sc-file", el).onchange = async (e) => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        $("#sc-msg", el).textContent = "Læser billedet…";
        try {
          const code = await decodeImage(file);
          if (code) return finish(code);
          $("#sc-msg", el).textContent = "Kunne ikke læse stregkoden – prøv igen tættere på og uden genskin, eller skriv tallene.";
        } catch (err) { $("#sc-msg", el).textContent = err.message; }
      };
      if (!live) return;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
        const video = $("#sc-video", el);
        video.srcObject = stream;
        await video.play();
        const detect = await liveDetector();
        let busy = false;
        const tick = async () => {
          if (done) return;
          if (!busy && video.readyState >= 2) {
            busy = true;
            try { const code = await detect(video); if (code) { if (navigator.vibrate) navigator.vibrate(60); return finish(code); } } catch (_) { /* keep trying */ }
            busy = false;
          }
          raf = setTimeout(tick, 120);
        };
        tick();
      } catch (err) {
        $("#sc-live", el).classList.add("hidden");
        $("#sc-msg", el).textContent = "Kameraet kunne ikke startes (" + err.message + "). Brug 'Tag billede' eller skriv koden.";
      }
    });
  });
}

// Decoder: the zxing-cpp WebAssembly ponyfill (barcode-detector 3.2.2, vendored) – identical on iPhone, Android and desktop,
// works over plain http too. Loaded on first use only.
const BARCODE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "qr_code", "data_matrix"];
const ZXING_JS = "/static/vendor/barcode-detector-3.2.2.js";
const ZXING_WASM_DIR = "/static/vendor/zxing-wasm-3.1.3/";
let _detectorPromise = null;
function getDetector() {
  if (_detectorPromise) return _detectorPromise;
  _detectorPromise = new Promise((resolve, reject) => {
    const ready = () => {
      try {
        const api = window.BarcodeDetectionAPI;
        api.prepareZXingModule({
          overrides: { locateFile: (p, prefix) => (p.endsWith(".wasm") ? ZXING_WASM_DIR + p : prefix + p) },
          fireImmediately: true,
        });
        resolve(new api.BarcodeDetector({ formats: BARCODE_FORMATS }));
      } catch (e) { reject(e); }
    };
    if (window.BarcodeDetectionAPI) return ready();
    const s = document.createElement("script");
    s.src = ZXING_JS;
    s.onload = ready;
    s.onerror = () => { _detectorPromise = null; reject(new Error("Stregkodelæseren kunne ikke indlæses – skriv koden i feltet")); };
    document.head.appendChild(s);
  });
  return _detectorPromise;
}
async function liveDetector() {
  const det = await getDetector();
  return async (video) => { const r = await det.detect(video); return r.length ? r[0].rawValue : null; };
}
// Phone photos are huge (12–48 MP) and iOS limits canvas size: scale down, retry larger, then rotated 45°.
function _toCanvas(bmp, maxSide, deg = 0) {
  const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const w = bmp.width * k, h = bmp.height * k, a = (deg * Math.PI) / 180;
  const cw = Math.ceil(Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a)));
  const ch = Math.ceil(Math.abs(w * Math.sin(a)) + Math.abs(h * Math.cos(a)));
  const c = document.createElement("canvas");
  c.width = cw; c.height = ch;
  const x = c.getContext("2d");
  x.fillStyle = "#fff"; x.fillRect(0, 0, cw, ch);
  x.translate(cw / 2, ch / 2); x.rotate(a); x.drawImage(bmp, -w / 2, -h / 2, w, h);
  return c;
}
async function decodeImage(file) {
  const det = await getDetector();
  const bmp = await createImageBitmap(file);
  try {
    for (const [maxSide, deg] of [[2048, 0], [4096, 0], [2048, 45], [2048, 90]]) {
      const hits = await det.detect(_toCanvas(bmp, maxSide, deg));
      if (hits.length) return hits[0].rawValue;
    }
    return null;
  } finally { if (bmp.close) bmp.close(); }
}

async function scanForNewSpool(slotKey, slot) {
  const code = await scanCode();
  if (!code) return newSpoolSheet(slotKey, slot);
  let r;
  try { r = await api("/api/barcode/lookup", { method: "POST", body: { code } }); } catch (e) { toast(e.message, true); return newSpoolSheet(slotKey, slot); }
  handleLookup(r, slotKey, slot);
}

function handleLookup(r, slotKey, slot) {
  if (r.kind === "spool") {
    if (!S.spools || !S.spools.some((s) => s.id === r.spool.id)) (S.spools ||= []).push(r.spool);
    return spoolSheet(r.spool.id);
  }
  if (r.kind === "known") {
    return spoolForm(r.item, { slotKey, slot, barcode: r.code, source: `${r.source} – alt er udfyldt` });
  }
  if (r.kind === "match") {
    const sure = r.confidence >= 0.8;
    return spoolForm(r.item, { slotKey, slot, barcode: r.code, source: `Fundet: “${r.title}” (${r.provider})`,
      notice: sure ? null : "Tjek at farve og variant passer – ret eller gå tilbage og vælg i listen." });
  }
  if (r.kind === "title") {
    const p = r.parsed || {};
    const name = (p.color_words || []).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
    return spoolForm({ manufacturer: p.vendor || "", material: p.material || "", name, weight: p.weight || 1000, diameter: p.diameter || 1.75 },
      { slotKey, slot, barcode: r.code, source: `Fundet: “${r.title}” (${r.provider})`,
        notice: "Produktet findes ikke i SpoolmanDB – tjek felterne. Stregkoden huskes til næste gang." });
  }
  // unknown: let the user pick in the catalogue; the code is remembered on the spool they create
  toast(r.message || "Ukendt stregkode – vælg filamentet i listen", true);
  return newSpoolSheet(slotKey, slot, r.code, r.vendor_hint);
}

// ------------------------------------------------------------------ history tab
function renderHistory() {
  const h = S.history;
  let html = "";
  if (h.pending.length) {
    html += `<div class="card"><h3>Venter på dig</h3>`;
    for (const d of h.pending) {
      const uncertain = d.state === "uncertain";
      const unknownGrams = !(d.full_grams > 0);
      const gramsTxt = unknownGrams ? `<b>forbrug ukendt</b>` : `<b>${fmtG1(d.grams)}</b>${d.full_grams !== d.grams ? ` <span class="muted">(fuldt print ${fmtG1(d.full_grams)})</span>` : ""}`;
      html += `<div class="slot"><span class="swatch sm"><i style="background:#${esc(d.color || (d.spool && d.spool.color) || "888")}"></i></span><div class="grow stack">
        <div><b>${esc(d.title || d.task_id)}</b></div>
        <div class="small">${esc(d.label)}${d.material ? ` · ${esc(d.material)}` : ""} · ${gramsTxt}${d.spool ? ` · spole #${d.spool.id} ${esc(d.spool.name)}` : ""}</div>
        <div class="small muted">${esc(d.note || "")}</div>
        <div class="row wrap">${uncertain
          ? `<button class="btn small primary" data-checked="${d.id}">Vægten er tjekket</button>`
          : `<button class="btn small primary" data-resolve="${d.id}" data-grams="${unknownGrams ? "" : d.grams}" data-slot="${esc(d.slot)}">${unknownGrams ? "Indtast forbrug" : "Vælg spole"}</button>
             ${d.spool && !unknownGrams ? `<button class="btn small" data-direct="${d.id}" data-spool="${d.spool.id}" data-grams="${d.grams}">Træk fra #${d.spool.id}</button>` : ""}
             <button class="btn small" data-skip="${d.id}">Spring over</button>`}</div></div></div>`;
    }
    html += `</div>`;
  }
  html += `<div class="row between" style="margin:6px 0 10px"><span class="small muted">Seneste prints</span>
    <button class="btn small" id="h-sync">Synkronisér nu</button></div>`;
  if (!h.tasks.length) html += `<div class="card muted">Ingen prints registreret endnu. Forbrug trækkes automatisk fra, når et print er færdigt.</div>`;
  for (const t of h.tasks) {
    const applied = t.deductions.filter((d) => d.state === "applied").reduce((a, d) => a + d.grams, 0);
    html += `<div class="card"><div class="row between"><b class="grow">${esc(t.title)}</b><span class="small">${fmtG1(applied)}</span></div>
      <div class="tiny muted">${fmtIso(t.end_time || t.start_time)} · ${esc(t.factor_source)}</div>
      ${t.deductions.map((d) => `<div class="row small" style="margin-top:6px"><span class="swatch sm"><i style="background:#${esc(d.color || "888")}"></i></span>
        <span class="grow">${esc(d.label)} → ${d.spool ? `#${d.spool.id} ${esc(d.spool.name)}` : "–"}</span>
        <span>${fmtG1(d.grams)}</span><span class="state ${esc(d.state)}">${{ applied: "trukket", pending: "venter", skipped: "sprunget over", uncertain: "tjek", applying: "…" }[d.state] || esc(d.state)}</span></div>`).join("")}
    </div>`;
  }
  html += `<details class="card" id="h-log"><summary>Hændelseslog</summary><div id="h-events" class="small" style="margin-top:8px">Henter…</div></details>`;
  $("#view").innerHTML = html;
  const reload = async () => { S.history = await api("/api/history"); renderHistory(); refresh(false); };
  document.querySelectorAll("[data-resolve]").forEach((b) => b.onclick = () => {
    const d = h.pending.find((x) => x.id === Number(b.dataset.resolve));
    pickSpool(b.dataset.slot, Number(b.dataset.resolve), b.dataset.grams === "" ? "" : Number(b.dataset.grams), d && d.spool ? d.spool.id : null);
  });
  document.querySelectorAll("[data-direct]").forEach((b) => b.onclick = () => run(b, async () => {
    const body = { spool_id: Number(b.dataset.spool), grams: Number(b.dataset.grams) };
    try {
      await api(`/api/deductions/${b.dataset.direct}/resolve`, { method: "POST", body });
    } catch (e) {
      if (!/kun .* g tilbage|vejet efter printet/.test(e.message) || !confirm(`${e.message}\n\nTræk fra alligevel?`)) throw e;
      await api(`/api/deductions/${b.dataset.direct}/resolve`, { method: "POST", body: { ...body, force: true } });
    }
    toast("Trukket fra"); await reload();
  }));
  document.querySelectorAll("[data-skip]").forEach((b) => b.onclick = () => run(b, async () => {
    if (!confirm("Spring over? Der trækkes intet fra.")) return;
    await api(`/api/deductions/${b.dataset.skip}/resolve`, { method: "POST", body: { spool_id: null } }); await reload();
  }));
  document.querySelectorAll("[data-checked]").forEach((b) => b.onclick = () => run(b, async () => {
    await api(`/api/deductions/${b.dataset.checked}/checked`, { method: "POST" }); await reload();
  }));
  $("#h-sync").onclick = (e) => run(e.target, async () => { const r = await api("/api/sync-now", { method: "POST" });
    toast(r.error ? r.error : `Synkroniseret (${r.processed || 0} nye prints)`, !!r.error); await reload(); });
  $("#h-log").ontoggle = async () => {
    if (!$("#h-log").open) return;
    const ev = await api("/api/events?limit=150");
    $("#h-events").innerHTML = ev.map((e) => `<div style="margin-bottom:4px"><span class="muted tiny">${fmtTime(e.ts)}</span>
      <span class="${e.level === "error" ? "state pending" : e.level === "warning" ? "state uncertain" : ""}">${esc(e.message)}</span></div>`).join("") || "Tom";
  };
}

// ------------------------------------------------------------------ settings tab
function renderSettings() {
  const st = S.status;
  const b = st.bambu;
  const exp = b.expires_at ? new Date(b.expires_at * 1000).toLocaleDateString("da-DK") : "ukendt";
  const mqttTxt = { connected: "Forbundet", connecting: "Forbinder…", reconnecting: "Genforbinder…", auth_failed: "Login udløbet", not_logged_in: "Ikke logget ind", no_device: "Ingen printer valgt", stopped: "Stoppet", error: "Fejl" }[b.mqtt] || b.mqtt;
  let html = `<div class="card"><h2>Bambu Lab-konto</h2>`;
  if (b.logged_in) {
    html += `<dl class="kv"><dt>Konto</dt><dd>${esc(b.email || "(token)")}</dd><dt>Login udløber</dt><dd>${esc(exp)}</dd>
      <dt>Printer-forbindelse</dt><dd>${esc(mqttTxt)}${b.mqtt_detail ? ` <span class="muted small">(${esc(b.mqtt_detail)})</span>` : ""}</dd>
      <dt>Sidste data</dt><dd>${fmtTime(b.last_message)}</dd><dt>Printhistorik hentet</dt><dd>${fmtTime(b.last_task_poll_ok)}</dd></dl>
      ${b.cloud_error ? `<div class="alert warn">${esc(b.cloud_error)}</div>` : ""}
      <div class="row wrap" style="margin-top:10px"><button class="btn" id="b-relogin">Log ind igen</button><button class="btn danger" id="b-logout">Log ud</button></div>
      <div id="b-devices" style="margin-top:12px"></div>`;
  }
  html += `<div id="b-login" class="${b.logged_in ? "hidden" : ""}">${bambuLoginForm()}</div></div>`;
  html += `<div class="card" id="fm-card"><h2>Bambu Filament Manager</h2><p class="small muted">Henter…</p></div>`;
  html += `<div class="card"><h2>Spoolman</h2><p class="small muted">Lageret ligger i Spoolman. Du kan også redigere spoler direkte dér.</p>
    <a class="btn" href="${esc(st.spoolman_url)}" target="_blank" rel="noopener">Åbn Spoolman</a></div>`;
  html += `<div class="card"><h2>Skift adgangskode til appen</h2>
    <label>Nuværende</label><input id="p-cur" type="password" autocomplete="current-password">
    <label>Ny (mindst 6 tegn)</label><input id="p-new" type="password" autocomplete="new-password">
    <div class="row" style="margin-top:10px"><button class="btn" id="p-save">Gem</button><button class="btn danger" id="p-logout">Log ud af appen</button></div></div>
    <p class="tiny muted center">Spool Sync · Tip: brug "Føj til hjemmeskærm" i browseren for at få den som app.</p>`;
  $("#view").innerHTML = html;
  bindBambuLogin();
  loadFm(false);
  if (b.logged_in) {
    $("#b-relogin").onclick = () => $("#b-login").classList.toggle("hidden");
    $("#b-logout").onclick = (e) => run(e.target, async () => { if (!confirm("Log ud af Bambu? Forbrug registreres ikke, før du logger ind igen.")) return;
      await api("/api/bambu/logout", { method: "POST" }); await refresh(true); });
    loadDevices();
  }
  $("#p-save").onclick = (e) => run(e.target, async () => {
    await api("/api/auth/password", { method: "POST", body: { current: $("#p-cur").value, new: $("#p-new").value } }); toast("Adgangskode ændret");
    $("#p-cur").value = ""; $("#p-new").value = "";
  });
  $("#p-logout").onclick = () => run(null, async () => { await api("/api/auth/logout", { method: "POST" }); showLogin({ password_set: true }); });
}

// ------------------------------------------------------------------ Bambu Filament Manager card
async function loadFm(refresh) {
  const card = $("#fm-card");
  if (!card) return;
  let ov;
  try {
    if (!S.spools) await loadSpools();
    ov = await api(`/api/fm${refresh ? "?refresh=true" : ""}`);
  } catch (e) { card.innerHTML = `<h2>Bambu Filament Manager</h2><div class="alert warn">${esc(e.message)}</div>`; return; }
  renderFm(ov);
}

function renderFm(ov) {
  const card = $("#fm-card");
  if (!card) return;
  const spools = Object.fromEntries((S.spools || []).map((s) => [s.id, s]));
  const linked = new Set(ov.linked_spools || []);
  const opt = (id) => { const s = spools[id]; return s ? `#${s.id} ${esc(s.vendor)} ${esc(s.material)} ${esc(s.name)} · ${fmtG(s.remaining)}` : `#${id}`; };
  const row = (u, ignored) => {
    const others = (S.spools || []).filter((s) => !s.archived && !linked.has(s.id) && !u.suggestions.includes(s.id));
    return `<div class="slot"><span class="swatch sm"><i style="background:#${esc(u.color || "888")}"></i></span><div class="grow stack">
      <div><b>${esc(u.vendor || "Generic")} ${esc(u.material)} · ${esc(u.name)}</b>${u.rfid ? ` <span class="chip">RFID</span>` : ""}${u.slot ? ` <span class="chip on">${esc(u.slot)}</span>` : ""}</div>
      <div class="small muted">${esc(u.net)} / ${esc(u.total)} g i Bambu</div>
      ${ignored ? `<div class="row wrap"><button class="btn small" data-fmunignore="${u.fm_id}">Fortryd ignorér</button></div>` : `
      <select data-fmsel="${u.fm_id}"><option value="">Vælg spole…</option>
        ${u.suggestions.length ? `<optgroup label="Forslag">${u.suggestions.map((id) => `<option value="${id}">${opt(id)}</option>`).join("")}</optgroup>` : ""}
        <optgroup label="Alle ukoblede spoler">${others.map((s) => `<option value="${s.id}">${opt(s.id)}</option>`).join("")}</optgroup></select>
      <div class="row wrap"><button class="btn small primary" data-fmlink="${u.fm_id}">Kobl</button>
        <button class="btn small" data-fmimport="${u.fm_id}">Findes ikke – importér</button>
        <button class="btn small" data-fmignore="${u.fm_id}">Ignorér</button></div>`}</div></div>`;
  };
  let html = `<h2>Bambu Filament Manager</h2>
    <p class="small muted">Spolelisten i Bambu Studio og Bambu Handy. Spool Sync holder vægten dér korrekt, og når du vælger
    spole til et AMS-slot i Bambu Studio (Enhed → AMS → rediger slot), følger appen med – og omvendt.</p>
    <label class="row" style="gap:8px;color:var(--text)"><input type="checkbox" id="fm-on" ${ov.enabled ? "checked" : ""}> Synkronisér med Bambu</label>`;
  if (ov.enabled) {
    html += `<dl class="kv" style="margin-top:8px"><dt>Spoler i Bambu</dt><dd>${ov.fm_total}</dd><dt>Koblet</dt><dd>${ov.linked}</dd>
      <dt>Sidst synkroniseret</dt><dd>${ov.last_sync ? fmtTime(ov.last_sync) : "venter…"}</dd></dl>
      ${ov.error ? `<div class="alert warn small">${esc(ov.error)}</div>` : ""}
      ${ov.empty_long ? `<div class="alert warn small">Bambu har vist en tom spoleliste i over 10 minutter. Det er som regel
        en fejl hos Bambu, som går over af sig selv. Har du selv slettet alle spoler i Bambu Studio/Handy?
        <div class="row" style="margin-top:6px"><button class="btn small danger" id="fm-empty">Ja, nulstil koblingerne</button></div></div>` : ""}`;
    if (ov.unlinked_fm.length) {
      html += `<h3 style="margin-top:12px">Kobl dine Bambu-spoler (${ov.unlinked_fm.length})</h3>
        <p class="tiny muted">Vælg hvilken spole i lageret hver Bambu-spole er. Forslag står øverst.</p>` + ov.unlinked_fm.map((u) => row(u, false)).join("");
    }
    if (ov.missing_in_fm.length) {
      html += `<div class="alert info small" style="margin-top:10px">${ov.missing_in_fm.length} spoler i lageret findes ikke i Bambu
        (${ov.missing_in_fm.map((id) => `#${id}`).join(", ")}).
        <div class="row wrap" style="margin-top:6px"><button class="btn small primary" id="fm-create">Opret dem i Bambu</button></div></div>`;
    }
    html += `<label class="row small" style="gap:8px;color:var(--text);margin-top:8px"><input type="checkbox" id="fm-auto" ${ov.auto_create ? "checked" : ""}>
      Opret nye spoler automatisk i Bambu</label>
      <div class="row wrap" style="margin-top:10px"><button class="btn small" id="fm-sync">Synkronisér nu</button></div>`;
    if ((ov.ignored_fm || []).length) {
      html += `<details style="margin-top:10px"><summary class="small">Ignorerede Bambu-spoler (${ov.ignored_fm.length})</summary>${ov.ignored_fm.map((u) => row(u, true)).join("")}</details>`;
    }
  }
  if (ov.created_by_us) {
    html += `<div class="row wrap" style="margin-top:10px"><button class="btn small danger" id="fm-remove">Fjern de ${ov.created_by_us} spoler Spool Sync har oprettet i Bambu</button></div>`;
  }
  card.innerHTML = html;
  // One request at a time: the whole card is locked while it runs (no double taps).
  const post = async (path, body, msg, after) => {
    if (card.dataset.busy) return;
    card.dataset.busy = "1";
    card.style.opacity = ".6";
    card.querySelectorAll("button,select,input").forEach((x) => { x.disabled = true; });
    try {
      const r = await api(path, { method: "POST", body });
      if (after) await after(r);
      if (msg) toast(typeof msg === "function" ? msg(r) : msg);
      delete card.dataset.busy;
      card.style.opacity = "";
      renderFm(r);
    } catch (e) {
      delete card.dataset.busy;
      card.style.opacity = "";
      card.querySelectorAll("button,select,input").forEach((x) => { x.disabled = false; });
      if (/allerede koblet/.test(e.message) && body && body.spool_id && !body.force && confirm(`${e.message}. Flyt koblingen hertil?`)) {
        return post(path, { ...body, force: true }, msg, after);
      }
      toast(e.message, true);
    }
  };
  $("#fm-on", card).onchange = (e) => post("/api/fm/settings", { enabled: e.target.checked }, e.target.checked ? "Synkronisering slået til" : "Slået fra");
  const em = $("#fm-empty", card);
  if (em) em.onclick = () => { if (confirm("Glem alle koblinger til Bambu-spoler? Gør det kun, hvis du har slettet alle spoler i Bambu.")) post("/api/fm/confirm-empty", {}, () => "Koblingerne er nulstillet"); };
  const rm = $("#fm-remove", card);
  if (rm) rm.onclick = () => { if (confirm("Slet de spoler Spool Sync har oprettet i Bambu Filament Manager? Automatisk oprettelse slås samtidig fra.")) post("/api/fm/remove-created", {}, (r) => `${r.removed} fjernet`); };
  if (!ov.enabled) return;
  $("#fm-auto", card).onchange = (e) => post("/api/fm/settings", { auto_create: e.target.checked });
  $("#fm-sync", card).onclick = () => post("/api/fm/sync", {}, (r) => (r.result && r.result.error) ? r.result.error : "Synkroniseret");
  const cr = $("#fm-create", card);
  if (cr) cr.onclick = () => { if (confirm(`Opret ${ov.missing_in_fm.length} spoler i Bambu Filament Manager?`)) post("/api/fm/create", { spool_ids: ov.missing_in_fm }, (r) => `${r.created} oprettet${r.errors.length ? " – " + r.errors[0] : ""}`); };
  card.querySelectorAll("[data-fmlink]").forEach((b) => b.onclick = () => {
    const sel = card.querySelector(`[data-fmsel="${b.dataset.fmlink}"]`);
    if (!sel.value) return toast("Vælg en spole først", true);
    post("/api/fm/link", { fm_id: Number(b.dataset.fmlink), spool_id: Number(sel.value) }, "Koblet");
  });
  card.querySelectorAll("[data-fmimport]").forEach((b) => b.onclick = () => post("/api/fm/import", { fm_id: Number(b.dataset.fmimport) },
    (r) => `Importeret som spole #${r.spool_id}`, async () => { await loadSpools(); }));
  card.querySelectorAll("[data-fmignore]").forEach((b) => b.onclick = () => post("/api/fm/ignore", { fm_id: Number(b.dataset.fmignore) }));
  card.querySelectorAll("[data-fmunignore]").forEach((b) => b.onclick = () => post("/api/fm/unignore", { fm_id: Number(b.dataset.fmunignore) }));
}

function bambuLoginForm() {
  return `<div id="bl-step1"><p class="small muted">Log ind med samme konto som i Bambu Handy. Adgangskoden sendes kun til Bambu og gemmes ikke.</p>
      <label>E-mail</label><input id="bl-email" type="email" autocomplete="username">
      <label>Adgangskode</label><input id="bl-pw" type="password" autocomplete="current-password">
      <div class="row wrap" style="margin-top:10px"><button class="btn primary" id="bl-go">Log ind</button>
        <button class="btn" id="bl-codeonly">Log ind med e-mailkode</button></div>
      <p class="tiny muted">Bruger du Google- eller Apple-login? Så vælg <b>Log ind med e-mailkode</b> (virker hvis din konto har en e-mail), eller brug token nedenfor.</p></div>
    <div id="bl-step2" class="hidden"><p id="bl-msg" class="small"></p>
      <label id="bl-code-label">Kode</label><input id="bl-code" type="text" inputmode="numeric" autocomplete="one-time-code">
      <div class="row wrap" style="margin-top:10px"><button class="btn primary" id="bl-verify">Bekræft</button>
        <button class="btn" id="bl-resend">Send ny kode</button><button class="btn" id="bl-back">Tilbage</button></div></div>
    <details style="margin-top:12px"><summary class="small">Avanceret: brug token (Google/Apple-login)</summary>
      <ol class="small muted"><li>Log ind på <b>bambulab.com</b> i en browser på en computer.</li>
      <li>Åbn udviklerværktøjer (F12) → Application/Lagring → Cookies → bambulab.com.</li>
      <li>Kopiér værdien af cookien <b>token</b> og indsæt her.</li></ol>
      <textarea id="bl-token" rows="3" placeholder="token"></textarea>
      <button class="btn" id="bl-token-go" style="margin-top:8px">Brug token</button></details>`;
}

function bindBambuLogin() {
  let mode = "code";
  const step2 = (msg, m) => { mode = m; $("#bl-step1").classList.add("hidden"); $("#bl-step2").classList.remove("hidden");
    $("#bl-msg").textContent = msg; $("#bl-code-label").textContent = m === "tfa" ? "Kode fra authenticator-app" : "Kode fra e-mail";
    $("#bl-resend").classList.toggle("hidden", m === "tfa"); $("#bl-code").value = ""; $("#bl-code").focus(); };
  const done = async () => { toast("Logget ind på Bambu"); await refresh(true); };
  const handle = async (r) => { if (r.next === "done") return done(); step2(r.message || "", r.next); };
  $("#bl-go").onclick = (e) => run(e.target, async () => handle(await api("/api/bambu/login", { method: "POST", body: { email: $("#bl-email").value.trim(), password: $("#bl-pw").value } })));
  $("#bl-codeonly").onclick = (e) => run(e.target, async () => { const email = $("#bl-email").value.trim(); if (!email) throw new Error("Indtast e-mail");
    handle(await api("/api/bambu/send-code", { method: "POST", body: { email } })); });
  $("#bl-verify").onclick = (e) => run(e.target, async () => handle(await api(mode === "tfa" ? "/api/bambu/tfa" : "/api/bambu/code",
    { method: "POST", body: { code: $("#bl-code").value.trim(), email: $("#bl-email").value.trim() || null } })));
  $("#bl-resend").onclick = (e) => run(e.target, async () => { await api("/api/bambu/send-code", { method: "POST", body: { email: $("#bl-email").value.trim() } }); toast("Ny kode sendt"); });
  $("#bl-back").onclick = () => { $("#bl-step2").classList.add("hidden"); $("#bl-step1").classList.remove("hidden"); };
  $("#bl-token-go").onclick = (e) => run(e.target, async () => handle(await api("/api/bambu/token", { method: "POST", body: { token: $("#bl-token").value.trim() } })));
}

async function loadDevices() {
  try {
    const devs = await api("/api/bambu/devices");
    $("#b-devices").innerHTML = `<h3>Printer</h3>` + devs.map((d) => `<div class="list-item" data-dev="${esc(d.dev_id)}">
      <div class="grow"><b>${esc(d.name)}</b> <span class="small muted">${esc(d.model || "")}</span>
      <div class="tiny muted">${d.online ? "Online" : "Offline"}</div></div>${d.selected ? `<span class="chip on">Valgt</span>` : `<button class="btn small">Vælg</button>`}</div>`).join("");
    document.querySelectorAll("[data-dev]").forEach((it) => it.onclick = () => run(null, async () => {
      await api("/api/bambu/device", { method: "POST", body: { dev_id: it.dataset.dev } }); toast("Printer valgt"); await refresh(true);
    }));
  } catch (e) { $("#b-devices").innerHTML = `<div class="alert warn">${esc(e.message)}</div>`; }
}

window.switchTab = switchTab;
boot();

// ============================================================================ statistics
const fmtKg = (g) => (Number(g) || 0) >= 1000 ? `${((Number(g) || 0) / 1000).toLocaleString("da-DK", { maximumFractionDigits: 2 })} kg` : fmtG(g);
const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

function isoWeek(iso) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
  const w1 = new Date(d.getFullYear(), 0, 4);
  return 1 + Math.round(((d - w1) / 86400000 - 3 + ((w1.getDay() + 6) % 7)) / 7);
}

function hbars(items, label) {
  const max = Math.max(1, ...items.map((x) => x.grams));
  return items.map((x) => `<div class="hbar"><div class="hbar-label">${label(x)}</div>
    <div class="track"><i style="width:${Math.max(1, (100 * x.grams) / max)}%"></i></div><div class="small">${fmtKg(x.grams)}</div></div>`).join("");
}

function renderStats() {
  const st = S.stats;
  const mode = S.statsMode || "weeks";
  const series = mode === "weeks"
    ? st.weeks.map((w) => ({ label: `${isoWeek(w.start)}`, long: `Uge ${isoWeek(w.start)}`, grams: w.grams }))
    : st.months.map((m) => { const [y, mo] = m.month.split("-").map(Number); return { label: MONTHS[mo - 1], long: `${MONTHS[mo - 1]} ${y}`, grams: m.grams }; });
  const max = Math.max(1, ...series.map((x) => x.grams));
  const since = st.since ? new Date(st.since * 1000).toLocaleDateString("da-DK", { day: "numeric", month: "short", year: "numeric" }) : "";
  let html = st.spoolman_error ? `<div class="alert err">Spoolman svarer ikke – priser, spolenavne og lager mangler: ${esc(st.spoolman_error)}</div>` : "";
  html += `<div class="card"><div class="tiles">
      <div class="tile"><b>${fmtKg(st.total)}</b><span>brugt i alt${since ? ` siden ${esc(since)}` : ""}</span></div>
      <div class="tile"><b>${fmtKg(st.last30)}</b><span>de sidste 30 dage</span></div>
      <div class="tile"><b>${st.prints}</b><span>print trukket fra</span></div>
      <div class="tile"><b>${st.cost != null ? `${st.cost.toLocaleString("da-DK", { maximumFractionDigits: 0 })} kr` : "–"}</b>
        <span>${st.cost != null ? (st.cost_coverage < 0.99 ? `filament (pris kendt for ${Math.round(st.cost_coverage * 100)} %)` : "filament") : st.spoolman_error ? "pris ukendt (Spoolman svarer ikke)" : "pris ukendt – indtast pris på spolerne"}</span></div>
    </div></div>
    <div class="card"><div class="row" style="justify-content:space-between"><h3>Forbrug</h3>
      <div class="row" style="gap:6px"><button class="chip ${mode === "weeks" ? "on" : ""}" data-mode="weeks">Uger</button>
      <button class="chip ${mode === "months" ? "on" : ""}" data-mode="months">Måneder</button></div></div>
      <div class="vbars" role="img" aria-label="Forbrug pr. ${mode === "weeks" ? "uge" : "måned"}">${series.map((x, i) =>
        `<div class="col${x.grams > 0 ? "" : " zero"}" data-i="${i}" title="${esc(x.long)}: ${fmtG(x.grams)}"><i style="height:${x.grams > 0 ? Math.max(3, (100 * x.grams) / max) : 2}%"></i></div>`).join("")}</div>
      <div class="vlabels">${series.map((x, i) => `<span>${mode === "weeks" && i % 2 ? "" : esc(x.label)}</span>`).join("")}</div>
      <p class="small muted" id="bar-caption">Tryk på en søjle for at se forbruget.</p></div>`;
  if (st.materials.length) html += `<div class="card"><h3>Materialer</h3>${hbars(st.materials, (x) => esc(x.material))}</div>`;
  if (st.spools.length) {
    html += `<div class="card"><h3>Mest brugte spoler</h3>${hbars(st.spools, (x) =>
      `${swatch(x.color, x.colors, "sm")} <span>#${x.id} ${esc(x.name || x.material)}${x.deleted ? ` <span class="muted">(slettet)</span>` : x.archived ? ` <span class="muted">(arkiveret)</span>` : ""}</span>`)}</div>`;
  }
  if (st.forecast.length) {
    html += `<div class="card"><h3>Løber tør om</h3><p class="tiny muted">Ud fra forbruget de sidste 30 dage (eller siden spolen blev taget i brug).</p>` +
      st.forecast.map((f) => `<div class="slot">${swatch(f.color, f.colors, "sm")}<div class="grow stack">
        <div><b>#${f.id} ${esc(f.name)}</b></div>
        <div class="small">${f.days_left <= 7 ? "⚠️ " : ""}ca. ${f.days_left} ${f.days_left === 1 ? "dag" : "dage"}
          <span class="muted">· ${fmtG(f.remaining)} tilbage · ${fmtG1(f.per_day)}/dag</span></div></div></div>`).join("") + `</div>`;
  }
  const sm = st.spoolman;
  if (!st.spoolman_error) html += `<div class="card"><h3>Hele lageret</h3><p class="tiny muted">Fra Spoolman – også forbrug fra før Spool Sync.</p>
    <dl class="kv"><dt>Brugt i alt</dt><dd>${fmtKg(sm.used)}</dd><dt>Tilbage</dt><dd>${fmtKg(sm.remaining)} på ${sm.active} aktive spoler</dd></dl>
    ${sm.materials.length ? hbars(sm.materials, (x) => esc(x.material)) : ""}</div>`;
  $("#view").innerHTML = html;
  document.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => { S.statsMode = b.dataset.mode; renderStats(); });
  document.querySelectorAll(".vbars .col").forEach((c) => c.onclick = () => {
    document.querySelectorAll(".vbars .col").forEach((x) => x.classList.toggle("sel", x === c));
    const x = series[Number(c.dataset.i)];
    $("#bar-caption").textContent = `${x.long}: ${fmtG(x.grams)}`;
  });
}
