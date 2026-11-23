"""Lokalny serwer HTTP (FastAPI) - backend okna aplikacji."""
from __future__ import annotations

import io
import json
import math
import time
from dataclasses import fields
from pathlib import Path

import chess
import chess.pgn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import config as config_mod
from . import databases as db_mod
from . import engine as engine_mod
from . import explorer as explorer_mod
from . import human as human_mod
from . import opponent as opponent_mod
from .config import CONFIG, DATA_DIR, WEB_DIR
from . import search as search_mod
from . import study as study_mod
from .search import JobManager, SearchParams

app = FastAPI(title="ChessPrep", version=__version__)
jobs = JobManager()

# indeks bazy: po zbudowaniu/zmianie przeładuj statystyki, przed budową zwolnij pliki
_idx = db_mod.shared()
_idx.on_ready = lambda: explorer_mod.shared().reload_local()
_idx.on_release = lambda: explorer_mod.shared().release_local()


@app.exception_handler(engine_mod.EngineMissing)
def _engine_missing(_: Request, exc: engine_mod.EngineMissing):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _board(fen: str) -> chess.Board:
    try:
        return chess.Board(fen)
    except ValueError as exc:
        raise HTTPException(400, f"Niepoprawny FEN: {exc}")


def _dests(board: chess.Board) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in board.legal_moves:
        out.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
    return out


def _san_pv(board: chess.Board, pv: list[str]) -> list[str]:
    b = board.copy(stack=False)
    out = []
    for u in pv:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            break
        out.append(b.san(mv))
        b.push(mv)
    return out


def _position(board: chess.Board) -> dict:
    return {"fen": board.fen(), "turn": "white" if board.turn else "black", "dests": _dests(board),
            "check": board.is_check(), "game_over": board.is_game_over(),
            "result": board.result() if board.is_game_over() else None}


# ------------------------------------------------------------------ pozycja

@app.get("/api/position")
def position(fen: str = chess.STARTING_FEN):
    return _position(_board(fen))


class MoveReq(BaseModel):
    fen: str
    uci: str | None = None
    san: str | None = None


@app.post("/api/move")
def move(req: MoveReq):
    b = _board(req.fen)
    try:
        if req.uci:
            mv = chess.Move.from_uci(req.uci)
            if mv not in b.legal_moves and len(req.uci) == 4:
                mv = chess.Move.from_uci(req.uci + "q")
            if mv not in b.legal_moves:
                raise ValueError(req.uci)
        else:
            mv = b.parse_san(req.san or "")
    except ValueError:
        raise HTTPException(400, "Nielegalny ruch")
    san = b.san(mv)
    b.push(mv)
    return {"san": san, "uci": mv.uci(), **_position(b)}


class LineReq(BaseModel):
    fen: str
    moves: list[str]


@app.post("/api/line")
def line(req: LineReq):
    """Rozwija listę ruchów UCI od pozycji -> SAN i FEN po każdym ruchu."""
    b = _board(req.fen)
    out = []
    for u in req.moves:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            break
        san = b.san(mv)
        b.push(mv)
        out.append({"san": san, "uci": u, "fen": b.fen()})
    return {"moves": out}


class PgnReq(BaseModel):
    pgn: str


@app.post("/api/parse_pgn")
def parse_pgn(req: PgnReq):
    text = req.pgn.strip()
    # sam FEN?
    try:
        b = chess.Board(text)
        return {"start_fen": b.fen(), "moves": [], **_position(b)}
    except ValueError:
        pass
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None:
        raise HTTPException(400, "Nie rozpoznano PGN ani FEN")
    b = game.board()
    start = b.fen()
    moves = []
    for mv in game.mainline_moves():
        moves.append({"san": b.san(mv), "uci": mv.uci()})
        b.push(mv)
    return {"start_fen": start, "moves": moves, **_position(b)}


# ------------------------------------------------------------------ analiza

@app.get("/api/analyse")
def analyse(fen: str, depth: int = 16, multipv: int = 3):
    b = _board(fen)
    depth = max(6, min(30, depth))
    lines = engine_mod.shared().analyse(b, depth=depth, multipv=max(1, min(6, multipv)))
    return {"engine": engine_mod.shared().name, "lines": [
        {**l.as_dict(), "san": _san_pv(b, l.pv), "es_white": round(l.es(chess.WHITE), 3)} for l in lines]}


