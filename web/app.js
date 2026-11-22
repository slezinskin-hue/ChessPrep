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
    novelty_max_loss: Math.round(+$("#p-nov-loss").value * 100),
    novelty_max_games: +$("#p-nov-games").value,
    theory_min_games: +$("#p-theory").value,
    hard_error: +$("#p-hard-err").value / 100,
    obvious_p: +$("#p-obvious").value / 100,
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
  const tabs = { traps: res.traps || [], hard: res.hard_lines || [], nov: res.novelties || [] };
  $('.tab[data-tab="traps"]').textContent = `Pułapki (${tabs.traps.length})`;
  $('.tab[data-tab="hard"]').textContent = `Trudne linie (${tabs.hard.length})`;
  $('.tab[data-tab="nov"]').textContent = `Nowinki (${tabs.nov.length})`;
  renderTraps(res);
  renderHard(res);
  renderNovelties(res);
  renderTree(res);
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
      if (e.target.closest("[data-sub]")) return;
      el.querySelectorAll("." + cls).forEach((x) => x.classList.remove("sel"));
      d.classList.add("sel");
      onPick(+d.dataset.i, null);
    };
    d.querySelectorAll("[data-sub]").forEach((s) => (s.onclick = (e) => {
      e.stopPropagation();
      el.querySelectorAll("." + cls).forEach((x) => x.classList.remove("sel"));
      d.classList.add("sel");
      onPick(+d.dataset.i, +s.dataset.sub);
    }));
  });
}

function rangeStr(res) { return `${res.params.elo_min}–${res.params.elo_max}`; }

