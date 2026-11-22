// Prosta szachownica bez zależności: przeciąganie i klikanie, podświetlenia, strzałki.
"use strict";

class Board {
  constructor(el, opts = {}) {
    this.el = el;
    this.onMove = opts.onMove || (() => {});
    this.orientation = "white";
    this.pieces = {};       // "e4" -> "wP"
    this.dests = {};        // "e2" -> ["e3","e4"]
    this.lastMove = null;   // ["e2","e4"]
    this.check = null;      // pole króla w szachu
    this.arrows = [];       // [{from,to,color}]
    this.selected = null;
    this.drag = null;
    this._build();
  }

  // Figury "cburnett" (domyślny zestaw Lichess, autor Colin M.L. Burnett, GPLv2+).
  static pieceUrl(p) { return `url("pieces/${p}.svg")`; }

  _build() {
    this.el.innerHTML = "";
    this.el.classList.add("cp-board");
    this.squaresEl = document.createElement("div");
    this.squaresEl.className = "cp-squares";
    this.el.appendChild(this.squaresEl);
    this.svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.svg.setAttribute("viewBox", "0 0 800 800");
    this.svg.classList.add("cp-arrows");
    this.el.appendChild(this.svg);
    this.squaresEl.addEventListener("pointerdown", (e) => this._down(e));
    window.addEventListener("pointermove", (e) => this._moveDrag(e));
    window.addEventListener("pointerup", (e) => this._up(e));
    this.el.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  set(state) {
    if (state.fen !== undefined) this.pieces = Board.parseFen(state.fen);
    for (const k of ["orientation", "dests", "lastMove", "check", "arrows"]) {
      if (state[k] !== undefined) this[k] = state[k];
    }
    this.selected = null;
    this.render();
  }

  static parseFen(fen) {
    const out = {};
    const rows = fen.split(" ")[0].split("/");
    rows.forEach((row, ri) => {
      let file = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { file += +ch; continue; }
        const sq = "abcdefgh"[file] + (8 - ri);
        out[sq] = (ch === ch.toUpperCase() ? "w" : "b") + ch.toUpperCase();
        file++;
      }
    });
    return out;
  }

  _squares() {
    const files = "abcdefgh".split("");
    const ranks = [8, 7, 6, 5, 4, 3, 2, 1];
    if (this.orientation === "black") { files.reverse(); ranks.reverse(); }
    const out = [];
    for (const r of ranks) for (const f of files) out.push(f + r);
    return out;
  }

  _xy(sq) { // środek pola w układzie 0..800
    let f = "abcdefgh".indexOf(sq[0]), r = +sq[1] - 1;
    if (this.orientation === "black") { f = 7 - f; r = 7 - r; }
    return [f * 100 + 50, (7 - r) * 100 + 50];
  }

  render() {
    const frag = document.createDocumentFragment();
    const sqs = this._squares();
    sqs.forEach((sq, i) => {
      const d = document.createElement("div");
      const f = "abcdefgh".indexOf(sq[0]), r = +sq[1];
      d.className = "cp-sq " + ((f + r) % 2 === 1 ? "dark" : "light"); // a1 (f=0, r=1) = ciemne
      d.dataset.sq = sq;
      if (this.lastMove && this.lastMove.includes(sq)) d.classList.add("last");
      if (this.check === sq) d.classList.add("check");
      if (this.selected === sq) d.classList.add("sel");
      if (this.selected && (this.dests[this.selected] || []).includes(sq)) {
        d.classList.add(this.pieces[sq] ? "cap" : "dest");
      }
      const p = this.pieces[sq];
      if (p) {
        const s = document.createElement("div");
        s.className = "cp-pc";
        s.style.backgroundImage = Board.pieceUrl(p);
        if (this.drag && this.drag.from === sq) s.classList.add("ghost");
        d.appendChild(s);
      }
      if (i % 8 === 0) d.insertAdjacentHTML("beforeend", `<i class="coord rank">${sq[1]}</i>`);
      if (i >= 56) d.insertAdjacentHTML("beforeend", `<i class="coord file">${sq[0]}</i>`);
      frag.appendChild(d);
    });
    this.squaresEl.replaceChildren(frag);
    this._renderArrows();
  }

  _renderArrows() {
    const ns = "http://www.w3.org/2000/svg";
    this.svg.replaceChildren();
    const defs = document.createElementNS(ns, "defs");
    this.svg.appendChild(defs);
    this.arrows.forEach((a, i) => {
      const color = a.color || "#15781b";
      const id = "ah" + i;
      const m = document.createElementNS(ns, "marker");
      m.setAttribute("id", id); m.setAttribute("orient", "auto");
      m.setAttribute("markerWidth", "4"); m.setAttribute("markerHeight", "4");
      m.setAttribute("refX", "2.05"); m.setAttribute("refY", "2");
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", "M0,0 V4 L3,2 Z"); path.setAttribute("fill", color);
      m.appendChild(path); defs.appendChild(m);
      const [x1, y1] = this._xy(a.from), [x2, y2] = this._xy(a.to);
      const len = Math.hypot(x2 - x1, y2 - y1), k = (len - 30) / len;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", x1); line.setAttribute("y1", y1);
      line.setAttribute("x2", x1 + (x2 - x1) * k); line.setAttribute("y2", y1 + (y2 - y1) * k);
      line.setAttribute("stroke", color); line.setAttribute("stroke-width", a.width || 15);
      line.setAttribute("stroke-linecap", "round"); line.setAttribute("opacity", a.opacity || 0.75);
      line.setAttribute("marker-end", `url(#${id})`);
      this.svg.appendChild(line);
    });
  }

  _sqAt(x, y) {
    const el = document.elementFromPoint(x, y);
    const sq = el && el.closest(".cp-sq");
    return sq ? sq.dataset.sq : null;
  }

  _down(e) {
    if (e.button !== 0) return;
    const sq = this._sqAt(e.clientX, e.clientY);
    if (!sq) return;
    if (this.selected && (this.dests[this.selected] || []).includes(sq)) {
      const from = this.selected;
      this.selected = null;
      this.onMove(from, sq);
      return;
    }
    if (this.dests[sq]) {
      this.selected = sq;
      const p = this.pieces[sq];
      const float = document.createElement("div");
      float.className = "cp-pc cp-float";
      float.style.backgroundImage = Board.pieceUrl(p);
      const size = this.squaresEl.getBoundingClientRect().width / 8;
      float.style.width = float.style.height = size + "px";
      document.body.appendChild(float);
      this.drag = { from: sq, float, size, moved: false, x: e.clientX, y: e.clientY };
      this._place(e);
      this.render();
    } else {
      this.selected = null;
      this.render();
    }
  }

  _place(e) {
    const d = this.drag;
    d.float.style.left = e.clientX - d.size / 2 + "px";
    d.float.style.top = e.clientY - d.size / 2 + "px";
  }

  _moveDrag(e) {
    if (!this.drag) return;
    if (Math.hypot(e.clientX - this.drag.x, e.clientY - this.drag.y) > 4) this.drag.moved = true;
    this._place(e);
  }

  _up(e) {
    if (!this.drag) return;
    const d = this.drag;
    this.drag = null;
    d.float.remove();
    const to = this._sqAt(e.clientX, e.clientY);
    if (d.moved && to && to !== d.from && (this.dests[d.from] || []).includes(to)) {
      this.selected = null;
      this.onMove(d.from, to);
      return;
    }
    if (d.moved) this.selected = null;
    this.render();
  }
}
