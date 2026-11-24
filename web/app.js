"use strict";
// ChessPrep - logika interfejsu.

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

async function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const state = {
  startFen: START,
  moves: [],        // [{san, uci, fen}]
  cursor: 0,        // ile ruchów z listy jest na szachownicy
  orientation: "white",
  pos: null,        // odpowiedź /api/position dla bieżącej pozycji
  job: null,        // id trwającego wyszukiwania
  result: null,     // wyniki wyświetlane w panelu
  arrows: [],
};

const board = new Board($("#board"), { onMove: userMove });

function curFen() {
  return state.cursor === 0 ? state.startFen : state.moves[state.cursor - 1].fen;
}

function fmtEval(cpWhite, mate) {
  if (mate !== null && mate !== undefined) return (mate > 0 ? "+M" : "-M") + Math.abs(mate);
  const v = cpWhite / 100;
  return (v > 0 ? "+" : "") + v.toFixed(2);
}

// Ocena z perspektywy "mojego" koloru -> zapis z perspektywy białych.
function myToWhite(cp, color) { return color === "white" ? cp : -cp; }

function moveNumberPrefix(fen, idx) {
  const parts = fen.split(" ");
  const startBlack = parts[1] === "b";
  const startNo = +parts[5] || 1;
  const ply = idx + (startBlack ? 1 : 0);
  const no = startNo + Math.floor(ply / 2);
  if (ply % 2 === 0) return no + ".";
  return idx === 0 ? no + "…" : "";
}

function lineText(fen, sans) {
  return sans.map((s, i) => `${moveNumberPrefix(fen, i)}${s}`).join(" ");
}

// ------------------------------------------------------------------ pozycja

async function refresh({ analyse = true } = {}) {
  const fen = curFen();
  const pos = await api("/api/position?fen=" + encodeURIComponent(fen));
  state.pos = pos;
  const last = state.cursor > 0 ? state.moves[state.cursor - 1].uci : null;
  let check = null;
  if (pos.check) {
    const pcs = Board.parseFen(fen);
    const k = pos.turn === "white" ? "wK" : "bK";
    check = Object.keys(pcs).find((s) => pcs[s] === k) || null;
  }
  board.set({ fen, dests: pos.dests, orientation: state.orientation,
    lastMove: last ? [last.slice(0, 2), last.slice(2, 4)] : null, check, arrows: state.arrows });
  $("#fen").value = fen;
  renderMoves();
  syncResults();
  if (analyse) {
    loadStats(fen);
    if ($("#auto-engine").checked) loadEngine(fen);
  }
}

