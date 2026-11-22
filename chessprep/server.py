"""Lokalny serwer HTTP (FastAPI) - backend okna aplikacji."""
from __future__ import annotations

import io
import json
from dataclasses import fields
from pathlib import Path

import chess
import chess.pgn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import config as config_mod
from . import engine as engine_mod
from . import explorer as explorer_mod
from . import human as human_mod
from .config import CONFIG, DATA_DIR, WEB_DIR
from .search import JobManager, SearchParams

app = FastAPI(title="ChessPrep", version=__version__)
jobs = JobManager()


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
            bad = [d for d in ch.get("moves_info") or [] if d["loss"] >= 0.10]
            good = [d for d in ch.get("moves_info") or [] if d["loss"] < 0.10]
            if bad and sum(d["p"] for d in bad) >= 0.08:
                parts.append("PUŁAPKA: naturalne błędy " + ", ".join(
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

def _index_progress() -> dict:
    log = DATA_DIR / "index_build.log"
    idx = explorer_mod.shared().local
    out = {"available": idx.available, "meta": getattr(idx, "meta", None)}
    if not idx.available and log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        out["log"] = lines[-1] if lines else ""
    return out


@app.get("/api/status")
def status():
    st = explorer_mod.shared()
    if not st.local.available and (Path(CONFIG["index_dir"]) / "meta.json").exists():
        st.reload_local()
    maia = human_mod.shared()
    return {
        "version": __version__,
        "engine": engine_mod.shared().name,
        "engine_path_ok": Path(CONFIG["stockfish_path"]).exists(),
        "index": _index_progress(),
        "lichess_token": bool(st.lichess.token),
        "lichess_error": st.lichess.error,
        "maia_loaded": maia._model is not None,
        "maia_error": maia.error,
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


# ------------------------------------------------------------------ pliki strony

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