function renderTraps(res) {
  const color = res.params.color;
  const rootFen = res.params.fen;
  const el = $("#tab-traps");
  const traps = res.traps || [];
  const oppName = color === "white" ? "czarnych" : "białych";
  const help = `<details class="help"><summary>Jak to czytać?</summary>
    <p>Pułapka to pozycja (po Twoim ruchu), w której <b>przeciwnik często gra naturalny, ale zły ruch</b>.
    „Naturalny” = tak zagrałby typowy gracz ${rangeStr(res)} (Maia-2 + statystyki partii), „zły” = silnik pokazuje
    spadek jego szansy wygranej o co najmniej 10% (jak „mistake” na Lichess).</p>
    <p><b>Dojście</b> – jak często przeciwnik sam doprowadzi do tej pozycji swoimi typowymi ruchami.
    <b>Błąd</b> – ilu ludzi zagra tu zły ruch. <b>Siła</b> – średnio ile % szansy wygranej przeciwnik tu oddaje.
    <span class="tag hid">ukryta</span> – nawet silnik na małej głębokości wybrałby zły ruch (refutacja jest głęboka).
    <span class="tag">jedyny ruch</span> – tylko jeden ruch nie jest błędem. Oceny zawsze z perspektywy białych.</p></details>`;
  if (!traps.length) {
    el.innerHTML = help + `<p class="muted">Brak pułapek. Spróbuj większej liczby pozycji, innego zakresu rankingu
      albo łagodniejszego progu oceny.</p>`;
    return;
  }
  el.innerHTML = help + `<p class="muted small">Przeanalizowano ${res.analysed} pozycji. Od najlepszych: częste dojście × częsty i duży błąd.</p>` +
    traps.map((t, i) => {
      const line = t.line.length ? lineText(rootFen, t.line) : "(od razu w tej pozycji)";
      const ev = (d) => fmtEval(myToWhite(d.cp, color));
      const bad = t.bad.slice(0, 3).map((d) =>
        `<li><b class="badmv">${mvNo(t.fen, d.san)}</b> – zagra to ok. <b>${pctStr(d.p)}</b> graczy, ocena spada do <b>${ev(d)}</b></li>`).join("");
      const good = t.good.slice(0, 3).map((d) => `${mvNo(t.fen, d.san)} <small>(${pctStr(d.p)}, ${ev(d)})</small>`).join(", ");
      return `<div class="trap" data-i="${i}">
        <div class="trap-head"><span class="num">#${i + 1}</span><span class="line">${line}</span></div>
        <div class="trap-body">
          <div class="muted small">Ruch ${oppName}. Naturalne, ale złe:</div>
          <ul class="badlist">${bad}</ul>
          <div class="small">${t.only_move ? '<span class="tag">jedyny dobry ruch</span> ' : '<span class="muted">Dobre odpowiedzi:</span> '}${good}</div>
        </div>
        <div class="trap-meta">
          <span title="Szansa, że przeciwnik swoimi typowymi ruchami doprowadzi do tej pozycji">dojście <b>${pctStr(t.reach)}</b></span>
          <span title="Ile % ludzi popełnia tu błąd">błąd <b>${pctStr(t.error_chance)}</b></span>
          <span title="Średnio ile % szansy wygranej przeciwnik tu oddaje">siła <b>${(t.trick * 100).toFixed(1)}</b></span>
          ${t.hidden ? '<span class="tag hid" title="Silnik na małej głębokości też wybrałby zły ruch">ukryta</span>' : ""}
          ${t.verified === true ? '<span class="tag ok" title="Twoje ruchy w linii sprawdzone na większej głębokości">✓ zweryfikowane</span>' : ""}
          <span class="muted" title="Liczba partii z tą pozycją (wybrany zakres rankingu)">${gamesStr(t.local_games, t.lichess_games)}</span>
        </div>
      </div>`;
    }).join("");
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
    <b>Dojście</b> = szansa, że w ogóle dojdzie do pierwszego trudnego momentu. Kliknij moment, żeby zobaczyć go na szachownicy.</p></details>`;
  if (!lines.length) {
    el.innerHTML = help + `<p class="muted">Brak trudnych linii. Pomaga większa liczba pozycji, większa głębokość
      wariantu albo obniżenie progu „trudny moment” w ustawieniach zaawansowanych.</p>`;
    return;
  }
  el.innerHTML = help + lines.map((h, i) => {
    const ev = (d) => fmtEval(myToWhite(d.cp, color));
    const moments = h.moments.map((m, j) => {
      const good = m.good.map((g) => `<b class="goodmv">${mvNo(m.fen, g.san)}!</b> <small>(znajduje ${pctStr(g.p)})</small>`).join(" lub ");
      const bad = m.bad.slice(0, 2).map((b) => `${mvNo(m.fen, b.san)} <small>${pctStr(b.p)} → ${ev(b)}</small>`).join(", ");
      const kind = m.type === "only" ? '<span class="tag">jedyny ruch</span>' : '<span class="tag soft">trudny wybór</span>';
      return `<li data-sub="${j}" class="moment" title="Pokaż tę pozycję">
        ${kind} ${m.hidden ? '<span class="tag hid">ukryta</span>' : ""}
        ${good} – myli się <b>${pctStr(m.error)}</b>${bad ? `, np. ${bad}` : ""}</li>`;
    }).join("");
    return `<div class="hline" data-i="${i}">
      <div class="trap-head"><span class="num">#${i + 1}</span><span class="line">${lineText(rootFen, h.line)}</span></div>
      <div class="muted small">${oppName} muszą znaleźć ${h.moments.length} ${h.moments.length === 1 ? "trudny ruch" : "trudne ruchy"}:</div>
      <ol class="moments">${moments}</ol>
      <div class="trap-meta">
        <span title="Szansa, że typowy gracz znajdzie wszystkie trudne ruchy">przejdzie bez błędu <b>${pctStr(h.survive)}</b></span>
        <span title="Szansa, że przeciwnik dojdzie do pierwszego trudnego momentu">dojście <b>${pctStr(h.entry_reach)}</b></span>
        ${h.verified === true ? '<span class="tag ok">✓ zweryfikowane</span>' : ""}
        <span class="muted">${gamesStr(h.local_games, h.lichess_games)}</span>
      </div>
    </div>`;
  }).join("");
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
  el.innerHTML = help + novs.map((n, i) => {
    const before = n.line.slice(0, -1);
    const pre = before.length ? lineText(rootFen, before) : "W pozycji startowej";
    const ev = (cp) => fmtEval(myToWhite(cp, color));
    const replies = (n.replies || []).map((d) => `${mvNo(n.fen, d.san)} <small>${pctStr(d.p)}${d.loss >= 0.1 ? " – błąd → " + ev(d.cp) : ""}</small>`).join(", ");
    const follow = n.follow_trap
      ? `<div class="small" data-sub="1" title="Pokaż pułapkę"><span class="muted">Dalej pułapka:</span> ${lineText(rootFen, n.follow_trap.line)}
         <small>(błąd ${pctStr(n.follow_trap.error)})</small></div>` : "";
    const games = n.games === 0 ? '<span class="tag tn">TN</span> nigdy nie grane' : `grane <b>${n.games}</b>×`;
    return `<div class="nov" data-i="${i}">
      <div class="trap-head"><span class="num">#${i + 1}</span><span class="line">${pre}</span></div>
      <div class="nov-move"><b class="novmv">${mvNo(n.parent_fen, n.san)}!?</b> N
        <span class="muted small">– ${games} na ${(n.parent_games || 0).toLocaleString("pl")} partii w tej pozycji</span></div>
      <div class="small">Ocena <b>${ev(n.eval)}</b> · najlepszy ruch silnika ${n.best_san ? mvNo(n.parent_fen, n.best_san) : "?"} ${ev(n.best_cp)}
        <span class="muted">(różnica ${(n.loss / 100).toFixed(2)})</span></div>
      ${replies ? `<div class="small"><span class="muted">Po nowince:</span> ${replies}</div>` : ""}
      ${follow}
      <div class="trap-meta">
        ${n.verified === true ? '<span class="tag ok">✓ zweryfikowane</span>' : '<span class="muted">niezweryfikowane</span>'}
        ${n.error >= 0.08 ? `<span>od razu błąd u <b>${pctStr(n.error)}</b> ludzi</span>` : ""}
      </div>
    </div>`;
  }).join("");
  bindCards(el, "nov", (i, sub) => {
    const n = novs[i];
    if (sub === 1 && n.follow_trap) loadLine(res, n.follow_trap.line_uci, []);
    else loadLine(res, n.line_uci, [{ from: n.line_uci.at(-1).slice(0, 2), to: n.line_uci.at(-1).slice(2, 4), color: "#2f7bd9", opacity: 0.8 }]);
  });
}

async function loadLine(res, ucis, arrows) {
  const hist = JSON.parse(localStorage.getItem("hist-" + res.id) || "null");
  const startFen = hist ? hist.startFen : res.params.fen;
  const all = (hist ? hist.moves : []).concat(ucis);
  const r = await api("/api/line", { method: "POST", body: { fen: startFen, moves: all } });
  state.startFen = startFen;
  state.moves = r.moves;
  state.cursor = r.moves.length;
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
    `<option value="${s.id}">${s.created || ""} · ${s.color === "white" ? "białe" : "czarne"} · ${s.elo} · ${s.traps} pułapek · ${s.hard ?? 0} linii · ${s.novelties ?? 0} nowinek</option>`).join("");
  if (selectId) sel.value = selectId;
  if (r.running.length && !state.job) {
    state.job = r.running[0].id;
    $("#btn-cancel").classList.remove("hidden");
    $("#btn-search").disabled = true;
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
      `<span class="${s.maia_error ? "bad" : "ok"}">Maia-2${s.maia_loaded ? "" : " (ładuje się przy 1. użyciu)"}</span>`,
      `<span class="${!s.lichess_token ? "bad" : s.lichess_error ? "warn" : "ok"}">Lichess${s.lichess_token ? "" : ": brak tokenu"}</span>`,
    ].join("");
    $("#set-info").textContent = s.lichess_error ? "Ostatni błąd Lichess: " + s.lichess_error : (s.lichess_token ? "Token ustawiony." : "Brak tokenu - statystyki Lichess wyłączone.");
    renderSettings(s);
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
  for (const t of ["traps", "hard", "nov", "tree"]) $("#tab-" + t).classList.toggle("hidden", b.dataset.tab !== t);
}));

updateEvalLabel();
refresh();
loadStatus();
loadSaved();