function renderMoves() {
  const el = $("#moves");
  el.innerHTML = "";
  state.moves.forEach((m, i) => {
    const pre = moveNumberPrefix(state.startFen, i);
    if (pre) el.insertAdjacentHTML("beforeend", `<span class="mn">${pre}</span>`);
    const s = document.createElement("span");
    s.className = "mv" + (i + 1 === state.cursor ? " cur" : "");
    s.textContent = m.san;
    s.onclick = () => goto(i + 1);
    el.appendChild(s);
  });
  const cur = el.querySelector(".cur");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function goto(n) {
  state.cursor = Math.max(0, Math.min(state.moves.length, n));
  state.arrows = [];
  refresh();
}

async function userMove(from, to) {
  const fen = curFen();
  let uci = from + to;
  const pcs = Board.parseFen(fen);
  const p = pcs[from];
  if (p && p[1] === "P" && (to[1] === "8" || to[1] === "1")) {
    uci += await askPromotion();
  }
  try {
    const r = await api("/api/move", { method: "POST", body: { fen, uci } });
    const next = state.moves[state.cursor];
    if (next && next.uci === r.uci) {
      state.cursor++;
    } else {
      state.moves = state.moves.slice(0, state.cursor);
      state.moves.push({ san: r.san, uci: r.uci, fen: r.fen });
      state.cursor = state.moves.length;
    }
    state.arrows = [];
    refresh();
  } catch (e) {
    refresh({ analyse: false });
  }
}

function askPromotion() {
  return new Promise((resolve) => {
    const dlg = $("#dlg-promo");
    const color = curFen().split(" ")[1];
    dlg.querySelectorAll("button").forEach((b) => (b.style.backgroundImage = Board.pieceUrl(color + b.dataset.p)));
    dlg.onclose = () => resolve(dlg.returnValue || "q");
    dlg.showModal();
  });
}

// ------------------------------------------------------------------ silnik i statystyki

let engineToken = 0;
async function loadEngine(fen) {
  const my = ++engineToken;
  $("#engine-lines").innerHTML = '<span class="muted">liczę…</span>';
  try {
    const r = await api(`/api/analyse?fen=${encodeURIComponent(fen)}&depth=18&multipv=3`);
    if (my !== engineToken) return;
    if (!r.lines.length) { $("#engine-lines").textContent = "koniec partii"; $("#eval-badge").textContent = ""; return; }
    $("#eval-badge").textContent = fmtEval(r.lines[0].cp, r.lines[0].mate);
    $("#engine-lines").innerHTML = r.lines.map((l) => {
      const w = l.wdl;
      return `<div class="eline"><b>${fmtEval(l.cp, l.mate)}</b>
        <span class="pv">${lineText(fen, l.san.slice(0, 10))}</span>
        <span class="wdl" title="WDL (promile): wygrana białych / remis / wygrana czarnych">${w[0]}/${w[1]}/${w[2]}</span></div>`;
    }).join("") + `<div class="muted small">${r.engine}, głębokość ${r.lines[0].depth}</div>`;
  } catch (e) {
    if (my === engineToken) $("#engine-lines").textContent = "Błąd silnika: " + e.message;
  }
}

let statsToken = 0;
async function loadStats(fen) {
  const my = ++statsToken;
  const lo = +$("#p-elo-min").value, hi = +$("#p-elo-max").value;
  const speeds = $$(".p-speed").filter((c) => c.checked).map((c) => c.value).join(",") || "blitz,rapid,classical";
  $("#stats-range").textContent = `${lo}–${hi}`;
  const tbl = $("#stats-table");
  tbl.innerHTML = '<tr><td class="muted">ładuję…</td></tr>';
  try {
    const src = $("#p-lsource").value;
    const srcName = { players: "Lichess", masters: "Masters", both: "Lichess+M" }[src];
    const srcTitle = { players: "gracze Lichess, wybrany zakres i tempo", masters: "partie mistrzowskie (bez filtra rankingu)", both: "gracze Lichess + Masters" }[src];
    // poprzednie pozycje (Maia-3 bierze pod uwagę historię) i przeciwnik
    const histMoves = state.moves.slice(0, state.cursor).map((m) => m.uci).join(",");
    const opp = $("#p-opponent").value;
    const r = await api(`/api/stats?fen=${encodeURIComponent(fen)}&elo_min=${lo}&elo_max=${hi}&speeds=${speeds}&source=${src}` +
      `&hist_fen=${encodeURIComponent(state.startFen)}&hist_moves=${histMoves}&opponent=${encodeURIComponent(opp)}`);
    if (my !== statsToken) return;
    const rows = {};
    const add = (src, list) => (list || []).forEach((m) => {
      rows[m.uci] = rows[m.uci] || { uci: m.uci, san: m.san };
      rows[m.uci][src] = m;
    });
    add("maia", r.maia);
    add("local", r.local && r.local.moves);
    add("lichess", r.lichess && r.lichess.moves);
    const op = r.opponent && r.opponent.reached ? r.opponent : null;
    if (op) add("opp", op.moves);
    const lt = r.local && r.local.available ? r.local.total : null;
    const lit = r.lichess && r.lichess.available ? r.lichess.total : null;
    const list = Object.values(rows).sort((a, b) =>
      ((b.opp || {}).games || 0) - ((a.opp || {}).games || 0) ||
      ((b.maia || {}).p || 0) - ((a.maia || {}).p || 0) ||
      ((b.lichess || {}).games || 0) - ((a.lichess || {}).games || 0)).slice(0, 14);
    const pct = (n, t) => (t ? Math.round((100 * n) / t) + "%" : "");
    const score = (m) => m && m.games ? Math.round((100 * (m.white + m.draws / 2)) / m.games) + "%" : "";
    const model = r.human_model || "Maia";
    tbl.innerHTML = `<tr><th>Ruch</th><th title="${model}: jak często człowiek z tego zakresu zagra ten ruch">${model.replace(/ \(.*\)/, "")}</th>
      <th title="Lokalna baza (OTB), wybrany zakres rankingu">Baza ${lt !== null ? "(" + lt.toLocaleString("pl") + ")" : ""}</th>
      <th title="${srcTitle}">${srcName} ${lit !== null ? "(" + lit.toLocaleString("pl") + ")" : ""}</th>
      ${op ? `<th title="Partie przeciwnika (${esc(op.name)}) z tą pozycją; w nawiasie jego wynik">${esc(op.name)} (${op.reached})</th>` : ""}
      <th title="Wynik białych w partiach Lichess">Białe</th></tr>` +
      list.map((m) => `<tr data-uci="${m.uci}">
        <td><b>${m.san}</b></td>
        <td><div class="bar" style="--w:${Math.round(((m.maia || {}).p || 0) * 100)}%"></div>${m.maia ? Math.round(m.maia.p * 100) + "%" : ""}</td>
        <td>${m.local ? m.local.games.toLocaleString("pl") + ` <small>${pct(m.local.games, lt)}</small>` : ""}</td>
        <td>${m.lichess ? m.lichess.games.toLocaleString("pl") + ` <small>${pct(m.lichess.games, lit)}</small>` : ""}</td>
        ${op ? `<td>${m.opp ? `<b>${m.opp.games}</b> <small>(${Math.round(m.opp.score * 100)}%)</small>` : ""}</td>` : ""}
        <td>${score(m.lichess)}</td></tr>`).join("");
    tbl.querySelectorAll("tr[data-uci]").forEach((tr) => {
      tr.onclick = () => { const u = tr.dataset.uci; userMove(u.slice(0, 2), u.slice(2, 4)); };
    });
    const notes = [];
    if (!r.local.available) notes.push("Baza lokalna: indeks jeszcze nie gotowy.");
    else if (r.local.beyond_index) notes.push("Baza lokalna: pozycja głębiej niż zindeksowane półruchy.");
    if (r.lichess_error) notes.push("Lichess: " + r.lichess_error);
    if (r.maia_error) notes.push(model + ": " + r.maia_error);
    if (/Maia-3/.test(model)) notes.push(r.maia_history ? `${model.replace(/ \(.*\)/, "")} widzi ${r.maia_history} poprzednich pozycji`
      : `${model.replace(/ \(.*\)/, "")} bez historii ruchów (pozycja z FEN) – w szukaniu przewidywania mogą być inne`);
    if (r.opponent && !r.opponent.reached) notes.push(`${r.opponent.name}: nie grał tej pozycji`);
    if (r.lichess && r.lichess.opening) notes.unshift(r.lichess.opening);
    $("#stats-note").textContent = notes.join(" · ");
  } catch (e) {
    if (my === statsToken) tbl.innerHTML = `<tr><td>Błąd: ${e.message}</td></tr>`;
  }
}

// ------------------------------------------------------------------ wyszukiwanie

function updateEvalLabel() {
  const white = $("#p-color").value === "white";
  $("#lbl-eval").firstChild.textContent = white
    ? "Akceptuję ocenę co najmniej (białe ≥) "
    : "Akceptuję ocenę co najwyżej (czarne: ≤) ";
  const v = +$("#p-eval").value;
  if (white && v > 0.5) $("#p-eval").value = -0.5;
  if (!white && v < -0.5) $("#p-eval").value = 1.0;
}

// Godzina "HH:MM" -> znacznik czasu (dziś, a jeśli już minęła - jutro).
function deadlineFrom(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  if (d.getTime() < Date.now() + 6 * 60000) d.setDate(d.getDate() + 1);
  return d.getTime() / 1000;
}

function updateModeUI() {
  const time = $("#p-mode").value === "time";
  $("#lbl-budget").classList.toggle("hidden", time);
  $("#lbl-deadline").classList.toggle("hidden", !time);
}

function searchParams() {
  const color = $("#p-color").value;
  const v = +$("#p-eval").value;
  const time = $("#p-mode").value === "time";
  return {
    fen: curFen(),
    color,
    min_eval: Math.round((color === "white" ? v : -v) * 100),
    max_drop: Math.round(+$("#p-drop").value * 100),
    elo_min: +$("#p-elo-min").value,
    elo_max: +$("#p-elo-max").value,
    max_plies: +$("#p-plies").value,
    budget: time ? 0 : +$("#p-budget").value,
    deadline: time ? deadlineFrom($("#p-deadline").value || "15:00") : 0,
    depth: +$("#p-depth").value,
    depth_min: Math.min(+$("#p-depth-min").value, +$("#p-depth").value),
    verify_depth: +$("#p-vdepth").value,
    my_candidates: +$("#p-cands").value,
    opp_max_replies: +$("#p-replies").value,
    opp_coverage: +$("#p-cover").value,
    use_lichess: $("#p-lichess").checked,
    lichess_source: $("#p-lsource").value,
    use_local_db: $("#p-local").checked,
    speeds: $$(".p-speed").filter((c) => c.checked).map((c) => c.value),
    novelty_max_loss: Math.round(+$("#p-nov-loss").value * 100),
    novelty_max_games: +$("#p-nov-games").value,
    novelty_max_share: +$("#p-nov-share").value / 100,
    theory_min_games: +$("#p-theory").value,
    hard_error: +$("#p-hard-err").value / 100,
    obvious_p: +$("#p-obvious").value / 100,
    min_reach: +$("#p-min-reach").value / 100,
    family_quota: +$("#p-fam-quota").value,
    family_share: +$("#p-fam-share").value / 100,
    // droga od początku partii do tej pozycji (zapisywana razem z wynikami)
    pre_fen: state.startFen,
    pre_moves: state.moves.slice(0, state.cursor).map((m) => m.uci),
    opponent: $("#p-opponent").value,
  };
}

// Ustawienia formularza z parametrów wcześniejszego wyszukiwania (odwrotność searchParams).
function applyParams(p) {
  const set = (sel, v) => { if (v !== undefined && v !== null) $(sel).value = v; };
  set("#p-color", p.color);
  updateEvalLabel();
  set("#p-eval", ((p.color === "white" ? p.min_eval : -p.min_eval) / 100).toFixed(1));
  set("#p-drop", p.max_drop / 100);
  set("#p-elo-min", p.elo_min); set("#p-elo-max", p.elo_max);
  set("#p-plies", p.max_plies);
  if (p.budget) set("#p-budget", p.budget);
  if (p.deadline) {
    const d = new Date(p.deadline * 1000);
    set("#p-deadline", `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`);
  }
  set("#p-mode", p.deadline && p.deadline * 1000 > Date.now() + 6 * 60000 ? "time" : "budget");
  updateModeUI();
  set("#p-depth", p.depth); set("#p-vdepth", p.verify_depth);
  set("#p-depth-min", p.depth_min ? p.depth_min : p.depth);
  set("#p-cands", p.my_candidates); set("#p-replies", p.opp_max_replies); set("#p-cover", p.opp_coverage);
  $("#p-lichess").checked = p.use_lichess !== false;
  set("#p-lsource", p.lichess_source);
  $("#p-local").checked = p.use_local_db !== false;
  if (p.speeds) $$(".p-speed").forEach((c) => (c.checked = p.speeds.includes(c.value)));
  if (p.novelty_max_loss !== undefined) set("#p-nov-loss", p.novelty_max_loss / 100);
  set("#p-nov-games", p.novelty_max_games); set("#p-theory", p.theory_min_games);
  if (p.novelty_max_share !== undefined) set("#p-nov-share", +(p.novelty_max_share * 100).toFixed(2));
  if (p.hard_error !== undefined) set("#p-hard-err", Math.round(p.hard_error * 100));
  if (p.obvious_p !== undefined) set("#p-obvious", Math.round(p.obvious_p * 100));
  if (p.min_reach !== undefined) set("#p-min-reach", +(p.min_reach * 100).toFixed(2));
  set("#p-fam-quota", p.family_quota);
  if (p.family_share !== undefined) set("#p-fam-share", Math.round(p.family_share * 100));
  if (p.opponent !== undefined && [...$("#p-opponent").options].some((o) => o.value === p.opponent)) set("#p-opponent", p.opponent);
  state.orientation = p.color;
}

function saveHistory(jobId) {
  const hist = { startFen: state.startFen, moves: state.moves.slice(0, state.cursor).map((m) => m.uci) };
  localStorage.setItem("hist-" + jobId, JSON.stringify(hist));
}

async function startSearch() {
  try {
    const info = await api("/api/search", { method: "POST", body: { params: searchParams() } });
    saveHistory(info.id);
    if (state.job) {  // już coś liczy - nowe czeka w kolejce
      refreshQueue();
      return;
    }
    state.job = info.id;
    pollJob();
  } catch (e) {
    $("#job").textContent = "Błąd: " + e.message;
  }
}

function fmtTime(s) {
  if (s === null || s === undefined) return "?";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h} h ${m} min`;
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function setJobButtons(running, partial) {
  $("#btn-cancel").classList.toggle("hidden", !running);
  $("#btn-finish").classList.toggle("hidden", !running);
  $("#btn-partial").classList.toggle("hidden", !(running && partial));
  $("#btn-search").textContent = running ? "Dodaj do kolejki" : "Szukaj";
}

async function pollJob() {
  if (!state.job) return;
  let info;
  try { info = await api("/api/search/" + state.job); } catch (e) { $("#job").textContent = e.message; return; }
  if (info.status === "running" || info.status === "queued") {
    let pct = info.total ? Math.round((100 * info.progress) / info.total) : 0;
    let line;
    if (info.status === "queued") line = "w kolejce…";
    else if (info.deadline) {
      const total = info.deadline - (Date.now() / 1000 - info.elapsed);
      pct = Math.round((100 * info.elapsed) / Math.max(1, total));
      line = `do ${fmtClock(info.deadline)} · ${info.progress} pozycji · ${fmtTime(info.elapsed)} · zostało ~${fmtTime(info.eta)}`;
    } else line = `${info.progress}/${info.total} pozycji · ${fmtTime(info.elapsed)} · zostało ~${fmtTime(info.eta)}`;
    if (info.finishing) line += " · kończę i weryfikuję…";
    $("#job").innerHTML = `<div class="progress"><div style="width:${Math.min(100, pct)}%"></div></div>
      ${line}<div class="ellipsis">${esc(info.message || "")}</div>`;
    setJobButtons(true, info.partial_available);
    refreshQueue();
    setTimeout(pollJob, 1500);
    return;
  }
  setJobButtons(false, false);
  const jobId = state.job;
  state.job = null;
  if (info.status === "error") { $("#job").textContent = "Błąd: " + info.error; return; }
  $("#job").textContent = (info.status === "cancelled" ? "Przerwano - wyniki częściowe. " : "Gotowe. ") +
    `Czas: ${fmtTime(info.elapsed)}`;
  if (info.result) showResult(info.result);
  await loadSaved(jobId);
}

// Kolejka: czekające wyszukiwania z przyciskiem anulowania.
async function refreshQueue() {
  let q, cal;
  try { ({ queued: q, calibration: cal } = await api("/api/queue")); } catch (_) { return; }
  const calHtml = cal ? `<span class="qitem">${esc(cal.message || "Kalibracja modelu…")} ` +
    `<button class="link" data-qcancel="${esc(cal.id)}" title="Przerwij kalibrację (zebrane pozycje zostaną zapisane)">✕</button></span> ` : "";
  $("#job-queue").innerHTML = calHtml + (q.length ? "W kolejce: " + q.map((j, i) =>
    `<span class="qitem">${i + 1}. ${j.kind === "calibration" ? "kalibracja modelu" : j.color === "white" ? "białe" : "czarne"} · ${j.elo} · ` +
    `${j.deadline ? "do " + fmtClock(j.deadline) : j.budget + " poz."} ` +
    `<button class="link" data-qcancel="${esc(j.id)}" title="Usuń z kolejki">✕</button></span>`).join(" ") : "");
}

$("#job-queue").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-qcancel]");
  if (!b) return;
  await api(`/api/search/${b.dataset.qcancel}/cancel`, { method: "POST" });
  refreshQueue();
});

// ------------------------------------------------------------------ przeciwnik

async function loadOpponents(select) {
  try {
    const r = await api("/api/opponents");
    const cur = select || $("#p-opponent").value;
    $("#p-opponent").innerHTML = '<option value="">— nikogo —</option>' + r.books.map((b) =>
      `<option value="${esc(b.key)}">${esc(b.name)} · ${esc(b.source_label)} · ${b.games} partii (${b.white} b., ${b.black} cz.)</option>`).join("");
    if (cur && r.books.some((b) => b.key === cur)) $("#p-opponent").value = cur;
  } catch (_) {}
}

async function pollOpponent() {
  let s;
  try { s = await api("/api/opponent/status"); } catch (_) { return; }
  const el = $("#opp-status");
  if (s.running) {
    el.innerHTML = `${esc(s.message)}${s.progress != null ? `<div class="progress"><div style="width:${Math.round(s.progress * 100)}%"></div></div>` : ""}`;
    $("#btn-opp-load").disabled = true;
    setTimeout(pollOpponent, 1500);
    return;
  }
  $("#btn-opp-load").disabled = false;
  if (s.error) el.innerHTML = `<span class="badtxt">${esc(s.error)}</span>`;
  else if (s.message) el.textContent = s.message;
  await loadOpponents(s.last && s.last.key);
  if (s.last) { $("#p-opponent").value = s.last.key; loadStats(curFen()); }
}

// ------------------------------------------------------------------ wyniki

function pctStr(x) { return (x * 100).toFixed(x < 0.1 ? 1 : 0) + "%"; }

function gamesStr(local, lichess) {
  const parts = [];
  if (local !== null && local !== undefined) parts.push(`baza ${local.toLocaleString("pl")}`);
  if (lichess !== null && lichess !== undefined) parts.push(`lichess ${lichess.toLocaleString("pl")}`);
  return parts.join(" · ");
}

function showResult(res) {
  const fresh = !state.result || state.result.id !== res.id;
  state.result = res;
  if (fresh) {
    state.marks = {};
    state.real = null;
    loadMarks(res);
    loadReal(res);
  }
  renderResults();
}

function renderResults() {
  const res = state.result;
  if (!res) return;
  const tabs = { traps: res.traps || [], hard: res.hard_lines || [], nov: res.novelties || [] };
  $('.tab[data-tab="traps"]').textContent = `Pułapki (${tabs.traps.length})`;
  $('.tab[data-tab="hard"]').textContent = `Trudne linie (${tabs.hard.length})`;
  $('.tab[data-tab="nov"]').textContent = `Nowinki (${tabs.nov.length})`;
  renderTraps(res);
  renderHard(res);
  renderNovelties(res);
  renderTree(res);
  $("#btn-close-res").classList.remove("hidden");
  syncResults();
}

// Zamknięcie wyników (np. otwartego zapisanego wyszukiwania) - szachownica zostaje,
// można dalej ustawiać pozycję i szukać od nowa.
function closeResult() {
  state.result = null;
  $('.tab[data-tab="traps"]').textContent = "Pułapki";
  $('.tab[data-tab="hard"]').textContent = "Trudne linie";
  $('.tab[data-tab="nov"]').textContent = "Nowinki";
  for (const t of ["traps", "hard", "nov", "tree"]) $("#tab-" + t).innerHTML = "";
  $("#saved").value = "";
  $("#btn-close-res").classList.add("hidden");
  if (!state.job) $("#job").textContent = "";
  state.arrows = [];
  board.set({ arrows: [] });
  syncResults();
}

// ------------------------------------------------------------------ pozycja na szachownicy <-> wyniki

// klucz pozycji: układ, strona na ruchu, roszady, bicie w przelocie (bez liczników ruchów)
function posKey(fen) { return fen.split(" ").slice(0, 4).join(" "); }

// Gdy pozycja na szachownicy jest w wynikach (pułapka, moment trudnej linii, nowinka,
// węzeł drzewa) - zaznacz te karty, przewiń do nich i pokaż strzałki jak po kliknięciu.
function syncResults() {
  const res = state.result;
  // karta kliknięta przed chwilą ma pierwszeństwo, gdy kilka kart pasuje do pozycji
  const prev = (sel) => { const e = $(sel); return e ? +e.dataset.i : -1; };
  const prevHard = prev("#tab-hard .hline.sel"), prevNov = prev("#tab-nov .nov.sel");
  const preferFirst = (arr, i) => (i >= 0 && i < arr.length ? [i, ...arr.keys()] : [...arr.keys()]);
  $$(".trap.sel, .hline.sel, .nov.sel").forEach((e) => e.classList.remove("sel"));
  $$(".moment.cur, #tab-tree summary.cur").forEach((e) => e.classList.remove("cur"));
  $$(".tab").forEach((t) => t.classList.remove("has-match"));
  state.matches = {};
  if (!res) return;
  const key = posKey(curFen());
  const found = {};

  const ti = (res.traps || []).findIndex((t) => posKey(t.fen) === key);
  if (ti >= 0) {
    const t = res.traps[ti];
    found.traps = { el: $(`#tab-traps .trap[data-i="${ti}"]`), arrows: arrowsFor(t.good, t.bad) };
  }
  const hard = res.hard_lines || [];
  preferFirst(hard, prevHard).some((i) => {
    const h = hard[i];
    const j = h.moments.findIndex((m) => posKey(m.fen) === key);
    if (j < 0) return false;
    const card = $(`#tab-hard .hline[data-i="${i}"]`);
    const li = card && card.querySelector(`[data-sub="${j}"]`);
    if (li) li.classList.add("cur");
    found.hard = { el: card, focus: li || card, arrows: arrowsFor(h.moments[j].good, h.moments[j].bad) };
    return true;
  });
  const novs = res.novelties || [];
  const order = preferFirst(novs, prevNov);
  let ni = order.find((i) => posKey(novs[i].fen) === key) ?? -1;
  let novArrows = [];
  if (ni < 0) {  // pozycja PRZED nowinką - podpowiedz ruch
    ni = order.find((i) => posKey(novs[i].parent_fen) === key) ?? -1;
    if (ni >= 0) {
      const u = novs[ni].line_uci.at(-1);
      novArrows = [{ from: u.slice(0, 2), to: u.slice(2, 4), color: "#2f7bd9", opacity: 0.8 }];
    }
  }
  if (ni >= 0) found.nov = { el: $(`#tab-nov .nov[data-i="${ni}"]`), arrows: novArrows };
  const sum = [...$$("#tab-tree summary")].find((s) => s.dataset.key === key);
  if (sum) {
    // rozwiń gałęzie prowadzące do tego węzła
    let d = sum.parentElement.parentElement.closest("details");
    while (d) { d.open = true; d = d.parentElement.closest("details"); }
    found.tree = { el: sum, arrows: [] };
  }

  for (const [tab, f] of Object.entries(found)) {
    if (!f.el) continue;
    if (tab === "tree") f.el.classList.add("cur"); else f.el.classList.add("sel");
    for (let d = f.el.closest("details.fam"); d; d = d.parentElement.closest("details.fam")) d.open = true;
    $(`.tab[data-tab="${tab}"]`).classList.add("has-match");
  }
  state.matches = found;
  scrollToMatch();
  // strzałki jak po kliknięciu - tylko gdy nic innego ich nie ustawiło
  if (!state.arrows.length) {
    const active = ($(".tab.active") || {}).dataset?.tab;
    const pick = [found[active], found.traps, found.hard, found.nov].find((f) => f && f.arrows && f.arrows.length);
    if (pick) {
      state.arrows = pick.arrows;
      board.set({ arrows: state.arrows });
    }
  }
}

