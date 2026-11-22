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
    const r = await api(`/api/stats?fen=${encodeURIComponent(fen)}&elo_min=${lo}&elo_max=${hi}&speeds=${speeds}&source=${src}`);
    if (my !== statsToken) return;
    const rows = {};
    const add = (src, list, key = "games") => (list || []).forEach((m) => {
      rows[m.uci] = rows[m.uci] || { uci: m.uci, san: m.san };
      rows[m.uci][src] = m;
    });
    add("maia", r.maia);
    add("local", r.local && r.local.moves);
    add("lichess", r.lichess && r.lichess.moves);
    const lt = r.local && r.local.available ? r.local.total : null;
    const lit = r.lichess && r.lichess.available ? r.lichess.total : null;
    const list = Object.values(rows).sort((a, b) =>
      ((b.maia || {}).p || 0) - ((a.maia || {}).p || 0) ||
      ((b.lichess || {}).games || 0) - ((a.lichess || {}).games || 0)).slice(0, 14);
    const pct = (n, t) => (t ? Math.round((100 * n) / t) + "%" : "");
    const score = (m) => m && m.games ? Math.round((100 * (m.white + m.draws / 2)) / m.games) + "%" : "";
    tbl.innerHTML = `<tr><th>Ruch</th><th title="Maia-2: jak często człowiek z tego zakresu zagra ten ruch">Maia</th>
      <th title="Lokalna baza (OTB), wybrany zakres rankingu">Baza ${lt !== null ? "(" + lt.toLocaleString("pl") + ")" : ""}</th>
      <th title="${srcTitle}">${srcName} ${lit !== null ? "(" + lit.toLocaleString("pl") + ")" : ""}</th>
      <th title="Wynik białych w partiach Lichess">Białe</th></tr>` +
      list.map((m) => `<tr data-uci="${m.uci}">
        <td><b>${m.san}</b></td>
        <td><div class="bar" style="--w:${Math.round(((m.maia || {}).p || 0) * 100)}%"></div>${m.maia ? Math.round(m.maia.p * 100) + "%" : ""}</td>
        <td>${m.local ? m.local.games.toLocaleString("pl") + ` <small>${pct(m.local.games, lt)}</small>` : ""}</td>
        <td>${m.lichess ? m.lichess.games.toLocaleString("pl") + ` <small>${pct(m.lichess.games, lit)}</small>` : ""}</td>
        <td>${score(m.lichess)}</td></tr>`).join("");
    tbl.querySelectorAll("tr[data-uci]").forEach((tr) => {
      tr.onclick = () => { const u = tr.dataset.uci; userMove(u.slice(0, 2), u.slice(2, 4)); };
    });
    const notes = [];
    if (!r.local.available) notes.push("Baza lokalna: indeks jeszcze nie gotowy.");
    else if (r.local.beyond_index) notes.push("Baza lokalna: pozycja głębiej niż zindeksowane półruchy.");
    if (r.lichess_error) notes.push("Lichess: " + r.lichess_error);
    if (r.maia_error) notes.push("Maia: " + r.maia_error);
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

function searchParams() {
  const color = $("#p-color").value;
  const v = +$("#p-eval").value;
  return {
    fen: curFen(),
    color,
    min_eval: Math.round((color === "white" ? v : -v) * 100),
    max_drop: Math.round(+$("#p-drop").value * 100),
    elo_min: +$("#p-elo-min").value,
    elo_max: +$("#p-elo-max").value,
    max_plies: +$("#p-plies").value,
    budget: +$("#p-budget").value,
    depth: +$("#p-depth").value,
    verify_depth: +$("#p-vdepth").value,
    my_candidates: +$("#p-cands").value,
    opp_max_replies: +$("#p-replies").value,
    opp_coverage: +$("#p-cover").value,
    use_lichess: $("#p-lichess").checked,
    lichess_source: $("#p-lsource").value,
    use_local_db: $("#p-local").checked,
    speeds: $$(".p-speed").filter((c) => c.checked).map((c) => c.value),
  };
}

function saveHistory(jobId) {
  const hist = { startFen: state.startFen, moves: state.moves.slice(0, state.cursor).map((m) => m.uci) };
  localStorage.setItem("hist-" + jobId, JSON.stringify(hist));
}

async function startSearch() {
  try {
    const info = await api("/api/search", { method: "POST", body: { params: searchParams() } });
    saveHistory(info.id);
    state.job = info.id;
    $("#btn-cancel").classList.remove("hidden");
    $("#btn-search").disabled = true;
    pollJob();
  } catch (e) {
    $("#job").textContent = "Błąd: " + e.message;
  }
}

