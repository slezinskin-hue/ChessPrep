"""Lokalny serwer HTTP (FastAPI) - backend okna aplikacji."""
from __future__ import annotations

import io
import json
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
from .config import CONFIG, DATA_DIR, WEB_DIR
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


@app.get("/api/stats")
def stats(fen: str, elo_min: int = 1600, elo_max: int = 2000, lichess: bool = True,
          speeds: str = "blitz,rapid,classical", maia: bool = True, source: str = "players"):
    b = _board(fen)
    st = explorer_mod.shared()
    out: dict = {"local": st.local_query(b, elo_min, elo_max)}
    if lichess and st.lichess.enabled:
        out["lichess"] = st.lichess_query(b, elo_min, elo_max, speeds.split(","), source)
        out["lichess_error"] = st.lichess.error
    if maia:
        probs = human_mod.shared().probs(b, elo_min, elo_max)
        out["maia"] = [{"uci": u, "san": b.san(chess.Move.from_uci(u)), "p": round(p, 4)}
                       for u, p in list(probs.items())[:12]]
        out["maia_error"] = human_mod.shared().error
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
    job = jobs.start(params)
    return job.info()


@app.get("/api/search/{job_id}")
def search_status(job_id: str):
    job = jobs.jobs.get(job_id)
    if job is None:
        res = jobs.load(job_id)
        if res is None:
            raise HTTPException(404, "Brak takiego wyszukiwania")
        return {"id": job_id, "status": "done", "result": res}
    info = job.info()
    if job.status in ("done", "cancelled") and job.result is not None:
        info["result"] = job.result
    return info


@app.post("/api/search/{job_id}/cancel")
def search_cancel(job_id: str):
    job = jobs.jobs.get(job_id)
    if job:
        job.cancel.set()
    return {"ok": True}


@app.get("/api/searches")
def searches():
    running = [j.info() | {"fen": j.params.fen, "color": j.params.color}
               for j in jobs.jobs.values() if j.status in ("queued", "running")]
    return {"running": running, "saved": jobs.saved()}


@app.delete("/api/search/{job_id}")
def search_delete(job_id: str):
    jobs.delete(job_id)
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
        "maia_loaded": maia._model is not None,
        "maia_error": maia.error,
        "search_running": _search_running(),
    }


class SettingsReq(BaseModel):
    lichess_token: str | None = None


@app.post("/api/settings")
def settings(req: SettingsReq):
    if req.lichess_token is not None:
        token = req.lichess_token.strip()
        config_mod.save_local({"lichess_token": token})
        CONFIG["lichess_token"] = token
        explorer_mod.shared().lichess.token = token
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