function scrollToMatch() {
  const active = ($(".tab.active") || {}).dataset?.tab;
  const f = (state.matches || {})[active];
  const el = f && (f.focus || f.el);
  if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// numer ruchu dla ruchu zagranego w pozycji `fen`
function mvNo(fen, san) {
  const f = fen.split(" ");
  return f[5] + (f[1] === "w" ? "." : "…") + san;
}

function arrowsFor(good, bad) {
  const out = [];
  (bad || []).slice(0, 3).forEach((d) => out.push({ from: d.uci.slice(0, 2), to: d.uci.slice(2, 4), color: "#c0392b", opacity: Math.min(0.9, 0.35 + d.p) }));
  (good || []).slice(0, 2).forEach((d) => out.push({ from: d.uci.slice(0, 2), to: d.uci.slice(2, 4), color: "#15781b", opacity: 0.8 }));
  return out;
}

function bindCards(el, cls, onPick) {
  el.querySelectorAll("." + cls).forEach((d) => {
    d.onclick = (e) => {
      if (e.target.closest("[data-sub],[data-act]")) return;
      el.querySelectorAll("." + cls).forEach((x) => x.classList.remove("sel"));
      d.classList.add("sel");
      onPick(+d.dataset.i, null);
    };
    d.querySelectorAll("[data-sub]").forEach((s) => (s.onclick = (e) => {
      if (e.target.closest("[data-act]")) return;  // przyciski w środku obsługuje bindActions
      e.stopPropagation();
      el.querySelectorAll("." + cls).forEach((x) => x.classList.remove("sel"));
      d.classList.add("sel");
      onPick(+d.dataset.i, +s.dataset.sub);
    }));
  });
}

function rangeStr(res) { return `${res.params.elo_min}–${res.params.elo_max}`; }

// ------------------------------------------------------------------ rodziny wariantów
// Wyniki z tym samym początkiem wariantu idą obok siebie. Serwer zwraca je od najlepszych,
// więc kolejność rodzin (i wariantów w rodzinie) wyznacza najlepszy wynik w danej rodzinie.

function groupByFamily() { return localStorage.getItem("res-order") !== "score"; }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// półruch i z listy SAN od pozycji fen; force = numer także przed ruchem czarnych ("7…Nf6")
function plyText(fen, sans, i, force) {
  const parts = fen.split(" ");
  const ply = i + (parts[1] === "b" ? 1 : 0);
  const no = (+parts[5] || 1) + Math.floor(ply / 2);
  if (ply % 2 === 0) return `${no}.${sans[i]}`;
  return (force ? `${no}…` : "") + sans[i];
}

// Wariant z numerami ruchów; pierwsze `dim` półruchów (wspólne z rodziną) wyszarzone.
function lineHtml(fen, sans, dim = 0) {
  dim = Math.max(0, Math.min(dim, sans.length - 1));
  const a = sans.slice(0, dim).map((_, i) => plyText(fen, sans, i, i === 0)).join(" ");
  const b = sans.slice(dim).map((_, j) => plyText(fen, sans, dim + j, j === 0)).join(" ");
  return (a ? `<span class="shared">${a}</span> ` : "") + b;
}

// zwykły tekst wariantu od półruchu `from`
function movesText(fen, sans, from = 0) {
  return sans.slice(from).map((_, j) => plyText(fen, sans, from + j, j === 0)).join(" ");
}

function familyTree(items, keep = () => true) {
  const mk = (uci, san) => ({ uci, san, items: [], kids: new Map(), best: Infinity, count: 0 });
  const root = mk([], []);
  items.forEach((it, i) => {
    if (!keep(i)) return;
    let n = root;
    n.best = Math.min(n.best, i); n.count++;
    it.line_uci.forEach((u, d) => {
      if (!n.kids.has(u)) n.kids.set(u, mk(it.line_uci.slice(0, d + 1), it.line.slice(0, d + 1)));
      n = n.kids.get(u);
      n.best = Math.min(n.best, i); n.count++;
    });
    n.items.push(i);
  });
  // łańcuchy bez rozgałęzień zwijamy w jeden węzeł (np. wspólne "6...Bb6 7.b5 Na5")
  const compress = (n) => {
    while (!n.items.length && n.kids.size === 1) {
      const c = n.kids.values().next().value;
      Object.assign(n, { uci: c.uci, san: c.san, items: c.items, kids: c.kids });
    }
    n.kids.forEach(compress);
    return n;
  };
  return compress(root);
}

const kidsOf = (n) => [...n.kids.values()].sort((a, b) => a.best - b.best);

// cardFn(i, num, dim) -> HTML karty; i = indeks w wynikach serwera, num = numer na liście
function renderFamilies(items, rootFen, unitFn, cardFn, keep = () => true) {
  let num = 0;
  const kept = items.map((_, i) => i).filter(keep);
  if (!kept.length) return '<p class="muted">Nic nie pasuje do wybranego filtra.</p>';
  if (!groupByFamily()) return kept.map((i) => cardFn(i, ++num, 0)).join("");
  const root = familyTree(items, keep);
  const MAX_LEVELS = 3;  // głębiej - ta sama kolejność, ale już bez kolejnych nagłówków
  const deeper = (uci) => `<button type="button" class="link deeper" data-act="deeper" data-line="${uci.join(",")}"
    title="Nowe wyszukiwanie od tej pozycji, z tymi samymi ustawieniami">szukaj głębiej</button>`;
  const walk = (n, dim, level) => {
    let html = n.items.map((i) => cardFn(i, ++num, dim)).join("");
    const kids = kidsOf(n);
    for (const c of kids) {
      // nagłówek tylko tam, gdzie wariant naprawdę się rozgałęzia
      if (c.count >= 2 && kids.length >= 2 && level < MAX_LEVELS) {
        html += `<details class="fam" open><summary><span class="fam-line">${lineHtml(rootFen, c.san, dim)}</span>
          <span class="fam-count">${c.count} ${unitFn(c.count)}</span> ${deeper(c.uci)}</summary>
          <div class="fam-body">${walk(c, c.uci.length, level + 1)}</div></details>`;
      } else html += walk(c, dim, level);
    }
    return html;
  };
  const head = root.uci.length && kept.length > 1
    ? `<div class="fam-root muted small">Wszystkie zaczynają się od <b>${lineHtml(rootFen, root.san)}</b> ${deeper(root.uci)}</div>` : "";
  return head + walk(root, root.uci.length, 0);
}

// Rozdziały eksportu = rodziny najwyższego poziomu (każda osobno), z tym samym filtrem co na liście.
function exportGroups(kind, perFamily, filter) {
  const res = state.result;
  const items = familyItems(kind);
  const keep = keepFn(kind, filter);
  const kept = items.map((_, i) => i).filter(keep);
  const rootFen = res.params.fen;
  const name = (sans) => (sans.length ? movesText(rootFen, sans) : "Pozycja startowa");
  if (!perFamily || !kept.length) {
    const root = familyTree(items, keep);
    return [{ name: `ChessPrep: ${name(root.san)}`, items: order(root) }];
  }
  const root = familyTree(items, keep);
  const groups = [];
  if (root.items.length) groups.push({ name: name(root.san), items: root.items.slice() });
  for (const c of kidsOf(root)) groups.push({ name: `${name(c.san)} (${c.count})`, items: order(c) });
  return groups;
  function order(n) { return n.items.concat(...kidsOf(n).map(order)); }
}

function plural(n, one, few, many) {
  if (n === 1) return one;
  const d = n % 10, dd = n % 100;
  return d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many;
}

// ------------------------------------------------------------------ filtry, gwiazdki, notatki

const FILTERS = {
  traps: [["all", "wszystkie"], ["main", "bez rzadkiego dojścia"], ["star", "★ moje"], ["verified", "zweryfikowane"],
    ["hidden", "ukryte"], ["sure", "pewne (z partii albo ukryte)"], ["games", "z partiami w bazach"], ["opp", "przeciwnik tu był"]],
  hard: [["all", "wszystkie"], ["star", "★ moje"], ["verified", "zweryfikowane"]],
  nov: [["all", "wszystkie"], ["novelty", "tylko N/TN (bez rzadkich wariantów)"], ["star", "★ moje"],
    ["verified", "zweryfikowane"], ["tn", "TN (nigdy nie grane)"]],
};

function resFilter(kind) { return localStorage.getItem("res-filter-" + kind) || "all"; }

function familyItems(kind) {
  const res = state.result;
  if (kind === "traps") return res.traps || [];
  if (kind === "hard") return res.hard_lines || [];
  // nowinki grupujemy po pozycji PRZED nowinką - kilka nowinek w tym samym miejscu idzie razem
  return (res.novelties || []).map((n) => ({ line: n.line.slice(0, -1), line_uci: n.line_uci.slice(0, -1) }));
}

function rawItems(kind) {
  const res = state.result;
  return kind === "traps" ? res.traps || [] : kind === "hard" ? res.hard_lines || [] : res.novelties || [];
}

function keepFn(kind, f = resFilter(kind)) {
  const list = rawItems(kind);
  return (i) => {
    const it = list[i];
    if (f === "star") return !!mark(kind, it).star;
    if (f === "verified") return it.verified === true;
    if (f === "hidden") return !!it.hidden;
    if (f === "sure") return it.confidence ? it.confidence === "games" || it.confidence === "hidden" : !!it.hidden || (it.games_total || 0) >= 30;
    if (f === "tn") return it.games === 0;
    if (f === "main") return !it.rare;
    if (f === "games") return !!(it.local_games || it.lichess_games);
    if (f === "opp") return !!(it.opponent && it.opponent.reached);
    if (f === "novelty") return !it.rare_only;
    return true;
  };
}

function markKey(kind, it) { return kind + ":" + it.line_uci.join(","); }
function mark(kind, it) { return (state.marks || {})[markKey(kind, it)] || {}; }

async function loadMarks(res) {
  try { state.marks = await api(`/api/search/${res.id}/marks`); } catch (_) { state.marks = {}; }
  if (state.result && state.result.id === res.id && Object.keys(state.marks).length) renderResults();
}

async function saveMark(kind, it, patch) {
  state.marks = await api(`/api/search/${state.result.id}/marks`, { method: "POST", body: { key: markKey(kind, it), ...patch } });
}

function markHtml(kind, it) {
  const m = mark(kind, it);
  return `<span class="marks"><button type="button" class="icon star ${m.star ? "on" : ""}" data-act="star"
      title="Do repertuaru (★) - filtr „★ moje” pokaże tylko oznaczone">${m.star ? "★" : "☆"}</button><button type="button"
      class="icon" data-act="note" title="Notatka">✎</button></span>`;
}

function noteHtml(kind, it) {
  const n = mark(kind, it).note;
  return `<div class="note ${n ? "" : "hidden"}" data-act="note" title="Kliknij, żeby zmienić">${esc(n)}</div>`;
}

function toolbar(kind) {
  const fam = groupByFamily();
  const f = resFilter(kind);
  return `<div class="res-tools small">
    <span>Kolejność:
      <button type="button" class="link ${fam ? "on" : ""}" data-order="family" title="Warianty z tym samym początkiem obok siebie">rodzinami</button> ·
      <button type="button" class="link ${fam ? "" : "on"}" data-order="score" title="Od najlepszego wyniku, bez grupowania">od najlepszych</button>
      ${fam ? ' · <button type="button" class="link" data-fold="1" title="Zwiń / rozwiń wszystkie rodziny">zwiń/rozwiń</button>' : ""}</span>
    <span class="right">Pokaż <select data-filter="${kind}">${FILTERS[kind].map(([v, l]) =>
      `<option value="${v}" ${v === f ? "selected" : ""}>${l}</option>`).join("")}</select></span></div>`;
}

function bindToolbar(el) {
  el.querySelectorAll("[data-order]").forEach((b) => (b.onclick = () => {
    localStorage.setItem("res-order", b.dataset.order);
    renderResults();
  }));
  el.querySelectorAll("[data-fold]").forEach((b) => (b.onclick = () => {
    const fams = el.querySelectorAll("details.fam");
    const close = [...fams].some((d) => d.open);
    fams.forEach((d) => (d.open = !close));
  }));
  el.querySelectorAll("[data-filter]").forEach((s) => (s.onchange = () => {
    localStorage.setItem("res-filter-" + s.dataset.filter, s.value);
    renderResults();
  }));
}

// ------------------------------------------------------------------ kara za błąd

function refHtml(fen, d, k, m = null) {
  const attrs = `data-act="ref" data-k="${k}"${m !== null ? ` data-m="${m}"` : ""}`;
  if (d.pv && d.pv.san && d.pv.san.length > 1) {
    return `<span class="refute" ${attrs} title="Pokaż karę na szachownicy (dalej strzałką →)">kara: ${movesText(fen, d.pv.san, 1)}</span>`;
  }
  return `<button type="button" class="link refute-btn" ${attrs} title="Silnik policzy, jak ukarać ten ruch">jak ukarać?</button>`;
}

async function showRefutation(it, a) {
  const mi = a.dataset.m !== undefined ? +a.dataset.m : null;
  const pos = mi !== null ? it.moments[mi] : it;   // pozycja z polami fen, line_uci, bad
  const k = +a.dataset.k;
  const d = pos.bad[k];
  if (!d.pv || !d.pv.uci || d.pv.uci.length < 2) {
    a.textContent = "silnik liczy…";
    try {
      const r = await api("/api/refute", { method: "POST", body: { fen: pos.fen, uci: d.uci, depth: Math.max(16, state.result.params.depth || 16) } });
      d.pv = r.pv;
    } catch (err) { a.textContent = "błąd: " + err.message; return; }
    if (!d.pv) { a.textContent = "brak linii"; return; }
    a.outerHTML = refHtml(pos.fen, d, k, mi);
  }
  const pv = d.pv.uci;
  const arrows = [{ from: pv[1].slice(0, 2), to: pv[1].slice(2, 4), color: "#15781b", opacity: 0.85 }];
  // szachownica: pozycja po złym ruchu, strzałka = jak ukarać; dalej strzałką →
  await loadLine(state.result, pos.line_uci.concat(pv), arrows, pos.line_uci.length + 1);
}

// ------------------------------------------------------------------ prawdziwe partie

async function loadReal(res) {
  if (!(res.traps || []).length) return;
  try {
    const r = await api(`/api/search/${res.id}/real`);
    if (state.result && state.result.id === res.id) { state.real = r; fillReal(); }
  } catch (_) {
    state.real = { traps: [], failed: true };
    fillReal();
  }
}

function realHtml(i) {
  const R = state.real;
  if (!R) return '<span class="muted">sprawdzam w prawdziwych partiach…</span>';
  const r = R.traps && R.traps[i];
  if (!r || (!r.local && !r.lichess)) return `<span class="muted">${R.failed ? "nie udało się sprawdzić partii" : "brak partii z tą pozycją w bazach"}</span>`;
  const lname = { players: "lichess", masters: "masters", both: "lichess+masters" }[R.lichess_source] || "lichess";
  const ci = (c) => (c ? ` <span class="ci" title="95% przedział ufności">${pctStr(c[0])}–${pctStr(c[1])}</span>` : "");
  const src = (name, s) => {
    if (!s) return "";
    const after = s.score_after_bad != null && s.fell >= 3
      ? `, potem Twój wynik <b>${pctStr(s.score_after_bad)}</b>${ci(s.score_ci)}${s.score_overall != null ? ` <span class="muted">(ogólnie ${pctStr(s.score_overall)})</span>` : ""}` : "";
    const txt = `${name}: wpadło <b>${s.fell.toLocaleString("pl")}</b> z ${s.total.toLocaleString("pl")} (${pctStr(s.fell_pct)}${ci(s.fell_ci)})${after}`;
    return s.small ? `<span class="small-sample" title="Mało partii - liczby mało pewne">${txt} · mała próba</span>` : txt;
  };
  return "W partiach – " + [src("baza", r.local), src(lname, r.lichess)].filter(Boolean).join(" · ");
}

function reachStr(r) {
  if (r == null) return "?";
  if (r > 0 && r < 0.001) return "<0,1%";
  return pctStr(r);
}

function hiddenTag(t) {
  if (t.confidence === "shallow" && t.hidden_depth != null) {
    return `<span class="tag warn" title="Silnik widzi błąd już na głębokości ${t.hidden_depth}, a w pozycji prawie nie ma partii - odsetek błędów przewiduje model. Silni gracze rzadko przeoczają takie błędy (kalibracja to uwzględnia).">widać od razu (gł. ${t.hidden_depth})</span>`;
  }
  if (t.hidden && t.hidden_depth == null && (t.confidence || t.hidden_max)) {
    const mx = t.hidden_max || 8;
    return `<span class="tag hid" title="Silnik nie widzi błędu do głębokości ${mx} - człowiekowi łatwo to przeoczyć">ukryta (gł. &gt; ${mx})</span>`;
  }
  if (t.hidden_depth != null) {
    return t.hidden
      ? `<span class="tag hid" title="Silnik widzi, że zły ruch jest błędem dopiero od głębokości ${t.hidden_depth} - człowiekowi łatwo to przeoczyć">ukryta (gł. ${t.hidden_depth})</span>`
      : `<span class="muted small" title="Silnik widzi błąd już na głębokości ${t.hidden_depth}">widać od gł. ${t.hidden_depth}</span>`;
  }
  return t.hidden ? '<span class="tag hid" title="Silnik na małej głębokości też wybrałby zły ruch">ukryta</span>' : "";
}

function oppHtml(o, res) {
  if (!o || !res.opponent) return "";
  const name = esc(res.opponent.name);
  if (!o.reached) return "";
  const moves = (o.moves || []).map((m) =>
    `<span class="${m.bad ? "badmv" : ""}">${m.san} ${m.n}×</span>`).join(", ");
  const fell = o.fell ? ` – <b>wpadł ${o.fell}×</b>` : "";
  return `<div class="small opp-info">${name} był tu <b>${o.reached}×</b>${fell}: ${moves}</div>`;
}

function statsLine(res) {
  const st = res.stats || {};
  const parts = [`Przeanalizowano ${res.analysed} pozycji`];
  if (st.transpositions) parts.push(`${st.transpositions} transpozycji połączonych`);
  const v = st.verified;
  if (v && v.planned) {
    const pl = v.planned;
    parts.push(`${v.interrupted ? '<b class="warntxt">weryfikacja przerwana (koniec czasu)</b>' : "zweryfikowano"}: pułapki ${v.traps}/${pl.traps}, linie ${v.lines}/${pl.lines}, nowinki ${v.novelties}/${pl.novelties}` +
      (v.cut ? `, odcięto ${v.cut} ${plural(v.cut, "pozycję", "pozycje", "pozycji")} po naszych ruchach` : ""));
  } else if (v) parts.push(`zweryfikowano ${v.traps} pułapek, ${v.lines} linii i ${v.novelties} nowinek`);
  const cal = st.calibration;
  if (cal && cal.kind) {
    const what = cal.kind === "measured" ? `pomiar na bazie (${cal.measured.positions} pozycji, ${esc(cal.measured.elo || "")})`
      : cal.kind === "search" ? `krzywa startowa + ${cal.positions} pozycji z partii z wyszukiwań`
      : "krzywa startowa";
    parts.push(`kalibracja: ${what}` + (cal.kind !== "measured"
      ? ' <span class="muted" title="Ustawienia (⚙) → Kalibruj model: pomiar na partiach z Twojej bazy dla tego modelu i rankingu - dokładniejsza poprawka odsetka błędów">(dokładniej: ⚙ → Kalibruj model)</span>' : ""));
  }
  const bs = st.budget_split;
  if (bs && bs.length > 1) {
    parts.push("budżet: " + bs.slice(0, 5).map((b) =>
      `<span title="${pctStr(b.p)} partii, ${b.analysed} przeanalizowanych pozycji">${esc(b.san)} ${pctStr(b.p)}→${pctStr(b.share)}</span>`).join(", "));
  }
  if (res.human_model) parts.push(`model: ${esc(res.human_model)}`);
  if (res.opponent) parts.push(`przeciwnik: ${esc(res.opponent.name)} (${res.opponent.games} partii)`);
  return parts.join(" · ") + "." + (res.partial ? ' <b class="warntxt">Wyniki częściowe (bez weryfikacji).</b>' : "");
}

function fillReal() { $$("#tab-traps [data-real]").forEach((e) => (e.innerHTML = realHtml(+e.dataset.real))); }

// ------------------------------------------------------------------ szukaj głębiej

async function searchDeeper(line) {
  const res = state.result;
  applyParams(res.params);
  if (res.params.deadline && $("#p-mode").value !== "time" && !res.params.budget) $("#p-budget").value = 400;
  await loadLine(res, line ? line.split(",") : [], []);
  $("#job").textContent = "Nowe wyszukiwanie od wybranej rodziny…";
  $("#btn-search").scrollIntoView({ block: "nearest", behavior: "smooth" });
  startSearch();
}

// ------------------------------------------------------------------ akcje na kartach (wspólne)

function bindActions(el, kind) {
  el.onclick = async (e) => {
    const a = e.target.closest("[data-act]");
    if (!a || !el.contains(a)) return;
    e.preventDefault();
    e.stopPropagation();
    const act = a.dataset.act;
    if (act === "deeper") return searchDeeper(a.dataset.line);
    if (act === "noop") return;
    const card = a.closest("[data-i]");
    if (!card) return;
    const it = rawItems(kind)[+card.dataset.i];
    if (act === "star") {
      const star = !mark(kind, it).star;
      await saveMark(kind, it, { star });
      a.classList.toggle("on", star);
      a.textContent = star ? "★" : "☆";
      if (resFilter(kind) === "star") renderResults();
    } else if (act === "note") {
      if (card.querySelector(".note-edit")) { card.querySelector(".note-edit textarea").focus(); return; }
      card.querySelector(".trap-head").insertAdjacentHTML("afterend", `<div class="note-edit" data-act="noop">
        <textarea rows="2" placeholder="Twoja notatka (np. kiedy to zagrać, co zapamiętać)…">${esc(mark(kind, it).note || "")}</textarea>
        <div class="row"><button type="button" class="small primary" data-act="note-save">Zapisz</button>
        <button type="button" class="small ghost" data-act="note-cancel">Anuluj</button></div></div>`);
      card.querySelector(".note-edit textarea").focus();
    } else if (act === "note-save" || act === "note-cancel") {
      const ed = card.querySelector(".note-edit");
      if (act === "note-save") {
        const note = ed.querySelector("textarea").value.trim();
        await saveMark(kind, it, { note });
        const div = card.querySelector(".note");
        div.textContent = note;
        div.classList.toggle("hidden", !note);
      }
      ed.remove();
    } else if (act === "ref") {
      showRefutation(it, a);
    }
  };
}

// ------------------------------------------------------------------ karty wyników

function renderTraps(res) {
  const color = res.params.color;
  const rootFen = res.params.fen;
  const el = $("#tab-traps");
  const traps = res.traps || [];
  const oppName = color === "white" ? "czarnych" : "białych";
  const help = `<details class="help"><summary>Jak to czytać?</summary>
    <p>Pułapka to pozycja (po Twoim ruchu), w której <b>przeciwnik często gra naturalny, ale zły ruch</b>.
    „Naturalny” = tak zagrałby typowy gracz ${rangeStr(res)} (Maia + statystyki partii), „zły” = silnik pokazuje
    spadek jego szansy wygranej o co najmniej 10% (jak „mistake” na Lichess).</p>
    <p><b>Dojście</b> – jak często przeciwnik sam doprowadzi do tej pozycji swoimi typowymi ruchami.
    <b>Błąd</b> – ilu ludzi zagra tu zły ruch. <b>Siła</b> – średnio ile % szansy wygranej przeciwnik tu oddaje.
    <span class="tag hid">ukryta</span> – nawet silnik na małej głębokości wybrałby zły ruch (refutacja jest głęboka).
    <span class="tag">jedyny ruch</span> – tylko jeden ruch nie jest błędem. Oceny zawsze z perspektywy białych.</p>
    <p><b>Kara</b> – jak silnik wykorzystuje błąd (kliknij, żeby zobaczyć na szachownicy, dalej strzałką →).
    <b>W partiach</b> – ilu graczy z wybranego rankingu naprawdę wpadło w tę pułapkę w bazie / na Lichess i jaki potem
    był Twój wynik (dla porównania: wynik ze wszystkich partii z tą pozycją), z 95% przedziałem ufności. ★ oznacza pułapkę do repertuaru.</p>
    <p><b>Zysk</b> – o ile rośnie Twój oczekiwany wynik w tej pozycji dzięki ludzkim błędom (w porównaniu z najlepszą obroną).
    <b>Na 100 partii</b> – to samo w punktach na 100 partii od pozycji startowej (uwzględnia dojście).
    <b>Koszt</b> – ile oceny oddajesz swoimi ruchami w linii względem najlepszych ruchów silnika.
    <b>Ukrycie</b> – od jakiej głębokości silnik widzi, że zły ruch jest błędem (im głębiej, tym trudniej człowiekowi).
    <span class="tag soft">bez partii</span> / <span class="tag soft">mało partii</span> – przewidywany błąd pochodzi (głównie) z modelu, więc część z modelu jest skalibrowana na prawdziwych partiach
    (model przeszacowuje wysokie odsetki błędów). Pułapki z bardzo małym dojściem są na końcu listy.</p></details>`;
  if (!traps.length) {
    el.innerHTML = help + `<p class="muted">Brak pułapek. Spróbuj większej liczby pozycji, innego zakresu rankingu
      albo łagodniejszego progu oceny.</p>`;
    return;
  }
  el.innerHTML = help + `<p class="muted small">${statsLine(res)} Najlepsze = dojście × siła (oczekiwana strata przeciwnika na partię).</p>` +
    toolbar("traps") + renderFamilies(traps, rootFen, (n) => plural(n, "pułapka", "pułapki", "pułapek"), (i, num, dim) => {
      const t = traps[i];
      const line = t.line.length ? lineHtml(rootFen, t.line, dim) : "(od razu w tej pozycji)";
      const ev = (d) => fmtEval(myToWhite(d.cp, color));
      const bad = t.bad.slice(0, 3).map((d, k) =>
        `<li><b class="badmv">${mvNo(t.fen, d.san)}</b> – zagra to ok. <b>${pctStr(d.p)}</b> graczy, ocena spada do <b>${ev(d)}</b>
          <div class="small">${refHtml(t.fen, d, k)}</div></li>`).join("");
      const good = t.good.slice(0, 3).map((d) => `${mvNo(t.fen, d.san)} <small>(${pctStr(d.p)}, ${ev(d)})</small>`).join(", ");
      return `<div class="trap ${mark("traps", t).star ? "starred" : ""}" data-i="${i}">
        <div class="trap-head"><span class="num">#${num}</span><span class="line">${line}</span>${markHtml("traps", t)}</div>
        ${noteHtml("traps", t)}
        <div class="trap-body">
          <div class="muted small">Ruch ${oppName}. Naturalne, ale złe:</div>
          <ul class="badlist">${bad}</ul>
          <div class="small">${t.only_move ? '<span class="tag">jedyny dobry ruch</span> ' : '<span class="muted">Dobre odpowiedzi:</span> '}${good}</div>
          <div class="real small" data-real="${i}" title="Prawdziwe partie z wybranego zakresu rankingu: ile razy zagrano zły ruch i jak potem kończyły się partie (wynik z Twojej perspektywy)">${realHtml(i)}</div>
          ${oppHtml(t.opponent, res)}
        </div>
        <div class="trap-meta">
          <span title="Szansa, że przeciwnik swoimi typowymi ruchami doprowadzi do tej pozycji">dojście <b>${reachStr(t.reach)}</b></span>
          <span title="Ile % ludzi popełnia tu błąd${t.calibrated ? ` (model: ${pctStr(t.error_raw || 0)}, po kalibracji na partiach)` : ""}">błąd <b>${pctStr(t.error_chance)}</b></span>
          <span title="Średnio ile % szansy wygranej przeciwnik tu oddaje">siła <b>${(t.trick * 100).toFixed(1)}</b></span>
          ${t.gain != null ? `<span title="O ile rośnie Twój oczekiwany wynik w tej pozycji dzięki ludzkim błędom">zysk <b>+${(t.gain * 100).toFixed(1)}%</b></span>` : ""}
          ${t.per100 != null ? `<span title="Oczekiwany zysk w punktach na 100 partii od pozycji startowej (dojście × zysk)">na 100 partii <b>+${t.per100.toFixed(t.per100 < 1 ? 2 : 1)} pkt</b></span>` : ""}
          ${t.line_net ? `<span class="${t.line_net.per100 < 0 ? "costtxt" : "muted"}" title="Czy opłaca się Twoje pierwsze odejście od najlepszego ruchu w tej linii (${esc(t.line_net.at)} zamiast ${esc(t.line_net.best)}): oczekiwany wynik ze WSZYSTKIMI pułapkami i błędami przeciwnika po nim minus wynik po najlepszym ruchu, w punktach na 100 partii. Dotyczy całej linii, nie tylko tej pułapki.">${esc(t.line_net.at)}: netto <b>${t.line_net.per100 >= 0 ? "+" : ""}${t.line_net.per100.toFixed(2)}</b></span>` : ""}
          ${t.cost >= 10 ? `<span class="costtxt" title="Ile oceny oddajesz swoimi ruchami w tej linii względem najlepszych ruchów silnika">koszt <b>−${(t.cost / 100).toFixed(2)}</b></span>` : ""}
          ${hiddenTag(t)}
          ${t.calibrated ? `<span class="tag soft" title="${t.games_total ? `Tylko ${t.games_total} partii - błąd w dużej części przewiduje model` : "Brak partii - błąd przewiduje sam model"}; część z modelu skalibrowano na prawdziwych partiach (przed kalibracją ${pctStr(t.error_raw || 0)})">${t.games_total ? "mało partii" : "bez partii"}</span>` : ""}
          ${t.rare ? '<span class="tag soft" title="Bardzo mała szansa dojścia - poniżej progu z ustawień">rzadkie dojście</span>' : ""}
          ${t.verified === true ? `<span class="tag ok" title="Twoje ruchy w linii${t.reverified ? " i błąd przeciwnika" : ""} sprawdzone na większej głębokości">✓ zweryfikowane</span>` : ""}
          <span class="muted" title="Liczba partii z tą pozycją (wybrany zakres rankingu)">${gamesStr(t.local_games, t.lichess_games)}</span>
        </div>
      </div>`;
    }, keepFn("traps"));
  bindToolbar(el);
  bindActions(el, "traps");
  bindCards(el, "trap", (i) => {
    const t = traps[i];
    loadLine(res, t.line_uci, arrowsFor(t.good, t.bad));
  });
}

function renderHard(res) {
  const color = res.params.color;
  const rootFen = res.params.fen;
  const el = $("#tab-hard");
  const lines = res.hard_lines || [];
  const oppName = color === "white" ? "Czarne" : "Białe";
  const help = `<details class="help"><summary>Jak to czytać?</summary>
    <p>Trudna linia to wariant, w którym <b>przeciwnik musi kilka razy znaleźć trudny ruch</b>. Trudny moment to pozycja,
    w której co najmniej ${Math.round((res.params.hard_error ?? 0.25) * 100)}% graczy ${rangeStr(res)} się myli, a dobry ruch
    <b>nie jest oczywisty</b> – nie jest odbiciem bierki, wymuszoną ucieczką z szacha ani ruchem, który typowy gracz
    i tak by zagrał (Maia ≥ ${Math.round((res.params.obvious_p ?? 0.5) * 100)}%).</p>
    <p>Linia pokazuje przebieg, w którym przeciwnik <b>za każdym razem broni się dobrze</b> – dla Ciebie to gotowy
    repertuar na najlepszą obronę. <b>Przejdzie bez błędu</b> = szansa, że typowy gracz znajdzie wszystkie trudne ruchy.
    <b>Dojście</b> = szansa, że w ogóle dojdzie do pierwszego trudnego momentu. Kliknij moment, żeby zobaczyć go na szachownicy,
    a „karę”, żeby zobaczyć, jak wykorzystać błąd.</p></details>`;
  if (!lines.length) {
    el.innerHTML = help + `<p class="muted">Brak trudnych linii. Pomaga większa liczba pozycji, większa głębokość
      wariantu albo obniżenie progu „trudny moment” w ustawieniach zaawansowanych.</p>`;
    return;
  }
  el.innerHTML = help + toolbar("hard") + renderFamilies(lines, rootFen, (n) => plural(n, "linia", "linie", "linii"), (i, num, dim) => {
    const h = lines[i];
    const ev = (d) => fmtEval(myToWhite(d.cp, color));
    const moments = h.moments.map((m, j) => {
      const good = m.good.map((g) => `<b class="goodmv">${mvNo(m.fen, g.san)}!</b> <small>(znajduje ${pctStr(g.p)})</small>`).join(" lub ");
      const bad = m.bad.slice(0, 2).map((b, k) => `${mvNo(m.fen, b.san)} <small>${pctStr(b.p)} → ${ev(b)}</small> <small>${refHtml(m.fen, b, k, j)}</small>`).join("; ");
      const kind = m.type === "only" ? '<span class="tag">jedyny ruch</span>' : '<span class="tag soft">trudny wybór</span>';
      return `<li data-sub="${j}" class="moment" title="Pokaż tę pozycję">
        ${kind} ${hiddenTag(m)} ${m.calibrated ? `<span class="tag soft" title="Błąd w dużej części z modelu, skalibrowany na prawdziwych partiach">${m.games ? "mało partii" : "bez partii"}</span>` : ""}
        ${good} – myli się <b>${pctStr(m.error)}</b>${bad ? `, np. ${bad}` : ""}</li>`;
    }).join("");
    return `<div class="hline ${mark("hard", h).star ? "starred" : ""}" data-i="${i}">
      <div class="trap-head"><span class="num">#${num}</span><span class="line">${lineHtml(rootFen, h.line, dim)}</span>${markHtml("hard", h)}</div>
      ${noteHtml("hard", h)}
      <div class="muted small">${oppName} muszą znaleźć ${h.moments.length} ${h.moments.length === 1 ? "trudny ruch" : "trudne ruchy"}:</div>
      <ol class="moments">${moments}</ol>
      <div class="trap-meta">
        <span title="Szansa, że typowy gracz znajdzie wszystkie trudne ruchy">przejdzie bez błędu <b>${pctStr(h.survive)}</b></span>
        <span title="Szansa, że przeciwnik dojdzie do pierwszego trudnego momentu">dojście <b>${reachStr(h.entry_reach)}</b></span>
        ${h.exp_errors != null ? `<span title="Oczekiwana liczba błędów przeciwnika w tej linii na 100 partii od pozycji startowej (uwzględnia szansę dojścia do każdego momentu)">≈ <b>${(h.exp_errors * 100).toFixed(1)}</b> błędów / 100 partii</span>` : ""}
        ${h.cost >= 10 ? `<span class="costtxt" title="Ile oceny oddajesz swoimi ruchami w tej linii">koszt <b>−${(h.cost / 100).toFixed(2)}</b></span>` : ""}
        ${h.verified === true ? '<span class="tag ok">✓ zweryfikowane</span>' : ""}
        <span class="muted">${gamesStr(h.local_games, h.lichess_games)}</span>
      </div>
    </div>`;
  }, keepFn("hard"));
  bindToolbar(el);
  bindActions(el, "hard");
  bindCards(el, "hline", (i, sub) => {
    const h = lines[i];
    const m = h.moments[sub === null ? 0 : sub];
    loadLine(res, m.line_uci, arrowsFor(m.good, m.bad));
  });
}

function renderNovelties(res) {
  const color = res.params.color;
  const rootFen = res.params.fen;
  const el = $("#tab-nov");
  const novs = res.novelties || [];
  const help = `<details class="help"><summary>Jak to czytać?</summary>
    <p>Nowinka to <b>Twój ruch rzadko grany w pozycji, która jest znaną teorią</b> (co najmniej
    ${res.params.theory_min_games ?? 50} partii w wybranych bazach i zakresie ${rangeStr(res)}), grany najwyżej
    ${res.params.novelty_max_games ?? 10} razy, a według silnika najwyżej ${((res.params.novelty_max_loss ?? 50) / 100).toFixed(1)} piona
    gorszy od najlepszego ruchu i mieszczący się w Twoim progu oceny. <span class="tag tn">TN</span> = ani jednej partii.</p>
    <p><b>Po nowince</b> – co przeciwnik zwykle odpowiada (Maia/statystyki) i najlepsza pułapka, jaką program znalazł dalej.
    Wyżej są nowinki w mocno grywanej teorii, bliskie najlepszemu ruchowi i takie, po których przeciwnikowi łatwo o błąd.</p></details>`;
  if (!novs.length) {
    el.innerHTML = help + `<p class="muted">Brak nowinek. Nowinki szukane są tylko w pozycjach z co najmniej
      ${res.params.theory_min_games ?? 50} partiami – zacznij szukanie wcześniej w debiucie, zwiększ liczbę partii „rzadko”
      albo dopuszczalną stratę.</p>`;
    return;
  }
  el.innerHTML = help + toolbar("nov") + renderFamilies(familyItems("nov"), rootFen, (k) => plural(k, "nowinka", "nowinki", "nowinek"), (i, num, dim) => {
    const n = novs[i];
    const before = n.line.slice(0, -1);
    const pre = before.length ? lineHtml(rootFen, before, dim) : "W pozycji startowej";
    const ev = (cp) => fmtEval(myToWhite(cp, color));
    const replies = (n.replies || []).map((d) => `${mvNo(n.fen, d.san)} <small>${pctStr(d.p)}${d.loss >= 0.1 ? " – błąd → " + ev(d.cp) : ""}</small>`).join(", ");
    const follow = n.follow_trap
      ? `<div class="small" data-sub="1" title="Pokaż pułapkę"><span class="muted">Dalej pułapka:</span> ${lineText(rootFen, n.follow_trap.line)}
         <small>(błąd ${pctStr(n.follow_trap.error)})</small></div>` : "";
    const share = n.share != null && n.parent_games ? ` (${(n.share * 100).toFixed(n.share < 0.01 ? 2 : 1)}%)` : "";
    const games = n.games === 0 ? '<span class="tag tn">TN</span> nigdy nie grane'
      : n.rare_only ? `<span class="tag soft" title="Ruch ma sporo partii, ale mały udział w tej pozycji">rzadki wariant</span> grane <b>${n.games.toLocaleString("pl")}</b>×${share}`
      : `grane <b>${n.games}</b>×${share}`;
    return `<div class="nov ${mark("nov", n).star ? "starred" : ""}" data-i="${i}">
      <div class="trap-head"><span class="num">#${num}</span><span class="line">${pre}</span>${markHtml("nov", n)}</div>
      ${noteHtml("nov", n)}
      <div class="nov-move"><b class="novmv">${mvNo(n.parent_fen, n.san)}!?</b> ${n.rare_only ? "" : n.tn ? "TN" : "N"}
        <span class="muted small">– ${games} na ${(n.parent_games || 0).toLocaleString("pl")} partii w tej pozycji</span></div>
      <div class="small">Ocena <b>${ev(n.eval)}</b> · najlepszy ruch silnika ${n.best_san ? mvNo(n.parent_fen, n.best_san) : "?"} ${ev(n.best_cp)}
        <span class="muted">(różnica ${(n.loss / 100).toFixed(2)})</span></div>
      ${replies ? `<div class="small"><span class="muted">Po nowince:</span> ${replies}</div>` : ""}
      ${follow}
      <div class="trap-meta">
        ${n.verified === true ? '<span class="tag ok">✓ zweryfikowane</span>' : '<span class="muted">niezweryfikowane</span>'}
        ${n.error >= 0.08 ? `<span>od razu błąd u <b>${pctStr(n.error)}</b> ludzi</span>` : ""}
        ${n.follow > 0.001 ? `<span title="Średnio ile % szansy wygranej przeciwnik oddaje w kilku ruchach po nowince (wg modelu ludzkich ruchów)">potem przeciwnik traci <b>${(n.follow * 100).toFixed(1)}%</b></span>` : ""}
        ${n.out_of_book ? '<span class="tag tn" title="Po nowince nie ma żadnych partii - przeciwnik musi myśleć sam">poza teorią</span>' : ""}
      </div>
    </div>`;
  }, keepFn("nov"));
  bindToolbar(el);
  bindActions(el, "nov");
  bindCards(el, "nov", (i, sub) => {
    const n = novs[i];
    if (sub === 1 && n.follow_trap) loadLine(res, n.follow_trap.line_uci, []);
    else loadLine(res, n.line_uci, [{ from: n.line_uci.at(-1).slice(0, 2), to: n.line_uci.at(-1).slice(2, 4), color: "#2f7bd9", opacity: 0.8 }]);
  });
}

// Ruchy od początku partii do pozycji wyszukiwania: zapisane z wynikami (od wersji 0.5),
// a dla starszych wyszukiwań - z pamięci okna.
function searchHistory(res) {
  const p = res.params || {};
  if (p.pre_fen) return { startFen: p.pre_fen, moves: p.pre_moves || [] };
  return JSON.parse(localStorage.getItem("hist-" + res.id) || "null");
}

// upTo = ile ruchów z `ucis` pokazać na szachownicy (reszta dostępna strzałką →)
async function loadLine(res, ucis, arrows, upTo = null) {
  const hist = searchHistory(res);
  let startFen = hist ? hist.startFen : res.params.fen;
  let pre = hist ? hist.moves : [];
  let r = await api("/api/line", { method: "POST", body: { fen: startFen, moves: pre.concat(ucis) } });
  // historia musi kończyć się na pozycji wyszukiwania - inaczej ją pomijamy
  const reached = pre.length ? (r.moves[pre.length - 1] || {}).fen : startFen;
  if (!reached || posKey(reached) !== posKey(res.params.fen)) {
    startFen = res.params.fen;
    pre = [];
    r = await api("/api/line", { method: "POST", body: { fen: startFen, moves: ucis } });
  }
  state.startFen = startFen;
  state.moves = r.moves;
  state.cursor = upTo === null ? r.moves.length : Math.min(r.moves.length, pre.length + upTo);
  state.arrows = arrows || [];
  refresh();
}

function renderTree(res) {
  const el = $("#tab-tree");
  const color = res.params.color;
  const rootFen = res.params.fen;
  const rootTurnWhite = rootFen.split(" ")[1] === "w";
  const build = (node, path, depth) => {
    if (!node.children.length) return "";
    const items = node.children.map((c) => {
      const p = path.concat(c.uci);
      const idx = p.length - 1;
      const whiteMove = (idx % 2 === 0) === rootTurnWhite;
      const mine = whiteMove === (color === "white");
      const label = `${moveNumberPrefix(rootFen, idx) || (whiteMove ? "" : "")}${c.san}`;
      const meta = [`<span class="ev">${c.eval !== null ? fmtEval(myToWhite(c.eval, color)) : ""}</span>`];
      if (!mine) meta.push(`<span class="muted">${pctStr(c.prob)}</span>`);
      if (c.novelty) meta.push(`<span class="tag tn" title="Nowinka: grane ${c.novelty.games}× na ${c.novelty.parent_games} partii">${c.novelty.tn ? "TN" : "N"}</span>`);
      if (c.moment) meta.push(`<span class="tag" title="Trudny moment: myli się ${pctStr(c.moment.error)}">${c.moment.type === "only" ? "jedyny ruch" : "trudne"}</span>`);
      else if (c.trick >= 0.03) meta.push(`<span class="tag" title="oczekiwana strata przeciwnika w tej pozycji">pułapka ${(c.trick * 100).toFixed(0)}</span>`);
      if (c.hidden) meta.push('<span class="tag hid">ukryta</span>');
      if (c.verified === true) meta.push('<span class="tag ok">✓</span>');
      if (c.transposition) meta.push('<span class="tag soft" title="Ta sama pozycja jest w drzewie inną kolejnością ruchów">↔ transpozycja</span>');
      const g = gamesStr(c.local_games, c.lichess_games);
      if (g) meta.push(`<span class="muted small">${g}</span>`);
      const sub = build(c, p, depth + 1);
      return `<li><details ${depth < 1 ? "open" : ""}><summary data-path="${p.join(",")}" data-key="${posKey(c.fen)}" class="${mine ? "mine" : "opp"}">
        <b>${label}</b> ${meta.join(" ")}</summary>${sub}</details></li>`;
    }).join("");
    return `<ul>${items}</ul>`;
  };
  el.innerHTML = build(res.tree, [], 0) || '<p class="muted">Puste drzewo.</p>';
  el.querySelectorAll("summary").forEach((s) => {
    s.addEventListener("click", (e) => {
      if (e.offsetX > 18) { e.preventDefault(); loadLine(res, s.dataset.path.split(",")); }
    });
  });
}

async function loadSaved(selectId) {
  const r = await api("/api/searches");
  const sel = $("#saved");
  const running = new Set(r.running.map((j) => j.id));
  sel.innerHTML = '<option value="">— zapisane wyszukiwania —</option>' + r.saved.map((s) =>
    `<option value="${s.id}">${running.has(s.id) ? "⏳ " : ""}${s.created || ""} · ${s.color === "white" ? "białe" : "czarne"} · ${s.elo}` +
    `${s.opponent ? " · vs " + esc(s.opponent) : ""} · ${s.traps} pułapek · ${s.hard ?? 0} linii · ${s.novelties ?? 0} nowinek` +
    `${s.partial ? " · częściowe" : ""}</option>`).join("");
  if (selectId) sel.value = selectId;
  refreshQueue();
  if (r.running.length && !state.job) {
    state.job = (r.running.find((j) => j.status === "running") || r.running[0]).id;
    pollJob();
  }
}

// ------------------------------------------------------------------ status

function fileName(p) { return p ? p.split(/[\\/]/).pop() : ""; }

function dbLabel(db, available) {
  const b = db.building || {};
  if (b.running) return `baza: indeksowanie ${b.pct ?? 0}%${b.eta_min != null ? `, zostało ~${b.eta_min} min` : ""}`;
  if (!db.pgn) return "baza: nie wybrano";
  if (!db.exists) return "baza: brak pliku";
  if (db.index && available) return `baza: ${(db.index.games || 0).toLocaleString("pl")} partii`;
  return "baza: brak indeksu";
}

let statusTimer = null;
let lastStatus = null;

async function loadStatus() {
  try {
    const s = await api("/api/status");
    lastStatus = s;
    const db = s.database;
    const dbOk = db.index && s.index_available;
    $("#status").innerHTML = [
      `<span class="${s.engine_path_ok ? "ok" : "bad"}" title="${s.engine_path || ""}">${s.engine_path_ok ? s.engine : "brak silnika"}</span>`,
      `<span class="${dbOk ? "ok" : db.building.running ? "warn" : db.pgn ? "bad" : "warn"}" title="${db.pgn || ""}">${dbLabel(db, s.index_available)}</span>`,
      `<span class="${s.maia_error ? "bad" : "ok"}" title="Model ludzkich ruchów">${esc(s.human_model || "Maia-2")}${s.maia_loaded ? "" : " (ładuje się przy 1. użyciu)"}</span>`,
      `<span class="${!s.lichess_token ? "bad" : s.lichess_error ? "warn" : "ok"}">Lichess${s.lichess_token ? "" : ": brak tokenu"}</span>`,
    ].join("");
    $("#set-info").textContent = s.lichess_error ? "Ostatni błąd Lichess: " + s.lichess_error : (s.lichess_token ? "Token ustawiony." : "Brak tokenu - statystyki Lichess wyłączone.");
    renderSettings(s);
    if (s.search_running || s.queued) refreshQueue();
    // częstsze odświeżanie, gdy coś się dzieje w tle
    clearTimeout(statusTimer);
    statusTimer = setTimeout(loadStatus, db.building.running || $("#dlg-settings").open ? 2500 : 15000);
    if (!s.engine_path_ok && !loadStatus.prompted) { loadStatus.prompted = true; openSettings(); }
  } catch (_) {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(loadStatus, 5000);
  }
}

function renderSettings(s) {
  if (document.activeElement !== $("#set-human")) $("#set-human").value = s.human_model_setting || "auto";
  $("#set-human-info").innerHTML = `Używany model: <b>${esc(s.human_model || "")}</b>.` +
    (s.maia3_installed ? "" : ` Maia-3 nie jest zainstalowana – zobacz README („Maia-3”).`) +
    (s.maia_error ? `<br><span class="badtxt">${esc(s.maia_error)}</span>` : "");
  const eng = $("#set-engine");
  eng.innerHTML = s.engine_path_ok
    ? `<b>${s.engine}</b><br><span class="path">${s.engine_path}</span>`
    : `<span class="badtxt">Nie znaleziono silnika.</span> Pobierz Stockfisha ze stockfishchess.org i wskaż plik .exe.`;
  const db = s.database;
  const b = db.building || {};
  let html;
  if (!db.pgn) html = "Nie wybrano bazy - statystyki tylko z Lichess.";
  else {
    html = `<b>${fileName(db.pgn)}</b>${db.size_mb != null ? ` (${db.size_mb.toLocaleString("pl")} MB)` : ""}<br><span class="path">${db.pgn}</span><br>`;
    if (!db.exists) html += '<span class="badtxt">Plik nie istnieje.</span>';
    else if (b.running) html += `Indeksowanie: ${b.phase || "start"} – ${b.pct ?? 0}%${b.eta_min != null ? `, zostało ~${b.eta_min} min` : ""}`;
    else if (db.index) html += `Indeks gotowy: ${(db.index.games || 0).toLocaleString("pl")} partii (zbudowany ${db.index.built || "?"}).`;
    else html += db.resumable ? "Indeksowanie przerwane – można je wznowić." : "Brak indeksu.";
    if (db.error) html += `<br><span class="badtxt">${db.error}</span>`;
  }
  $("#set-db").innerHTML = html;
  const prog = $("#set-db-progress");
  prog.classList.toggle("hidden", !b.running);
  prog.firstElementChild.style.width = (b.pct || 0) + "%";
  $("#btn-db-build").classList.toggle("hidden", !(db.pgn && db.exists && !db.index && !b.running));
  $("#btn-db-build").textContent = db.resumable ? "Wznów indeksowanie" : "Zbuduj indeks";
  $("#btn-db-cancel").classList.toggle("hidden", !b.running);
  $("#btn-db-clear").classList.toggle("hidden", !db.pgn || b.running);
  $("#btn-pick-db").disabled = !!b.running;
  $("#btn-pick-engine").disabled = !!s.search_running;
}

function openSettings() {
  $("#set-token").value = "";
  $("#set-msg").textContent = "";
  if (!$("#dlg-settings").open) $("#dlg-settings").showModal();
  loadStatus();
}

async function settingsAction(fn) {
  $("#set-msg").textContent = "";
  try { await fn(); } catch (e) { $("#set-msg").innerHTML = `<span class="badtxt">${e.message}</span>`; }
  await loadStatus();
}

async function pickFile(kind) {
  const r = await api("/api/pick_file", { method: "POST", body: { kind } });
  return r.path;
}

// ------------------------------------------------------------------ zdarzenia

$("#btn-start").onclick = () => goto(0);
$("#btn-back").onclick = () => goto(state.cursor - 1);
$("#btn-fwd").onclick = () => goto(state.cursor + 1);
$("#btn-flip").onclick = () => { state.orientation = state.orientation === "white" ? "black" : "white"; refresh({ analyse: false }); };
document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (e.key === "ArrowLeft") goto(state.cursor - 1);
  if (e.key === "ArrowRight") goto(state.cursor + 1);
  if (e.key === "Home") goto(0);
  if (e.key === "End") goto(state.moves.length);
  if (e.key === "f") $("#btn-flip").click();
});
$("#btn-paste").onclick = () => { $("#paste-text").value = ""; $("#dlg-paste").showModal(); };
$("#paste-ok").onclick = async (e) => {
  try {
    const r = await api("/api/parse_pgn", { method: "POST", body: { pgn: $("#paste-text").value } });
    const lr = await api("/api/line", { method: "POST", body: { fen: r.start_fen, moves: r.moves.map((m) => m.uci) } });
    state.startFen = r.start_fen;
    state.moves = lr.moves;
    state.cursor = lr.moves.length;
    state.arrows = [];
    refresh();
  } catch (err) { e.preventDefault(); alert(err.message); }
};
$("#btn-settings").onclick = openSettings;
$("#btn-pick-engine").onclick = () => settingsAction(async () => {
  const p = await pickFile("engine");
  if (!p) return;
  $("#set-msg").textContent = "Sprawdzam silnik…";
  const r = await api("/api/engine", { method: "POST", body: { path: p } });
  $("#set-msg").textContent = `Ustawiono silnik: ${r.name}`;
  if ($("#auto-engine").checked) loadEngine(curFen());
});
$("#btn-pick-db").onclick = () => settingsAction(async () => {
  const p = await pickFile("pgn");
  if (!p) return;
  const r = await api("/api/database", { method: "POST", body: { path: p } });
  $("#set-msg").textContent = r.index ? "Baza ustawiona - indeks już był gotowy." : "Baza ustawiona - indeksowanie ruszyło w tle.";
  loadStats(curFen());
});
$("#btn-db-build").onclick = () => settingsAction(() => api("/api/database/build", { method: "POST" }));
$("#btn-db-cancel").onclick = () => settingsAction(() => api("/api/database/cancel", { method: "POST" }));
$("#btn-db-clear").onclick = () => settingsAction(async () => { await api("/api/database/clear", { method: "POST" }); loadStats(curFen()); });
$("#set-save").onclick = async () => {
  const t = $("#set-token").value.trim();
  if (t) await api("/api/settings", { method: "POST", body: { lichess_token: t } });
  loadStatus();
};
$("#p-color").onchange = () => {
  updateEvalLabel();
  const c = $("#p-color").value;
  $("#p-eval").value = c === "white" ? -0.5 : 1.0;
  state.orientation = c;
  refresh({ analyse: false });
};
$("#p-elo-min").onchange = $("#p-elo-max").onchange = () => loadStats(curFen());
$$(".p-speed").forEach((c) => (c.onchange = () => loadStats(curFen())));
$("#p-lsource").onchange = () => loadStats(curFen());
$("#auto-engine").onchange = () => { if ($("#auto-engine").checked) loadEngine(curFen()); };
$("#btn-search").onclick = startSearch;
$("#btn-cancel").onclick = () => state.job && api(`/api/search/${state.job}/cancel`, { method: "POST" });
$("#btn-finish").onclick = () => state.job && api(`/api/search/${state.job}/finish`, { method: "POST" });
$("#btn-partial").onclick = async () => {
  if (!state.job) return;
  const r = await api(`/api/search/${state.job}?partial=1`);
  if (r.result) { showResult(r.result); $("#saved").value = ""; }
};
$("#p-mode").onchange = updateModeUI;
$("#p-depth").onchange = () => { if (+$("#p-depth-min").value > +$("#p-depth").value) $("#p-depth-min").value = $("#p-depth").value; };
$("#p-opponent").onchange = () => loadStats(curFen());
$("#btn-opp-load").onclick = async () => {
  try {
    await api("/api/opponent/load", { method: "POST", body: { source: $("#opp-source").value, name: $("#opp-name").value, max_games: +$("#opp-max").value } });
    pollOpponent();
  } catch (e) { $("#opp-status").innerHTML = `<span class="badtxt">${esc(e.message)}</span>`; }
};
$("#set-human").onchange = () => settingsAction(async () => {
  const r = await api("/api/settings", { method: "POST", body: { human_model: $("#set-human").value } });
  $("#set-msg").textContent = "Model: " + r.human_model;
  loadStats(curFen());
});
$("#saved").onchange = async () => {
  const id = $("#saved").value;
  if (!id) { closeResult(); return; }
  let r = await api("/api/search/" + id);
  // trwające wyszukiwanie: pokaż ostatni zapis częściowy
  if (!r.result && (r.status === "running" || r.partial_available)) r = await api(`/api/search/${id}?partial=1`);
  if (r.result) showResult(r.result);
};
$("#btn-close-res").onclick = closeResult;
$("#btn-new").onclick = () => {
  state.startFen = START;
  state.moves = [];
  state.cursor = 0;
  state.arrows = [];
  refresh();
};
// ------------------------------------------------------------------ eksport do studium Lichess