function fmtTime(s) {
  if (s === null || s === undefined) return "?";
  const m = Math.floor(s / 60);
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

async function pollJob() {
  if (!state.job) return;
  let info;
  try { info = await api("/api/search/" + state.job); } catch (e) { $("#job").textContent = e.message; return; }
  if (info.status === "running" || info.status === "queued") {
    const pct = info.total ? Math.round((100 * info.progress) / info.total) : 0;
    $("#job").innerHTML = `<div class="progress"><div style="width:${Math.min(100, pct)}%"></div></div>
      ${info.status === "queued" ? "w kolejce…" : `${info.progress}/${info.total} pozycji · ${fmtTime(info.elapsed)} · zostało ~${fmtTime(info.eta)}`}
      <div class="ellipsis">${info.message || ""}</div>`;
    setTimeout(pollJob, 1500);
    return;
  }
  $("#btn-cancel").classList.add("hidden");
  $("#btn-search").disabled = false;
  const jobId = state.job;
  state.job = null;
  if (info.status === "error") { $("#job").textContent = "Błąd: " + info.error; return; }
  $("#job").textContent = (info.status === "cancelled" ? "Przerwano - wyniki częściowe. " : "Gotowe. ") +
    `Czas: ${fmtTime(info.elapsed)}`;
  if (info.result) showResult(info.result);
  await loadSaved(jobId);
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
  state.result = res;
  const color = res.params.color;
  const rootFen = res.params.fen;
  const el = $("#tab-traps");
  if (!res.traps.length) {
    el.innerHTML = `<p class="muted">Brak pułapek spełniających kryteria. Spróbuj szerszego zakresu, większej liczby
      pozycji albo łagodniejszego progu oceny.</p>`;
  } else {
    const range = `${res.params.elo_min}–${res.params.elo_max}`;
    const oppName = color === "white" ? "czarnych" : "białych";
    el.innerHTML = `<details class="help"><summary>Jak to czytać?</summary>
      <p>Pułapka to pozycja (po Twoim ruchu), w której <b>przeciwnik często gra naturalny, ale zły ruch</b>.
      „Naturalny” = tak zagrałby typowy gracz ${range} (Maia-2 + statystyki partii), „zły” = silnik pokazuje
      wyraźny spadek jego szans (jak „mistake” na Lichess).</p>
      <p>Każda karta mówi: jaką linię grasz, co przeciwnik najczęściej tu psuje i jakie odpowiedzi trzymają pozycję.
      Wszystkie Twoje ruchy w linii mieszczą się w ustawionym progu oceny. Kliknij kartę, żeby zobaczyć pozycję
      na szachownicy (czerwone strzałki = błędy przeciwnika, zielone = dobre odpowiedzi).</p>
      <p><b>Dojście</b> – jak często przeciwnik z tego zakresu sam doprowadzi do tej pozycji swoimi typowymi ruchami.
      <b>Siła pułapki</b> – średnio ile % szansy wygranej przeciwnik tu oddaje (wyżej = lepiej).
      Oceny zawsze z perspektywy białych.</p></details>
      <p class="muted small">Przeanalizowano ${res.analysed} pozycji, znaleziono ${res.traps.length} pułapek
      (od najlepszych: częste dojście × duży i częsty błąd).</p>` +
      res.traps.map((t, i) => {
        const line = t.line.length ? lineText(rootFen, t.line) : "(od razu w tej pozycji)";
        const mvNo = (san) => {
          const f = t.fen.split(" ");
          return f[5] + (f[1] === "w" ? "." : "…") + san;
        };
        const ev = (d) => fmtEval(myToWhite(d.cp, color));
        const bad = t.bad.slice(0, 3).map((d) =>
          `<li><b class="badmv">${mvNo(d.san)}</b> – zagra to ok. <b>${pctStr(d.p)}</b> graczy, ocena spada do <b>${ev(d)}</b></li>`).join("");
        const good = t.good.slice(0, 3).map((d) => `${mvNo(d.san)} <small>(${pctStr(d.p)}, ${ev(d)})</small>`).join(", ");
        return `<div class="trap" data-i="${i}">
          <div class="trap-head">
            <span class="num">#${i + 1}</span>
            <span class="line">${line}</span>
          </div>
          <div class="trap-body">
            <div class="muted small">Ruch ${oppName}. Naturalne, ale złe:</div>
            <ul class="badlist">${bad}</ul>
            <div class="small">${t.only_move ? '<span class="tag">jedyny dobry ruch</span> ' : '<span class="muted">Dobre odpowiedzi:</span> '}${good}</div>
          </div>
          <div class="trap-meta">
            <span title="Szansa, że przeciwnik swoimi typowymi ruchami doprowadzi do tej pozycji">dojście <b>${pctStr(t.reach)}</b></span>
            <span title="Ile % ludzi popełnia tu błąd">błąd <b>${pctStr(t.error_chance)}</b></span>
            <span title="Średnio ile % szansy wygranej przeciwnik tu oddaje">siła <b>${(t.trick * 100).toFixed(1)}</b></span>
            ${t.verified === true ? '<span class="tag ok" title="Twoje ruchy w linii sprawdzone na większej głębokości">✓ zweryfikowane</span>' : ""}
            <span class="muted" title="Liczba partii z tą pozycją (wybrany zakres rankingu)">${gamesStr(t.local_games, t.lichess_games)}</span>
          </div>
        </div>`;
      }).join("");
    el.querySelectorAll(".trap").forEach((d) => {
      d.onclick = () => {
        el.querySelectorAll(".trap").forEach((x) => x.classList.remove("sel"));
        d.classList.add("sel");
        const t = res.traps[+d.dataset.i];
        loadLine(res, t.line_uci, t);
      };
    });
  }
  renderTree(res);
}

async function loadLine(res, ucis, trap) {
  const hist = JSON.parse(localStorage.getItem("hist-" + res.id) || "null");
  const startFen = hist ? hist.startFen : res.params.fen;
  const all = (hist ? hist.moves : []).concat(ucis);
  const r = await api("/api/line", { method: "POST", body: { fen: startFen, moves: all } });
  state.startFen = startFen;
  state.moves = r.moves;
  state.cursor = r.moves.length;
  state.arrows = [];
  if (trap) {
    trap.bad.slice(0, 3).forEach((d) => state.arrows.push({ from: d.uci.slice(0, 2), to: d.uci.slice(2, 4), color: "#c0392b", opacity: 0.35 + d.p }));
    trap.good.slice(0, 2).forEach((d) => state.arrows.push({ from: d.uci.slice(0, 2), to: d.uci.slice(2, 4), color: "#15781b", opacity: 0.8 }));
  }
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
      if (c.trick >= 0.03) meta.push(`<span class="tag" title="oczekiwana strata przeciwnika w tej pozycji">pułapka ${(c.trick * 100).toFixed(0)}</span>`);
      if (c.verified === true) meta.push('<span class="tag ok">✓</span>');
      const g = gamesStr(c.local_games, c.lichess_games);
      if (g) meta.push(`<span class="muted small">${g}</span>`);
      const sub = build(c, p, depth + 1);
      return `<li><details ${depth < 1 ? "open" : ""}><summary data-path="${p.join(",")}" class="${mine ? "mine" : "opp"}">
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
  sel.innerHTML = '<option value="">— zapisane wyszukiwania —</option>' + r.saved.map((s) =>
    `<option value="${s.id}">${s.created || ""} · ${s.color === "white" ? "białe" : "czarne"} · ${s.elo} · ${s.traps} pułapek</option>`).join("");
  if (selectId) sel.value = selectId;
  if (r.running.length && !state.job) {
    state.job = r.running[0].id;
    $("#btn-cancel").classList.remove("hidden");
    $("#btn-search").disabled = true;
    pollJob();
  }
}

// ------------------------------------------------------------------ status

async function loadStatus() {
  try {
    const s = await api("/api/status");
    const idx = s.index.available
      ? `baza: ${(s.index.meta.games || 0).toLocaleString("pl")} partii`
      : `baza: indeksowanie… ${s.index.log ? s.index.log.replace(/.*\|\s*(\d+\/\d+).*ETA (\d+ min).*/, "$1, zostało ~$2") : ""}`;
    $("#status").innerHTML = [
      `<span class="${s.engine_path_ok ? "ok" : "bad"}">${s.engine !== "?" ? s.engine : "Stockfish"}</span>`,
      `<span class="${s.index.available ? "ok" : "warn"}">${idx}</span>`,
      `<span class="${s.maia_error ? "bad" : "ok"}">Maia-2${s.maia_loaded ? "" : " (ładuje się przy 1. użyciu)"}</span>`,
      `<span class="${!s.lichess_token ? "bad" : s.lichess_error ? "warn" : "ok"}">Lichess${s.lichess_token ? "" : ": brak tokenu"}</span>`,
    ].join("");
    $("#set-info").textContent = s.lichess_error ? "Ostatni błąd Lichess: " + s.lichess_error : "";
  } catch (_) {}
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
$("#btn-settings").onclick = () => { $("#set-token").value = ""; loadStatus(); $("#dlg-settings").showModal(); };
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
$("#saved").onchange = async () => {
  const id = $("#saved").value;
  if (!id) return;
  const r = await api("/api/search/" + id);
  if (r.result) showResult(r.result);
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
  $("#tab-traps").classList.toggle("hidden", b.dataset.tab !== "traps");
  $("#tab-tree").classList.toggle("hidden", b.dataset.tab !== "tree");
}));

updateEvalLabel();
refresh();
loadStatus();
loadSaved();
setInterval(loadStatus, 15000);