def _history_boards(hist_fen: str, hist_moves: str) -> list[chess.Board]:
    """Poprzednie pozycje (dla Mai-3) z pozycji startowej i listy ruchów UCI."""
    if not hist_fen:
        return []
    try:
        b = chess.Board(hist_fen)
        out = []
        for u in [m for m in hist_moves.split(",") if m]:
            out.append(b.copy(stack=False))
            b.push(chess.Move.from_uci(u))
        return out[-8:]
    except (ValueError, AssertionError):
        return []


@app.get("/api/stats")
def stats(fen: str, elo_min: int = 1600, elo_max: int = 2000, lichess: bool = True,
          speeds: str = "blitz,rapid,classical", maia: bool = True, source: str = "players",
          hist_fen: str = "", hist_moves: str = "", opponent: str = ""):
    b = _board(fen)
    st = explorer_mod.shared()
    out: dict = {"local": st.local_query(b, elo_min, elo_max)}
    if lichess and st.lichess.enabled:
        out["lichess"] = st.lichess_query(b, elo_min, elo_max, speeds.split(","), source)
        out["lichess_error"] = st.lichess.error
    if maia:
        hm = human_mod.shared()
        probs = hm.probs(b, elo_min, elo_max, _history_boards(hist_fen, hist_moves))
        out["maia"] = [{"uci": u, "san": b.san(chess.Move.from_uci(u)), "p": round(p, 4)}
                       for u, p in list(probs.items())[:12]]
        out["maia_error"] = hm.error
        out["human_model"] = hm.label
    book = opponent_mod.shared().get(opponent) if opponent else None
    if book is not None:
        n, moves = book.at(b)
        out["opponent"] = {"name": book.meta["name"], "reached": n, "moves": [
            {"uci": u, "san": b.san(chess.Move.from_uci(u)), "games": m[0], "score": round(m[1] / m[0], 2)}
            for u, m in sorted(moves.items(), key=lambda kv: -kv[1][0]) if b.is_legal(chess.Move.from_uci(u))]}
    for src in ("local", "lichess"):
        if src in out:
            for m in out[src]["moves"]:
                try:
                    m["san"] = b.san(chess.Move.from_uci(m["uci"]))
                except ValueError:
                    m["san"] = m["uci"]
    return out


# ------------------------------------------------------------------ wyszukiwanie

class SearchReq(BaseModel):
    params: dict


@app.post("/api/search")
def search_start(req: SearchReq):
    allowed = {f.name for f in fields(SearchParams)}
    params = SearchParams(**{k: v for k, v in req.params.items() if k in allowed})
    _board(params.fen)
    if params.deadline and params.deadline < time.time() + search_mod.MIN_TIME_LEFT:
        raise HTTPException(400, "Godzina końca musi być co najmniej 5 minut od teraz")
    job = jobs.start(params)
    return job.info()


@app.get("/api/search/{job_id}")
def search_status(job_id: str, partial: bool = False):
    job = jobs.jobs.get(job_id)
    if job is None:
        res = jobs.load(job_id)
        if res is None:
            raise HTTPException(404, "Brak takiego wyszukiwania")
        return {"id": job_id, "status": "done", "result": res}
    info = job.info()
    if job.status in ("done", "cancelled") and job.result is not None:
        info["result"] = job.result
    elif job.status == "running":
        f = jobs.dir / f"{job_id}.json"
        info["partial_available"] = f.exists()
        if partial and f.exists():
            info["result"] = json.loads(f.read_text(encoding="utf-8"))
    return info


@app.post("/api/search/{job_id}/cancel")
def search_cancel(job_id: str):
    job = jobs.jobs.get(job_id)
    if job:
        jobs.cancel(job)
    return {"ok": True}


@app.post("/api/search/{job_id}/finish")
def search_finish(job_id: str):
    """Zakończ szukanie teraz, ale zweryfikuj wyniki."""
    job = jobs.jobs.get(job_id)
    if job:
        job.finish.set()
    return {"ok": True}