function exportBody() {
  const kind = $("#exp-kind").value;
  const groups = exportGroups(kind, $("#exp-fam").checked, $("#exp-filter").value);
  const list = rawItems(kind);
  const notes = {};
  groups.forEach((g) => g.items.forEach((i) => { const n = mark(kind, list[i]).note; if (n) notes[i] = n; }));
  const hist = searchHistory(state.result);
  const old = document.querySelector('input[name="exp-target"]:checked').value === "old";
  return {
    kind, groups, notes,
    prefix: hist ? { start_fen: hist.startFen, moves: hist.moves } : null,
    study: old ? $("#exp-study").value.trim() : null,
    new_name: $("#exp-name").value.trim(),
    visibility: $("#exp-vis").value,
  };
}

function kindUnit(kind, n) {
  if (kind === "traps") return plural(n, "pułapka", "pułapki", "pułapek");
  if (kind === "hard") return plural(n, "linia", "linie", "linii");
  return plural(n, "nowinka", "nowinki", "nowinek");
}

function updateExportInfo() {
  const kind = $("#exp-kind").value;
  const f = $("#exp-filter");
  if (f.dataset.kind !== kind) {
    f.innerHTML = FILTERS[kind].map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
    f.value = resFilter(kind);
    f.dataset.kind = kind;
  }
  const groups = exportGroups(kind, $("#exp-fam").checked, f.value);
  const n = groups.reduce((a, g) => a + g.items.length, 0);
  const many = groups.length > 64;
  $("#exp-count").innerHTML = n
    ? `${n} ${kindUnit(kind, n)} w ${groups.length} ${plural(groups.length, "rozdziale", "rozdziałach", "rozdziałach")}${many ? ' <span class="badtxt">(Lichess przyjmie najwyżej 64)</span>' : ""}:
       <span class="muted">${groups.slice(0, 6).map((g) => esc(g.name)).join(" · ")}${groups.length > 6 ? " …" : ""}</span>`
    : '<span class="badtxt">Nic nie pasuje do wybranego filtra.</span>';
  $("#exp-send").disabled = !n || !state.tokenOk;
  $("#exp-copy").disabled = !n;
}

