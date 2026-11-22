"""Stockfish przez UCI (python-chess) + trwała pamięć ocen w SQLite.

Wszystkie oceny zwracamy z perspektywy BIAŁYCH (UCI podaje je z perspektywy
strony na ruchu - tu to ujednolicamy). Oprócz centypionów bierzemy też WDL
(promile wygranej/remisu/przegranej), z którego liczymy "oczekiwany wynik"
(expected score, 0..1) - to lepsza miara wagi błędu niż same centypiony.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field

import chess
import chess.engine

from .config import CONFIG, DATA_DIR

MATE_CP = 100_000


@dataclass
class Line:
    move: str                 # UCI pierwszego ruchu
    cp: int                   # ocena z perspektywy białych (mat -> +-MATE_CP)
    mate: int | None          # mat w N (dodatnie = białe matują), z perspektywy białych
    wdl: tuple[int, int, int]  # promile (wygrana białych, remis, wygrana czarnych)
    pv: list[str] = field(default_factory=list)
    depth: int = 0

    def es(self, color: chess.Color) -> float:
        """Oczekiwany wynik (0..1) dla podanego koloru."""
        w, d, l = self.wdl
        s = (w + d / 2) / 1000
        return s if color == chess.WHITE else 1 - s

    def cp_for(self, color: chess.Color) -> int:
        return self.cp if color == chess.WHITE else -self.cp

    def as_dict(self) -> dict:
        return {"move": self.move, "cp": self.cp, "mate": self.mate, "wdl": list(self.wdl),
                "pv": self.pv, "depth": self.depth}

    @staticmethod
    def from_dict(d: dict) -> "Line":
        return Line(d["move"], d["cp"], d["mate"], tuple(d["wdl"]), d.get("pv", []), d.get("depth", 0))


def fen_key(board: chess.Board) -> str:
    return board.epd()


class EvalCache:
    def __init__(self, path=None):
        DATA_DIR.mkdir(exist_ok=True)
        self.path = path or DATA_DIR / "evals.sqlite"
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS evals (fen TEXT, root TEXT, multipv INT, depth INT, lines TEXT,"
            " PRIMARY KEY (fen, root, multipv))"
        )
        self.db.commit()

    def get(self, fen: str, root: str, multipv: int, depth: int) -> list[Line] | None:
        with self._lock:
            row = self.db.execute(
                "SELECT lines FROM evals WHERE fen=? AND root=? AND multipv>=? AND depth>=?"
                " ORDER BY depth DESC LIMIT 1", (fen, root, multipv, depth)).fetchone()
        if not row:
            return None
        return [Line.from_dict(d) for d in json.loads(row[0])][:multipv]

    def put(self, fen: str, root: str, multipv: int, depth: int, lines: list[Line]) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO evals VALUES (?,?,?,?,?)",
                (fen, root, multipv, depth, json.dumps([l.as_dict() for l in lines])))
            self.db.commit()


class Engine:
    def __init__(self, path: str | None = None, threads: int | None = None, hash_mb: int | None = None):
        self.path = path or CONFIG["stockfish_path"]
        self.threads = threads or CONFIG["engine_threads"]
        self.hash_mb = hash_mb or CONFIG["engine_hash_mb"]
        self._lock = threading.Lock()
        self._engine: chess.engine.SimpleEngine | None = None
        self.cache = EvalCache()
        self.name = "?"

    def _ensure(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
            self._engine.configure({"Threads": self.threads, "Hash": self.hash_mb, "UCI_ShowWDL": True})
            self.name = self._engine.id.get("name", "?")
        return self._engine

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except Exception:
                    pass
                self._engine = None

    def analyse(self, board: chess.Board, depth: int = 18, multipv: int = 1,
                moves: list[chess.Move] | None = None, use_cache: bool = True) -> list[Line]:
        """Najlepsze linie (posortowane od najlepszej dla strony na ruchu).

        ``moves`` ogranicza analizę do podanych ruchów (searchmoves).
        """
        if board.is_game_over(claim_draw=False):
            return []
        legal = board.legal_moves.count()
        multipv = max(1, min(multipv, len(moves) if moves else legal))
        key = fen_key(board)
        root = ",".join(sorted(m.uci() for m in moves)) if moves else ""
        if use_cache:
            hit = self.cache.get(key, root, multipv, depth)
            if hit is not None:
                return hit
        with self._lock:
            eng = self._ensure()
            infos = eng.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv,
                                root_moves=moves or None)
        if isinstance(infos, dict):
            infos = [infos]
        lines = []
        for info in infos:
            if "pv" not in info or not info["pv"]:
                continue
            score = info["score"].white()
            mate = score.mate()
            cp = score.score(mate_score=MATE_CP)
            wdl_info = info.get("wdl")
            if wdl_info is not None:
                w = wdl_info.white()
                wdl = (w.wins, w.draws, w.losses)
            else:
                m = score.wdl(model="sf")
                wdl = (m.wins, m.draws, m.losses)
            lines.append(Line(info["pv"][0].uci(), cp, mate, wdl,
                              [m.uci() for m in info["pv"][:12]], info.get("depth", depth)))
        side = board.turn
        lines.sort(key=lambda l: -l.cp_for(side))
        if use_cache and lines:
            self.cache.put(key, root, multipv, depth, lines)
        return lines

    def eval_moves(self, board: chess.Board, moves: list[chess.Move], depth: int) -> dict[str, Line]:
        """Ocena konkretnych ruchów (każdy osobno, żeby wynik był dokładny)."""
        out: dict[str, Line] = {}
        for mv in moves:
            res = self.analyse(board, depth=depth, multipv=1, moves=[mv])
            if res:
                out[mv.uci()] = res[0]
        return out


_shared: Engine | None = None


def shared() -> Engine:
    global _shared
    if _shared is None:
        _shared = Engine()
    return _shared
