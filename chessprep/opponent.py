"""Przygotowanie pod konkretnego przeciwnika.

Pobieramy partie gracza (Lichess, chess.com albo z lokalnej bazy PGN po nazwisku) i budujemy
jego "książkę": dla każdej pozycji, w której on był na ruchu (pierwsze ``MAX_PLIES``
półruchów), ile razy do niej doszedł, jakie ruchy grał i jaki miał po nich wynik.

W szukaniu (``search.HumanPolicy``) jego własne ruchy mieszamy z ogólnym modelem:
waga jego statystyk = n / (n + 3), gdzie n to liczba jego partii z tą pozycją.

Książki zapisują się w ``data/opponents/<źródło>-<nick>.json``.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from pathlib import Path

import chess
import chess.pgn
import requests

from .config import CONFIG, DATA_DIR

DIR = DATA_DIR / "opponents"
MAX_PLIES = 40
HEADERS = {"User-Agent": "ChessPrep (github.com/slezinskin-hue/ChessPrep)"}
SOURCES = {"lichess": "Lichess", "chesscom": "chess.com", "local": "baza lokalna"}


def _slug(name: str) -> str:
    s = re.sub(r"[^\w.-]+", "_", name.strip().lower())
    return re.sub(r"\.{2,}", ".", s)[:60] or "gracz"


def book_key(source: str, name: str) -> str:
    return f"{source}-{_slug(name)}"


KEY_RE = re.compile(r"^[a-z]+-[\w.-]+$")


def valid_key(key: str | None) -> bool:
    """Klucz książki ma postać ``źródło-nick`` - bez ukośników i ``..`` (nie wyjdzie poza folder)."""
    return bool(key and KEY_RE.match(key)) and ".." not in key


def _path(key: str) -> Path:
    if not valid_key(key):
        raise ValueError("Zły klucz przeciwnika")
    return DIR / f"{key}.json"


class Book:
    """Pozycje, w których przeciwnik był na ruchu: epd -> [n, {uci: [ile, suma wyników]}]."""

    def __init__(self, meta: dict, positions: dict):
        self.meta = meta
        self.pos = positions

    @property
    def key(self) -> str:
        return self.meta["key"]

    def at(self, board: chess.Board) -> tuple[int, dict[str, list]]:
        e = self.pos.get(board.epd())
        return (e[0], e[1]) if e else (0, {})

    def dist(self, board: chess.Board) -> tuple[int, dict[str, float]]:
        n, moves = self.at(board)
        tot = sum(m[0] for m in moves.values())
        if not tot:
            return n, {}
        return n, {u: m[0] / tot for u, m in moves.items()}

    def info(self, board: chess.Board, bad: set[str] | None = None) -> dict | None:
        """Co przeciwnik robił w tej pozycji (do karty pułapki)."""
        n, moves = self.at(board)
        if not n:
            return None
        out = {"reached": n, "moves": []}
        for u, (cnt, score) in sorted(moves.items(), key=lambda kv: -kv[1][0])[:5]:
            try:
                san = board.san(chess.Move.from_uci(u))
            except ValueError:
                continue
            out["moves"].append({"uci": u, "san": san, "n": cnt, "score": round(score / cnt, 2) if cnt else None,
                                 "bad": bool(bad and u in bad)})
        if bad is not None:
            out["fell"] = sum(m[0] for u, m in moves.items() if u in bad)
        return out


def _add_game(pos: dict, game: chess.pgn.Game, color: chess.Color) -> bool:
    res = game.headers.get("Result", "*")
    score = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}.get(res)
    if score is not None and color == chess.BLACK:
        score = 1 - score
    board = game.board()
    if board.chess960 or game.headers.get("Variant", "Standard").lower() not in ("standard", "", "from position"):
        return False
    for ply, mv in enumerate(game.mainline_moves()):
        if ply >= MAX_PLIES:
            break
        if board.turn == color:
            e = pos.setdefault(board.epd(), [0, {}])
            e[0] += 1
            m = e[1].setdefault(mv.uci(), [0, 0.0])
            m[0] += 1
            m[1] += score if score is not None else 0.5
        board.push(mv)
    return True


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().casefold())


class Loader:
    """Wczytywanie partii w tle (jedno naraz)."""

    def __init__(self):
        self.running = False
        self.progress: float | None = None
        self.message = ""
        self.error: str | None = None
        self.last_key: str | None = None
        self._books: dict[str, Book] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------- dostęp do książek
    def list(self) -> list[dict]:
        out = []
        if DIR.exists():
            for f in sorted(DIR.glob("*.json"), key=lambda f: -f.stat().st_mtime):
                try:
                    with open(f, encoding="utf-8") as fh:
                        head = json.loads(fh.read()).get("meta", {})
                    out.append(head)
                except (OSError, json.JSONDecodeError):
                    continue
        return out

    def get(self, key: str | None) -> Book | None:
        if not valid_key(key):
            return None
        b = self._books.get(key)
        if b is None and _path(key).exists():
            try:
                d = json.loads(_path(key).read_text(encoding="utf-8"))
                b = Book(d["meta"], d["positions"])
                self._books[key] = b
            except (OSError, json.JSONDecodeError, KeyError):
                return None
        return b

    def delete(self, key: str) -> None:
        if not valid_key(key):
            raise ValueError("Zły klucz przeciwnika")
        self._books.pop(key, None)
        p = _path(key)
        if p.exists():
            p.unlink()

    def status(self) -> dict:
        return {"running": self.running, "progress": self.progress, "message": self.message,
                "error": self.error, "last": self.get(self.last_key).meta if self.last_key and self.get(self.last_key) else None}

    # -------------------------------------------------- wczytywanie
    def start(self, source: str, name: str, max_games: int = 2000) -> None:
        if source not in SOURCES:
            raise ValueError("Nieznane źródło")
        name = name.strip()
        if not name:
            raise ValueError("Podaj nick albo nazwisko gracza")
        with self._lock:
            if self.running:
                raise RuntimeError("Trwa wczytywanie innego gracza")
            self.running, self.progress, self.error = True, 0.0, None
            self.message = "start…"
        threading.Thread(target=self._run, args=(source, name, max_games), daemon=True).start()

    def _run(self, source: str, name: str, max_games: int) -> None:
        pos: dict = {}
        counts = {"white": 0, "black": 0}
        try:
            games = {"lichess": self._lichess, "chesscom": self._chesscom, "local": self._local}[source](name, max_games)
            target = _norm(name)
            for g in games:
                w, b = _norm(g.headers.get("White", "")), _norm(g.headers.get("Black", ""))
                color = chess.WHITE if w == target else chess.BLACK if b == target else None
                if color is None:
                    continue
                if _add_game(pos, g, color):
                    counts["white" if color == chess.WHITE else "black"] += 1
            total = counts["white"] + counts["black"]
            if not total:
                raise RuntimeError("Nie znaleziono partii tego gracza")
            key = book_key(source, name)
            meta = {"key": key, "source": source, "source_label": SOURCES[source], "name": name,
                    "games": total, **counts, "positions": len(pos), "date": time.strftime("%Y-%m-%d %H:%M")}
            DIR.mkdir(parents=True, exist_ok=True)
            _path(key).write_text(json.dumps({"meta": meta, "positions": pos}), encoding="utf-8")
            self._books[key] = Book(meta, pos)
            self.last_key = key
            self.message = f"Wczytano {total} partii ({counts['white']} białymi, {counts['black']} czarnymi)"
        except Exception as exc:
            self.error = f"{exc}"
            self.message = ""
        finally:
            self.running = False
            self.progress = None

    @staticmethod
    def _read_pgns(text: str):
        f = io.StringIO(text)
        while True:
            try:
                g = chess.pgn.read_game(f)
            except Exception:
                continue
            if g is None:
                return
            yield g

    def _lichess(self, name: str, max_games: int):
        token = CONFIG.get("lichess_token", "")
        headers = {**HEADERS, "Accept": "application/x-chess-pgn"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://lichess.org/api/games/user/{requests.utils.quote(name)}"
        params = {"max": max_games, "moves": "true", "tags": "true", "clocks": "false", "evals": "false",
                  "opening": "false", "perfType": "bullet,blitz,rapid,classical,correspondence"}
        self.message = "pobieram partie z Lichess…"
        with requests.get(url, params=params, headers=headers, stream=True, timeout=(10, 60)) as r:
            if r.status_code == 404:
                raise RuntimeError("Nie ma takiego gracza na Lichess")
            if r.status_code != 200:
                raise RuntimeError(f"Lichess HTTP {r.status_code}")
            buf, n = [], 0
            for line in r.iter_lines(decode_unicode=True):
                line = line or ""
                if line.startswith("[Event ") and buf:
                    n += 1
                    if n % 50 == 0:
                        self.message = f"pobrano {n} partii z Lichess…"
                        self.progress = min(0.99, n / max_games)
                    yield from self._read_pgns("\n".join(buf))
                    buf = []
                buf.append(line)
            if buf:
                yield from self._read_pgns("\n".join(buf))

    def _chesscom(self, name: str, max_games: int):
        base = f"https://api.chess.com/pub/player/{requests.utils.quote(name.lower())}/games/archives"
        r = requests.get(base, headers=HEADERS, timeout=(10, 30))
        if r.status_code == 404:
            raise RuntimeError("Nie ma takiego gracza na chess.com")
        if r.status_code != 200:
            raise RuntimeError(f"chess.com HTTP {r.status_code}")
        archives = r.json().get("archives", [])[::-1]  # od najnowszych
        n = 0
        for i, url in enumerate(archives):
            if n >= max_games:
                break
            self.message = f"pobieram partie z chess.com ({i + 1}/{len(archives)} mies.)…"
            self.progress = min(0.99, n / max_games)
            rr = requests.get(url, headers=HEADERS, timeout=(10, 60))
            if rr.status_code != 200:
                continue
            for g in rr.json().get("games", []):
                if g.get("rules") != "chess" or not g.get("pgn"):
                    continue
                for game in self._read_pgns(g["pgn"]):
                    n += 1
                    yield game
                if n >= max_games:
                    break
            time.sleep(0.2)

    def _local(self, name: str, max_games: int):
        from . import databases
        pgn = databases.active_pgn()
        if not pgn or not Path(pgn).exists():
            raise RuntimeError("Nie wybrano lokalnej bazy PGN")
        size = Path(pgn).stat().st_size
        target = _norm(name)
        tag = re.compile(r'^\[(White|Black) "(.*)"\]')
        done = 0
        n = 0
        buf: list[str] = []
        match = False
        lines = 0
        self.message = "przeszukuję bazę…"
        with open(pgn, "rb") as f:
            for raw in f:
                done += len(raw)
                lines += 1
                if lines % 500_000 == 0:
                    self.progress = done / size
                    self.message = f"przeszukuję bazę… {done * 100 // size}% · znaleziono {n} partii"
                if raw.startswith(b"[Event "):
                    if match and buf:
                        n += 1
                        yield from self._read_pgns("".join(buf))
                        if n >= max_games:
                            return
                    buf, match = [], False
                line = raw.decode("utf-8", "replace")
                if not match and line.startswith(("[White ", "[Black ")):
                    m = tag.match(line)
                    if m and _norm(m.group(2)) == target:
                        match = True
                buf.append(line)
            if match and buf:
                yield from self._read_pgns("".join(buf))


_shared: Loader | None = None


def shared() -> Loader:
    global _shared
    if _shared is None:
        _shared = Loader()
    return _shared