async function checkExportToken() {
  const box = $("#exp-token");
  box.textContent = "Sprawdzam token Lichess…";
  state.tokenOk = false;
  updateExportInfo();
  let t;
  try { t = await api("/api/lichess/token"); } catch (e) { box.innerHTML = `<span class="badtxt">${esc(e.message)}</span>`; return; }
  const form = `<div class="row left"><input id="exp-new-token" type="password" placeholder="wklej nowy token lip_…">
    <button type="button" id="exp-save-token">Zapisz token</button></div>`;
  const link = `<a href="${t.create_url}" target="_blank" rel="noopener">utwórz token z uprawnieniami do studiów</a>
    <span class="path">(${esc(t.create_url)})</span>`;
  if (t.valid === null) box.innerHTML = `<span class="badtxt">Nie udało się sprawdzić tokenu: ${esc(t.error)}</span>`;
  else if (!t.valid) box.innerHTML = `<span class="badtxt">Brak ważnego tokenu Lichess.</span> Na Lichess ${link}, potem wklej go tutaj:${form}`;
  else if (!t.can_write) box.innerHTML = `Token konta <b>${esc(t.user)}</b> nie ma uprawnienia do zapisu studiów (study:write).
    Na Lichess ${link} – zadziała też dla statystyk – i wklej go tutaj:${form}`;
  else { box.innerHTML = `Konto Lichess: <b>${esc(t.user)}</b> – można zapisywać studia.`; state.tokenOk = true; }
  const save = $("#exp-save-token");
  if (save) save.onclick = async () => {
    const v = $("#exp-new-token").value.trim();
    if (!v) return;
    await api("/api/settings", { method: "POST", body: { lichess_token: v } });
    await checkExportToken();
    loadStatus();
  };
  updateExportInfo();
}