@app.get("/api/searches")
def searches():
    running = [j.info() | {"fen": j.params.fen, "color": j.params.color}
               for j in jobs.jobs.values() if j.status in ("queued", "running")]
    return {"running": running, "saved": jobs.saved()}


@app.get("/api/queue")
def queue():
    """Wyszukiwania czekające w kolejce (w kolejności uruchamiania)."""
    return {"queued": [{"id": j.id, "color": j.params.color, "budget": j.params.budget,
                        "deadline": j.params.deadline or None, "elo": f"{j.params.elo_min}-{j.params.elo_max}"}
                       for j in jobs.queued()]}


@app.delete("/api/search/{job_id}")
def search_delete(job_id: str):
    jobs.delete(job_id)
    allm = _marks_all()
    if allm.pop(job_id, None) is not None:
        MARKS_FILE.write_text(json.dumps(allm, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}


def _tree_to_pgn(result: dict) -> str:
    params = result["params"]
    board = chess.Board(params["fen"])
    game = chess.pgn.Game()
    if params["fen"] != chess.STARTING_FEN:
        game.setup(board)
    game.headers["Event"] = f"ChessPrep: {params['color']} {params['elo_min']}-{params['elo_max']}"
    game.headers["Annotator"] = "ChessPrep (Stockfish + Maia-2)"

    def fmt_cp(cp):
        if cp is None:
            return "?"
        side_cp = cp if params["color"] == "white" else -cp  # z perspektywy białych
        return f"{side_cp / 100:+.2f}"

    def add(gnode, tnode):
        for ch in tnode["children"]:
            mv = chess.Move.from_uci(ch["uci"])
            if mv not in gnode.board().legal_moves:
                continue
            g = gnode.add_variation(mv)
            parts = [f"ocena {fmt_cp(ch['eval'])}"]
            nv = ch.get("novelty")
            if nv:
                g.nags.add(chess.pgn.NAG_NOVELTY)
                g.nags.add(chess.pgn.NAG_SPECULATIVE_MOVE)
                parts.append("NOWINKA: " + ("nigdy nie grane" if nv.get("tn") else f"grane {nv['games']}×")
                             + f" na {nv.get('parent_games') or 0} partii")
            # ruch przeciwnika, który był "trudnym momentem" w pozycji rodzica
            pm = tnode.get("moment")
            if pm and any(x["uci"] == ch["uci"] for x in pm["good"]):
                g.nags.add(chess.pgn.NAG_GOOD_MOVE)
            bad = [d for d in ch.get("moves_info") or [] if d["loss"] >= 0.10]
            good = [d for d in ch.get("moves_info") or [] if d["loss"] < 0.10]
            err = ch.get("error") or sum(d["p"] for d in bad)
            if bad and err >= 0.08:
                label = "PUŁAPKA"
                if ch.get("moment"):
                    label = "JEDYNY RUCH" if ch["moment"]["type"] == "only" else "TRUDNY MOMENT"
                if ch.get("hidden"):
                    label += " (ukryta)"
                parts.append(f"{label}: myli się {err * 100:.0f}%, naturalne błędy " + ", ".join(
                    f"{d['san']} ({d['p'] * 100:.0f}%, {fmt_cp(d['cp'])})" for d in bad[:3]))
                parts.append("dobre: " + ", ".join(f"{d['san']} ({d['p'] * 100:.0f}%)" for d in good[:3]))
            g.comment = "; ".join(parts)
            add(g, ch)

    add(game, result["tree"])
    return str(game)


@app.get("/api/search/{job_id}/pgn", response_class=PlainTextResponse)
def search_pgn(job_id: str):
    res = jobs.load(job_id)
    if res is None:
        raise HTTPException(404, "Brak wyników")
    return _tree_to_pgn(res)


def _result(job_id: str) -> dict:
    job = jobs.jobs.get(job_id)
    res = job.result if job is not None and job.result is not None else jobs.load(job_id)
    if res is None:
        raise HTTPException(404, "Brak wyników")
    return res


# ------------------------------------------------------------------ kara za błąd

class RefuteReq(BaseModel):
    fen: str
    uci: str
    depth: int = 18


@app.post("/api/refute")
def refute(req: RefuteReq):
    """Jak ukarać zły ruch: linia silnika zaczynająca się od tego ruchu."""
    b = _board(req.fen)
    try:
        mv = chess.Move.from_uci(req.uci)
    except ValueError:
        raise HTTPException(400, "Zły ruch")
    if not b.is_legal(mv):
        raise HTTPException(400, "Nielegalny ruch")
    lines = engine_mod.shared().analyse(b, depth=max(8, min(26, req.depth)), multipv=1, moves=[mv])
    if not lines:
        raise HTTPException(500, "Silnik nie zwrócił analizy")
    return {"pv": search_mod.refutation(b, lines[0].pv), "cp": lines[0].cp}


# ------------------------------------------------------------------ prawdziwe partie

def _real_source(r: dict, bad: set[str], white: bool) -> dict | None:
    if not r.get("available") or not r.get("total") or r.get("beyond_index"):
        return None
    moves = r["moves"]
    get = (lambda m, k: m[k]) if moves and isinstance(moves[0], dict) else (lambda m, k: getattr(m, k))

    def score(ms) -> tuple[int, float | None]:
        n = sum(get(m, "games") for m in ms)
        if not n:
            return 0, None
        w = sum(get(m, "white") for m in ms)
        bl = sum(get(m, "black") for m in ms)
        d = sum(get(m, "draws") for m in ms)
        mine = w if white else bl
        return n, round((mine + d / 2) / n, 3)

    bad_moves = [m for m in moves if get(m, "uci") in bad]
    fell, after_bad = score(bad_moves)
    _, overall = score(moves)
    out = {"total": r["total"], "fell": fell, "fell_pct": round(fell / r["total"], 3),
           "fell_ci": wilson(fell, r["total"]), "score_after_bad": after_bad, "score_overall": overall,
           "small": r["total"] < 20}
    if fell and after_bad is not None:
        out["score_ci"] = wilson(after_bad * fell, fell)
    return out


def wilson(k: float, n: int, z: float = 1.96) -> list[float] | None:
    """95% przedział ufności Wilsona dla odsetka k/n."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


@app.get("/api/search/{job_id}/real")
def search_real(job_id: str):
    """Pułapki w prawdziwych partiach: ilu ludzi wpadło i jak potem kończyły się partie."""
    res = _result(job_id)
    p = res["params"]
    st = explorer_mod.shared()
    white = p["color"] == "white"
    out = []
    for t in res.get("traps") or []:
        b = chess.Board(t["fen"])
        bad = {d["uci"] for d in t.get("bad") or []}
        row = {"local": None, "lichess": None}
        if p.get("use_local_db", True) and st.local.available:
            row["local"] = _real_source(st.local.query(b, p["elo_min"], p["elo_max"]), bad, white)
        if p.get("use_lichess", True) and st.lichess.enabled:
            row["lichess"] = _real_source(st.lichess_query(b, p["elo_min"], p["elo_max"], p.get("speeds"),
                                                           p.get("lichess_source", "players")), bad, white)
        out.append(row)
    return {"traps": out, "lichess_source": p.get("lichess_source", "players"),
            "lichess_error": st.lichess.error}


# ------------------------------------------------------------------ gwiazdki i notatki

MARKS_FILE = DATA_DIR / "marks.json"


def _marks_all() -> dict:
    try:
        return json.loads(MARKS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@app.get("/api/search/{job_id}/marks")
def marks_get(job_id: str):
    return _marks_all().get(job_id, {})


class MarkReq(BaseModel):
    key: str
    star: bool | None = None
    note: str | None = None


@app.post("/api/search/{job_id}/marks")
def marks_set(job_id: str, req: MarkReq):
    allm = _marks_all()
    mine = allm.setdefault(job_id, {})
    m = mine.setdefault(req.key, {})
    if req.star is not None:
        m["star"] = req.star
    if req.note is not None:
        m["note"] = req.note.strip()[:2000]
    if not m.get("star") and not m.get("note"):
        mine.pop(req.key, None)
    if not mine:
        allm.pop(job_id, None)
    DATA_DIR.mkdir(exist_ok=True)
    tmp = MARKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(allm, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(MARKS_FILE)
    return mine


# ------------------------------------------------------------------ eksport rozdziałów / studium Lichess

class ExportReq(BaseModel):
    kind: str = "traps"                 # traps | hard | nov
    groups: list[dict]                  # [{"name": str, "items": [indeksy]}]
    prefix: dict | None = None          # {"start_fen", "moves"} - droga do pozycji wyszukiwania
    notes: dict[str, str] | None = None  # {indeks: notatka}
    study: str | None = None            # adres/ID istniejącego studium; puste = nowe studium
    new_name: str | None = None
    visibility: str = "private"


def _chapters(job_id: str, req: ExportReq) -> list[dict]:
    if req.kind not in ("traps", "hard", "nov"):
        raise HTTPException(400, "Zły rodzaj eksportu")
    notes = {int(k): v for k, v in (req.notes or {}).items() if v}
    res = _result(job_id)
    prefix = req.prefix
    pr = res.get("params", {})
    if pr.get("pre_fen"):  # historia zapisana z wyszukiwaniem ma pierwszeństwo
        prefix = {"start_fen": pr["pre_fen"], "moves": pr.get("pre_moves") or []}
    return study_mod.build_chapters(res, req.kind, req.groups, prefix, notes)


@app.post("/api/search/{job_id}/chapters_pgn", response_class=PlainTextResponse)
def chapters_pgn(job_id: str, req: ExportReq):
    return "\n\n".join(c["pgn"] for c in _chapters(job_id, req))


@app.get("/api/lichess/token")
def lichess_token():
    token = explorer_mod.shared().lichess.token
    try:
        info = study_mod.token_info(token)
    except study_mod.StudyError as exc:
        return {"valid": None, "error": str(exc), "create_url": study_mod.TOKEN_URL}
    return {**info, "create_url": study_mod.TOKEN_URL}


@app.post("/api/search/{job_id}/lichess_study")
def lichess_study(job_id: str, req: ExportReq):
    chapters = _chapters(job_id, req)
    color = _result(job_id)["params"]["color"]
    if req.visibility not in ("private", "unlisted", "public"):
        req.visibility = "private"
    try:
        return study_mod.export_to_lichess(explorer_mod.shared().lichess.token, chapters, color,
                                           req.study or None, req.new_name, req.visibility)
    except study_mod.StudyError as exc:
        raise HTTPException(400, str(exc))


# ------------------------------------------------------------------ status/ustawienia

def _search_running() -> bool:
    return any(j.status in ("queued", "running") for j in jobs.jobs.values())


@app.get("/api/status")
def status():
    st = explorer_mod.shared()
    idx = db_mod.shared()
    if not st.local.available and not idx.building:
        d = db_mod.active_index_dir()
        if d is not None and (d / "meta.json").exists():
            st.reload_local()
    maia = human_mod.shared()
    eng = engine_mod.shared()
    return {
        "version": __version__,
        "engine": eng.name,
        "engine_path": eng.path,
        "engine_path_ok": bool(eng.path and Path(eng.path).is_file()),
        "database": idx.status(),
        "index_available": st.local.available,
        "lichess_token": bool(st.lichess.token),
        "lichess_error": st.lichess.error,
        "maia_loaded": maia.loaded,
        "maia_error": maia.error,
        "human_model": maia.label,
        "human_model_setting": CONFIG.get("human_model", "auto"),
        "maia3_installed": human_mod.maia3_installed(),
        "search_running": _search_running(),
        "queued": len(jobs.queued()),
    }


class SettingsReq(BaseModel):
    lichess_token: str | None = None
    human_model: str | None = None


@app.post("/api/settings")
def settings(req: SettingsReq):
    if req.lichess_token is not None:
        token = req.lichess_token.strip()
        config_mod.save_local({"lichess_token": token})
        CONFIG["lichess_token"] = token
        explorer_mod.shared().lichess.token = token
    if req.human_model is not None:
        name = req.human_model.strip().lower()
        if name != "auto" and name not in human_mod.MODELS:
            raise HTTPException(400, "Nieznany model")
        if _search_running():
            raise HTTPException(409, "Trwa wyszukiwanie - zmień model po jego zakończeniu")
        config_mod.save_local({"human_model": name})
        CONFIG["human_model"] = name
    return {"ok": True, "human_model": human_mod.shared().label}


# ------------------------------------------------------------------ przeciwnik

class OpponentReq(BaseModel):
    source: str
    name: str
    max_games: int = 2000


@app.post("/api/opponent/load")
def opponent_load(req: OpponentReq):
    try:
        opponent_mod.shared().start(req.source, req.name, max(50, min(10000, req.max_games)))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return opponent_mod.shared().status()


@app.get("/api/opponent/status")
def opponent_status():
    return opponent_mod.shared().status()


@app.get("/api/opponents")
def opponents():
    return {"books": opponent_mod.shared().list(), "sources": opponent_mod.SOURCES}


@app.delete("/api/opponent/{key}")
def opponent_delete(key: str):
    try:
        opponent_mod.shared().delete(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


_FILE_TYPES = {
    "engine": (("Silnik szachowy (*.exe)", "*.exe"), ("Wszystkie pliki (*.*)", "*.*")),
    "pgn": (("Partie PGN (*.pgn)", "*.pgn"), ("Wszystkie pliki (*.*)", "*.*")),
}


def _pick_file(kind: str) -> str | None:
    """Systemowe okno wyboru pliku: okno aplikacji (pywebview) albo tkinter."""
    types = _FILE_TYPES[kind]
    try:
        import webview
        if webview.windows:
            res = webview.windows[0].create_file_dialog(
                webview.FileDialog.OPEN, file_types=tuple(f"{d.split(' (')[0]} ({p})" for d, p in types))
            if not res:
                return None
            return res[0] if isinstance(res, (list, tuple)) else str(res)
    except ImportError:
        pass
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    title = "Wybierz silnik szachowy" if kind == "engine" else "Wybierz bazę partii (PGN)"
    path = filedialog.askopenfilename(title=title, filetypes=[(d, p) for d, p in types], parent=root)
    root.destroy()
    return path or None


class PickReq(BaseModel):
    kind: str


@app.post("/api/pick_file")
def pick_file(req: PickReq):
    if req.kind not in _FILE_TYPES:
        raise HTTPException(400, "Nieznany rodzaj pliku")
    return {"path": _pick_file(req.kind)}


class PathReq(BaseModel):
    path: str


@app.post("/api/engine")
def set_engine(req: PathReq):
    if _search_running():
        raise HTTPException(409, "Trwa wyszukiwanie - poczekaj albo je przerwij.")
    try:
        name = engine_mod.set_engine(req.path.strip().strip('"'))
    except engine_mod.EngineMissing as exc:
        raise HTTPException(400, str(exc))
    return {"name": name, "path": req.path}


@app.post("/api/database")
def set_database(req: PathReq):
    if _search_running():
        raise HTTPException(409, "Trwa wyszukiwanie - poczekaj albo je przerwij.")
    path = req.path.strip().strip('"')
    try:
        return db_mod.shared().select(path)
    except FileNotFoundError:
        raise HTTPException(400, f"Nie ma pliku: {path}")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/database/build")
def database_build():
    try:
        db_mod.shared().start_build()
    except FileNotFoundError as exc:
        raise HTTPException(400, f"Nie ma pliku bazy: {exc}")
    return db_mod.shared().status()


@app.post("/api/database/cancel")
def database_cancel():
    db_mod.shared().cancel()
    return db_mod.shared().status()


@app.post("/api/database/clear")
def database_clear():
    try:
        db_mod.shared().clear()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return db_mod.shared().status()


# ------------------------------------------------------------------ pliki strony

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


class _NoCacheStatic(StaticFiles):
    """Po aktualizacji programu okno ma od razu użyć nowych plików JS/CSS
    (no-cache = przeglądarka zawsze pyta serwer; niezmienione pliki i tak idą jako 304)."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", _NoCacheStatic(directory=WEB_DIR), name="web")
