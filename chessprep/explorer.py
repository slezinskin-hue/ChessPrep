"""Statystyki partii: explorer Lichess (online) + lokalny indeks bazy PGN.

Explorer Lichess wymaga tokenu (darmowy, lichess.org/account/oauth/token, bez
uprawnień). Odpowiedzi trzymamy w SQLite, żeby nie pytać dwa razy o to samo.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time

import chess
import requests

from .config import CONFIG, DATA_DIR
from .dbindex import GameIndex

LICHESS_BUCKETS = [0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]
DEFAULT_SPEEDS = ["blitz", "rapid", "classical"]
CACHE_TTL = 30 * 24 * 3600  # statystyki zmieniają się powoli

_CASTLE_FIX = {"e1h1": "e1g1", "e1a1": "e1c1", "e8h8": "e8g8", "e8a8": "e8c8"}


def lichess_buckets(elo_min: int, elo_max: int) -> list[int]:
    """Przedziały Lichess (dolne granice) nachodzące na [elo_min, elo_max)."""
    out = []
    for i, lo in enumerate(LICHESS_BUCKETS):
        hi = LICHESS_BUCKETS[i + 1] if i + 1 < len(LICHESS_BUCKETS) else 4000
        if lo < elo_max and hi > elo_min:
            out.append(lo)
    return out or [LICHESS_BUCKETS[-1]]


def _norm_uci(board: chess.Board, uci: str) -> str:
    if uci in _CASTLE_FIX:
        frm = chess.parse_square(uci[:2])
        if board.piece_type_at(frm) == chess.KING:
            return _CASTLE_FIX[uci]
    return uci


class LichessExplorer:
    URL = "https://explorer.lichess.org"

    def __init__(self, token: str | None = None):
        self.token = token if token is not None else CONFIG.get("lichess_token", "")
        DATA_DIR.mkdir(exist_ok=True)
        self.db = sqlite3.connect(DATA_DIR / "lichess.sqlite", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, ts REAL, body TEXT)")
        self.db.commit()
        self._lock = threading.Lock()
        self._last = 0.0
        self._blocked_until = 0.0
        self.error: str | None = None
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, params: dict) -> dict | None:
        req = requests.Request("GET", self.URL + path, params=params).prepare()
        url = req.url
        with self._lock:
            row = self.db.execute("SELECT ts, body FROM cache WHERE url=?", (url,)).fetchone()
        if row and time.time() - row[0] < CACHE_TTL:
            return json.loads(row[1])
        if not self.enabled:
            self.error = "Brak tokenu Lichess"
            return None
        if time.time() < self._blocked_until:
            return None  # po 429 przez minutę nie pytamy (szukanie idzie dalej bez Lichess)
        for attempt in range(2):
            with self._lock:
                wait = 0.25 - (time.time() - self._last)  # nie zasypujemy serwera
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = self.session.get(url, headers={"Authorization": f"Bearer {self.token}"}, timeout=20)
                except requests.RequestException as exc:
                    self.error = f"Błąd sieci: {exc}"
                    return None
                finally:
                    self._last = time.time()
            if r.status_code == 429:
                self.error = "Limit zapytań Lichess - pauza 60 s"
                self._blocked_until = time.time() + 60
                return None
            if r.status_code == 401:
                self.error = "Token Lichess odrzucony (401)"
                return None
            if r.status_code != 200:
                self.error = f"Lichess HTTP {r.status_code}"
                return None
            self.error = None
            body = r.text
            with self._lock:
                self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (url, time.time(), body))
                self.db.commit()
            return json.loads(body)
        return None

    @staticmethod
    def _parse(board: chess.Board, data: dict | None) -> dict:
        if data is None:
            return {"available": False, "total": 0, "moves": []}
        moves = []
        for m in data.get("moves", []):
            n = m["white"] + m["draws"] + m["black"]
            moves.append({"uci": _norm_uci(board, m["uci"]), "games": n, "white": m["white"],
                          "draws": m["draws"], "black": m["black"],
                          "avg_rating": m.get("averageRating")})
        total = data.get("white", 0) + data.get("draws", 0) + data.get("black", 0)
        opening = data.get("opening") or {}
        return {"available": True, "total": total, "moves": moves,
                "opening": f"{opening.get('eco', '')} {opening.get('name', '')}".strip()}

    def query(self, board: chess.Board, elo_min: int, elo_max: int,
              speeds: list[str] | None = None, moves: int = 20) -> dict:
        params = {
            "variant": "standard",
            "fen": board.fen(),
            "speeds": ",".join(speeds or DEFAULT_SPEEDS),
            "ratings": ",".join(str(b) for b in lichess_buckets(elo_min, elo_max)),
            "moves": moves,
            "topGames": 0,
            "recentGames": 0,
        }
        return self._parse(board, self._get("/lichess", params))

    def masters(self, board: chess.Board, moves: int = 20) -> dict:
        return self._parse(board, self._get("/masters", {"fen": board.fen(), "moves": moves, "topGames": 0}))


class Stats:
    """Wspólny dostęp do statystyk z bazy lokalnej i z Lichess."""

    def __init__(self):
        self.local = GameIndex()
        self.lichess = LichessExplorer()

    def reload_local(self) -> None:
        self.local = GameIndex()

    def local_query(self, board: chess.Board, elo_min: int, elo_max: int) -> dict:
        r = self.local.query(board, elo_min, elo_max)
        r["moves"] = [m.as_dict() for m in r["moves"]]
        return r

    def lichess_query(self, board: chess.Board, elo_min: int, elo_max: int,
                      speeds: list[str] | None = None) -> dict:
        return self.lichess.query(board, elo_min, elo_max, speeds)


_shared: Stats | None = None


def shared() -> Stats:
    global _shared
    if _shared is None:
        _shared = Stats()
    return _shared