function openExport() {
  const res = state.result;
  if (!res) return;
  const active = ($(".tab.active") || {}).dataset?.tab;
  $("#exp-kind").value = ["traps", "hard", "nov"].includes(active) ? active : "traps";
  $("#exp-filter").dataset.kind = "";
  const hist = searchHistory(res);
  let opening = "";
  if (hist && hist.moves.length) opening = movesText(hist.startFen, state.moves.slice(0, hist.moves.length).map((m) => m.san));
  if (!hist || state.moves.length < (hist ? hist.moves.length : 0)) opening = "";
  $("#exp-name").value = ("ChessPrep " + (opening || (res.params.color === "white" ? "białe" : "czarne"))).slice(0, 100);
  $("#exp-msg").textContent = "";
  updateExportInfo();
  $("#dlg-export").showModal();
  checkExportToken();
}

$("#btn-export").onclick = openExport;
$("#exp-kind").onchange = updateExportInfo;
$("#exp-filter").onchange = updateExportInfo;
$("#exp-fam").onchange = updateExportInfo;
document.querySelectorAll('input[name="exp-target"]').forEach((r) => (r.onchange = () => {
  const old = document.querySelector('input[name="exp-target"]:checked').value === "old";
  $("#exp-study").disabled = !old;
  $("#exp-name").disabled = old;
  $("#exp-vis").disabled = old;
}));
$("#exp-copy").onclick = async () => {
  try {
    const pgn = await api(`/api/search/${state.result.id}/chapters_pgn`, { method: "POST", body: exportBody() });
    try { await navigator.clipboard.writeText(pgn); $("#exp-msg").textContent = "PGN (wszystkie rozdziały) skopiowany do schowka."; }
    catch (_) { $("#dlg-export").close(); $("#paste-text").value = pgn; $("#dlg-paste").showModal(); }
  } catch (e) { $("#exp-msg").innerHTML = `<span class="badtxt">${esc(e.message)}</span>`; }
};
$("#exp-send").onclick = async () => {
  const body = exportBody();
  if (body.study === "") { $("#exp-msg").innerHTML = '<span class="badtxt">Wklej adres studium.</span>'; return; }
  const n = Math.min(64, body.groups.length);
  $("#exp-send").disabled = true;
  $("#exp-msg").textContent = `Wysyłam ${n} ${plural(n, "rozdział", "rozdziały", "rozdziałów")} do Lichess…`;
  try {
    const r = await api(`/api/search/${state.result.id}/lichess_study`, { method: "POST", body });
    $("#exp-msg").innerHTML = `Gotowe: ${r.chapters} ${plural(r.chapters, "rozdział", "rozdziały", "rozdziałów")} w studium
      <a href="${r.url}" target="_blank" rel="noopener">${r.url}</a>
      ${r.errors.length ? `<br><span class="badtxt">Problemy: ${r.errors.map(esc).join("; ")}</span>` : ""}`;
  } catch (e) {
    $("#exp-msg").innerHTML = `<span class="badtxt">${esc(e.message)}</span>`;
  } finally {
    $("#exp-send").disabled = !state.tokenOk;
  }
};

$("#btn-pgn").onclick = async () => {
  const id = (state.result && state.result.id) || $("#saved").value;
  if (!id) return;
  const pgn = await api(`/api/search/${id}/pgn`);
  try { await navigator.clipboard.writeText(pgn); $("#job").textContent = "PGN skopiowany do schowka."; }
  catch (_) {
    $("#paste-text").value = pgn; $("#dlg-paste").showModal();
  }
};
$$(".tab").forEach((b) => (b.onclick = () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
  for (const t of ["traps", "hard", "nov", "tree"]) $("#tab-" + t).classList.toggle("hidden", b.dataset.tab !== t);
  scrollToMatch();
}));

updateEvalLabel();
updateModeUI();
loadOpponents().then(() => refresh());
loadStatus();
loadSaved();

// ------------------------------------------------------------------ aktualizacje modeli

let updTimer = null;

function updListHtml(u) {
  return u.updates.map((x) => `<b>${esc(x.name)}</b> <span class="muted" title="${esc(x.info || "")}">${esc(x.installed)} → ${esc(x.latest)}${x.info ? " (" + esc(x.info) + ")" : ""}</span>`).join(", ");
}

function showUpdateNotice(html, cancelPending) {
  const b = $("#update-banner");
  b.innerHTML = html + (cancelPending ? ' <button type="button" class="small ghost" id="upd-unplan">Nie instaluj</button>' : "") +
    ' <button type="button" class="small ghost" id="upd-close">OK</button>';
  b.classList.remove("hidden");
  $("#upd-close").onclick = () => b.classList.add("hidden");
  if (cancelPending) $("#upd-unplan").onclick = async () => { await api("/api/updates/pending", { method: "DELETE" }); b.classList.add("hidden"); };
}

async function checkUpdates(force = false) {
  let u;
  try { u = await api(`/api/updates${force ? "?force=1" : ""}`); } catch (_) { return; }
  const box = $("#set-updates");
  if (u.state && u.state.running) return pollUpdate();
  if (u.last_install) {
    const li = u.last_install;
    showUpdateNotice(li.ok ? `Zaktualizowano: ${esc(li.items.join(", "))} (${esc(li.time)}).`
      : `<span class="badtxt">Aktualizacja (${esc(li.items.join(", "))}) nie powiodła się: ${esc(li.error)}</span>`, false);
    return;
  }
  if (u.pending) {
    box.textContent = `Zaplanowana instalacja przy następnym uruchomieniu: ${u.pending.items.join(", ")}.`;
    if (!force) return;
  }
  if (u.updates.length) {
    box.innerHTML = "Dostępne: " + updListHtml(u);
    if (!sessionStorage.getItem("upd-later") || force) showUpdateBanner(u);
  } else {
    box.textContent = u.errors.length ? `Nie udało się sprawdzić (${u.errors.join(", ")}).` : `Modele są aktualne (sprawdzono ${u.checked}).`;
  }
}

function showUpdateBanner(u) {
  const b = $("#update-banner");
  const restart = u.updates.some((x) => x.restart);
  b.innerHTML = `Nowa wersja modeli: ${updListHtml(u)}${restart ? ' <span class="muted">(program sprawdzi, co się zmieni, a zainstaluje przy następnym uruchomieniu)</span>' : ""}
    <button type="button" class="small primary" id="upd-yes">Aktualizuj</button>
    <button type="button" class="small ghost" id="upd-later">Później</button>`;
  b.classList.remove("hidden");
  $("#upd-later").onclick = () => { sessionStorage.setItem("upd-later", "1"); b.classList.add("hidden"); };
  $("#upd-yes").onclick = async () => {
    try {
      const r = await api("/api/updates", { method: "POST", body: { ids: u.updates.map((x) => x.id) } });
      if (r.error && !r.running) { showUpdateNotice(`<span class="badtxt">${esc(r.error)}</span>`, false); return; }
      pollUpdate();
    } catch (e) { b.innerHTML = `<span class="badtxt">${esc(e.message)}</span>`; }
  };
}

async function pollUpdate() {
  clearTimeout(updTimer);
  let s;
  try { s = await api("/api/updates/status"); } catch (_) { return; }
  const b = $("#update-banner");
  b.classList.remove("hidden");
  if (s.running) {
    b.innerHTML = esc(s.message) + " …";
    updTimer = setTimeout(pollUpdate, 2000);
    return;
  }
  if (s.error) showUpdateNotice(`<span class="badtxt">${esc(s.message)}: ${esc(s.error)}</span>`, false);
  else showUpdateNotice(esc(s.message), s.restart);
  loadStatus();
}

$("#btn-upd-check").onclick = () => { $("#set-updates").textContent = "Sprawdzam…"; checkUpdates(true); };
setTimeout(() => checkUpdates(false), 1500);

// ------------------------------------------------------------------ kalibracja na bazie

let calibTimer = null;

function calibTable(sum) {
  if (!sum || !sum.all || !sum.all.length) return "";
  const row = (k, label) => (sum[k] || []).filter((b) => b.pred >= 0.1).map((b) =>
    `<tr><td>${label}</td><td>${pctStr(b.pred)}</td><td>${pctStr(b.actual)}</td><td class="muted">${b.n}</td></tr>`).join("");
  return `<table class="calib-tbl"><tr><th></th><th>model</th><th>w partiach</th><th>pozycji</th></tr>
    ${row("all", "wszystkie")}${row("shallow", "widać od razu")}${row("deep", "ukryte")}</table>`;
}

async function loadCalib() {
  clearTimeout(calibTimer);
  const lo = +$("#p-elo-min").value, hi = +$("#p-elo-max").value;
  let r;
  try { r = await api(`/api/calibration?elo_min=${lo}&elo_max=${hi}`); } catch (e) { $("#set-calib").textContent = e.message; return; }
  const c = r.current;
  let html = `Model <b>${esc(r.model)}</b>, ranking ${lo}–${hi}: `;
  html += c ? `pomiar na bazie z ${c.date}: <b>${c.positions}</b> pozycji (${c.errors} błędów w partiach)` +
      (c.positions < r.min_positions ? ` <span class="warntxt">– za mało (potrzeba ${r.min_positions}), używana krzywa startowa</span>` : "")
    : '<span class="warntxt">brak pomiaru</span> – używana krzywa startowa i dane z wyszukiwań.';
  if (r.job) html += `<br>${r.job.status === "queued" ? "Czeka w kolejce…" : esc(r.job.message || "Start…")}`;
  else if (r.last) {
    const lr = r.last;
    html += `<br>Ostatnia kalibracja: ${lr.status === "done" ? "zakończona" : lr.status === "error" ? '<span class="badtxt">błąd: ' + esc(lr.error || "") + "</span>" : "przerwana"}` +
      (lr.saved != null ? ` – zapisano ${lr.saved} pozycji` : "") + (lr.note ? ` <span class="warntxt">(${esc(lr.note)})</span>` : "") +
      (lr.summary ? ":" + calibTable(lr.summary) : "");
  }
  $("#set-calib").innerHTML = html;
  $("#btn-calib").classList.toggle("hidden", !!r.job);
  $("#btn-calib-cancel").classList.toggle("hidden", !r.job);
  $("#btn-calib-cancel").dataset.job = r.job ? r.job.id : "";
  if (r.job && $("#dlg-settings").open) calibTimer = setTimeout(loadCalib, 3000);
}

$("#btn-calib").onclick = () => settingsAction(async () => {
  await api("/api/calibrate", { method: "POST", body: {
    elo_min: +$("#p-elo-min").value, elo_max: +$("#p-elo-max").value, positions: +$("#calib-n").value || 2000 } });
  refreshQueue();
  loadCalib();
});
$("#btn-calib-cancel").onclick = async () => {
  const id = $("#btn-calib-cancel").dataset.job;
  if (id) await api(`/api/search/${id}/cancel`, { method: "POST" });
  setTimeout(loadCalib, 500);
};
$("#btn-settings").addEventListener("click", () => loadCalib());
