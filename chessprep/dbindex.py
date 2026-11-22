"""Indeks pozycji z dużej bazy PGN (np. TeraBaza.pgn, ~17 mln partii).

Dla każdej partii zapisujemy pierwsze ``max_plies`` półruchów jako rekordy
(pozycja, ruch, przedział rankingu, wynik). Pozycja = klucz Zobrist (Polyglot),
więc transpozycje liczą się razem.

Budowa (jednorazowo, można przerwać i wznowić):
    python -m chessprep.dbindex build [--workers 3]

Faza 1: plik PGN dzielimy na kawałki ~64 MB; każdy kawałek -> posortowany
         pliki .npy + .json w data/index/tmp (gotowe kawałki są pomijane po wznowieniu).
Faza 2: scalanie po 256 "szardach" (najstarszy bajt klucza) do trzech płaskich
         plików (keys.u64, info.u32, count.u32) posortowanych po kluczu.
         Zapytanie to wyszukiwanie binarne w pliku zmapowanym w pamięci.

Kodowanie pola ``info`` (uint32):
    bity  0-14  ruch: from | to<<6 | promocja<<12   (0 = partia skończyła się tu)
    bity 15-20  przedział rankingu: średni ranking // 100 w zakresie 8..28 (0 = brak)
    bity 21-22  wynik: 0 = 1-0, 1 = remis, 2 = 0-1, 3 = nieznany
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

import chess
import chess.polyglot
import numpy as np

from .config import CONFIG

INDEX_VERSION = 1
CHUNK_BYTES = 64 * 1024 * 1024
END_MOVE = 0  # specjalny "ruch": partia zakończyła się w tej pozycji / limit półruchów

BUCKET_MIN, BUCKET_MAX = 8, 28  # 800 .. 2800+

_RE_HEADER = re.compile(rb'^\[(\w+)\s+"([^"]*)"\]', re.M)
_RE_COMMENT = re.compile(r"\{[^}]*\}|;[^\n]*")
_RE_VARIATION = re.compile(r"\([^()]*\)")
_RE_SAN = re.compile(
    r"(O-O-O|O-O|0-0-0|0-0|[NBRQK][a-h]?[1-8]?x?[a-h][1-8]|[a-h](?:x[a-h])?[1-8](?:=?[NBRQ])?)"
)
_RESULTS = {b"1-0": 0, b"1/2-1/2": 1, b"0-1": 2}


# ---------------------------------------------------------------- kodowanie

def encode_move(move: chess.Move) -> int:
    return move.from_square | (move.to_square << 6) | ((move.promotion or 0) << 12)


def decode_move(code: int) -> chess.Move | None:
    if code == END_MOVE:
        return None
    promo = (code >> 12) & 7
    return chess.Move(code & 63, (code >> 6) & 63, promo or None)


def elo_bucket(white: int | None, black: int | None) -> int:
    elos = [e for e in (white, black) if e]
    if not elos:
        return 0
    avg = sum(elos) // len(elos)
    return max(BUCKET_MIN, min(BUCKET_MAX, avg // 100))


_MIX = 0x9E3779B97F4A7C15
_MASK64 = (1 << 64) - 1


def position_key(board: chess.Board) -> int:
    """64-bitowy klucz pozycji (szybszy niż Zobrist z python-chess).

    Opiera się na deterministycznym haszu krotki intów w CPythonie (stabilny od 3.8);
    wersja metody zapisana jest w meta.json indeksu (``key_method``).
    Pole bicia w przelocie liczy się tylko, gdy bicie jest faktycznie możliwe,
    żeby transpozycje się zgadzały.
    """
    ep = board.ep_square
    if ep is not None and not board.has_legal_en_passant():
        ep = None
    h = hash((board.occupied_co[0], board.occupied_co[1], board.pawns, board.knights,
              board.bishops, board.rooks, board.queens, board.kings, board.turn,
              board.castling_rights, ep))
    return ((h & _MASK64) * _MIX) & _MASK64


KEY_METHOD = "tuplehash-v1"


def _fast_move(board: chess.Board, san: str) -> chess.Move:
    """Szybkie rozwiązanie SAN bez generowania wszystkich ruchów (fallback: parse_san)."""
    turn = board.turn
    if san[0] == "O":
        return board.parse_san(san)
    c0 = san[0]
    if "a" <= c0 <= "h":  # ruch pionem
        promo = None
        if "=" in san:
            san, p = san.split("=")
            promo = chess.PIECE_SYMBOLS.index(p.lower())
        elif san[-1] in "NBRQ":
            promo = chess.PIECE_SYMBOLS.index(san[-1].lower())
            san = san[:-1]
        to = chess.parse_square(san[-2:])
        step = 8 if turn == chess.WHITE else -8
        if "x" in san:
            frm = chess.square(ord(c0) - 97, chess.square_rank(to - step))
        else:
            frm = to - step
            if not (board.pawns & board.occupied_co[turn] & chess.BB_SQUARES[frm]):
                frm = to - 2 * step
        move = chess.Move(frm, to, promo)
    else:
        pt = chess.PIECE_SYMBOLS.index(c0.lower())
        to = chess.parse_square(san[-2:])
        cands = board.attackers_mask(turn, to) & board.pieces_mask(pt, turn)
        dis = san[1:-2].replace("x", "")
        for ch in dis:
            if "a" <= ch <= "h":
                cands &= chess.BB_FILES[ord(ch) - 97]
            else:
                cands &= chess.BB_RANKS[int(ch) - 1]
        if cands == 0:
            raise ValueError(san)
        if cands & (cands - 1):  # wiele kandydatów (związanie) - pełna walidacja
            return board.parse_san(san)
        move = chess.Move(chess.lsb(cands), to)
    if not board.is_pseudo_legal(move):
        raise ValueError(san)
    return move


# ---------------------------------------------------------------- faza 1

def _to_int(value: bytes | None) -> int | None:
    if not value:
        return None
    try:
        v = int(value)
    except ValueError:
        return None
    return v if 100 <= v <= 3500 else None


def _parse_game(raw: bytes, max_plies: int, keys: list, infos: list) -> bool:
    split = raw.find(b"\n\n")
    if split < 0:
        return False
    headers = {k: v for k, v in _RE_HEADER.findall(raw[:split])}
    if headers.get(b"SetUp") == b"1" or b"FEN" in headers:
        return False
    variant = headers.get(b"Variant", b"standard").lower()
    if variant not in (b"standard", b"chess", b""):
        return False
    result = _RESULTS.get(headers.get(b"Result", b"*"), 3)
    bucket = elo_bucket(_to_int(headers.get(b"WhiteElo")), _to_int(headers.get(b"BlackElo")))
    tag = (bucket << 15) | (result << 21)

    text = raw[split + 2:].decode("latin-1")
    if "{" in text or ";" in text:
        text = _RE_COMMENT.sub(" ", text)
    while "(" in text:
        new = _RE_VARIATION.sub(" ", text)
        if new == text:
            break
        text = new

    board = chess.Board()
    ply = 0
    for san in _RE_SAN.findall(text):
        if ply >= max_plies:
            break
        try:
            move = _fast_move(board, san.replace("0", "O"))
        except ValueError:
            break  # uszkodzony zapis - bierzemy to, co już mamy
        keys.append(position_key(board))
        infos.append(encode_move(move) | tag)
        board.push(move)
        ply += 1
    if ply == 0:
        return False
    keys.append(position_key(board))
    infos.append(END_MOVE | tag)
    return True


def _iter_games(path: str, start: int, end: int):
    """Zwraca surowe partie, których nagłówek [Event zaczyna się w [start, end)."""
    with open(path, "rb") as f:
        f.seek(max(0, start - 1))
        buf = f.read(end - start + 1)
        # dopełnij do końca ostatniej partii
        while True:
            more = f.read(1 << 20)
            if not more:
                break
            buf += more
            if buf.find(b"\n[Event ", end - start + 1) >= 0:
                break
    offset = max(0, start - 1)
    if start == 0 and buf.startswith(b"[Event "):
        pos = 0
    else:
        p = buf.find(b"\n[Event ")
        pos = p + 1 if p >= 0 else -1
    while 0 <= pos and offset + pos < end:
        nxt = buf.find(b"\n[Event ", pos + 1)
        game = buf[pos:nxt if nxt >= 0 else len(buf)]
        yield game.replace(b"\r\n", b"\n")
        if nxt < 0:
            break
        pos = nxt + 1


def _chunk_paths(tmp_dir, idx: int) -> dict[str, Path]:
    base = Path(tmp_dir) / f"chunk_{idx:05d}"
    return {"keys": base.with_suffix(".keys.npy"), "info": base.with_suffix(".info.npy"),
            "meta": base.with_suffix(".json"), "legacy": base.with_suffix(".npz")}


def _save_chunk(paths: dict[str, Path], k, i, bounds, games: int) -> None:
    np.save(paths["keys"], k)
    np.save(paths["info"], i)
    tmp = paths["meta"].with_suffix(".json.part")
    tmp.write_text(json.dumps({"bounds": [int(b) for b in bounds], "games": int(games)}))
    os.replace(tmp, paths["meta"])  # plik .json = znacznik ukończenia kawałka


def _convert_legacy(paths: dict[str, Path]) -> None:
    """Stary format (.npz) -> pliki .npy, które da się mapować w pamięci."""
    if paths["meta"].exists() or not paths["legacy"].exists():
        return
    with np.load(paths["legacy"]) as f:
        _save_chunk(paths, f["keys"], f["info"], f["bounds"], int(f["games"][0]))
    paths["legacy"].unlink()


def _process_chunk(args):
    idx, path, start, end, max_plies, tmp_dir = args
    paths = _chunk_paths(tmp_dir, idx)
    _convert_legacy(paths)
    if paths["meta"].exists():
        return idx, -1, 0.0
    t0 = time.time()
    keys: list[int] = []
    infos: list[int] = []
    games = 0
    for raw in _iter_games(path, start, end):
        if _parse_game(raw, max_plies, keys, infos):
            games += 1
    k = np.array(keys, dtype=np.uint64)
    i = np.array(infos, dtype=np.uint32)
    del keys, infos
    order = np.argsort(k, kind="stable")
    k, i = k[order], i[order]
    shard = (k >> np.uint64(56)).astype(np.int32)
    bounds = np.searchsorted(shard, np.arange(257), side="left").astype(np.int64)
    _save_chunk(paths, k, i, bounds, games)
    return idx, games, time.time() - t0


def _chunks(path: str) -> list[tuple[int, int]]:
    size = os.path.getsize(path)
    return [(s, min(size, s + CHUNK_BYTES)) for s in range(0, size, CHUNK_BYTES)]


# ---------------------------------------------------------------- faza 2

def _merge(index_dir: Path, tmp_dir: Path, n_chunks: int) -> dict:
    # Pliki .npy mapujemy w pamięci - w RAM jest naraz tylko jeden szard (~1/256 danych).
    chunk_paths = [_chunk_paths(tmp_dir, i) for i in range(n_chunks)]
    metas = [json.loads(p["meta"].read_text()) for p in chunk_paths]
    total_games = sum(m["games"] for m in metas)
    bounds = [m["bounds"] for m in metas]
    keys_all = [np.load(p["keys"], mmap_mode="r") for p in chunk_paths]
    info_all = [np.load(p["info"], mmap_mode="r") for p in chunk_paths]

    paths = {n: index_dir / f"{n}.part" for n in ("keys.u64", "info.u32", "count.u32")}
    outs = {n: open(p, "wb") for n, p in paths.items()}
    entries = 0
    t0 = time.time()
    try:
        for s in range(256):
            ks = [keys_all[c][bounds[c][s]:bounds[c][s + 1]] for c in range(n_chunks)]
            ins = [info_all[c][bounds[c][s]:bounds[c][s + 1]] for c in range(n_chunks)]
            k = np.concatenate(ks)
            inf = np.concatenate(ins)
            if len(k) == 0:
                continue
            order = np.lexsort((inf, k))
            k, inf = k[order], inf[order]
            new = np.empty(len(k), dtype=bool)
            new[0] = True
            new[1:] = (k[1:] != k[:-1]) | (inf[1:] != inf[:-1])
            starts = np.flatnonzero(new)
            counts = np.diff(np.append(starts, len(k))).astype(np.uint32)
            outs["keys.u64"].write(k[starts].tobytes())
            outs["info.u32"].write(inf[starts].tobytes())
            outs["count.u32"].write(counts.tobytes())
            entries += len(starts)
            if s % 16 == 15:
                print(f"  scalanie {s + 1}/256, wpisów: {entries:,} ({time.time() - t0:.0f}s)", flush=True)
    finally:
        for f in outs.values():
            f.close()
        del keys_all, info_all
    for n, p in paths.items():
        os.replace(p, index_dir / n)
    return {"games": total_games, "entries": entries}


def build(workers: int = 3, max_plies: int | None = None, keep_tmp: bool = False,
          pgn: str | None = None, index_dir: str | None = None) -> None:
    pgn = pgn or CONFIG["pgn_path"]
    max_plies = max_plies or CONFIG["index_max_plies"]
    if index_dir is None:
        from .databases import index_dir_for
        index_dir = index_dir_for(pgn)
    index_dir = Path(index_dir)
    tmp_dir = index_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # wznawiamy tylko pracę nad tym samym plikiem (ta sama ścieżka i rozmiar)
    source = {"pgn": os.path.abspath(pgn), "size": os.path.getsize(pgn), "max_plies": max_plies}
    src_file = tmp_dir / "source.json"
    if src_file.exists():
        try:
            old = json.loads(src_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
        if old != source:
            for f in tmp_dir.glob("chunk_*"):
                f.unlink()
    src_file.write_text(json.dumps(source), encoding="utf-8")
    (index_dir / "meta.json").unlink(missing_ok=True)  # do końca budowy indeks jest nieważny
    print(f"Indeks: {pgn} -> {index_dir}", flush=True)

    chunks = _chunks(pgn)
    tasks = [(i, pgn, s, e, max_plies, str(tmp_dir)) for i, (s, e) in enumerate(chunks)]
    for i in range(len(chunks)):
        _convert_legacy(_chunk_paths(tmp_dir, i))
    done = sum(1 for i in range(len(chunks)) if _chunk_paths(tmp_dir, i)["meta"].exists())
    print(f"Faza 1: {len(chunks)} kawałków ({done} już gotowych), {workers} procesy", flush=True)
    t0 = time.time()
    games = 0
    finished = done
    with Pool(workers) as pool:
        for idx, g, dt in pool.imap_unordered(_process_chunk, tasks):
            if g < 0:
                continue
            finished += 1
            games += g
            elapsed = time.time() - t0
            new_done = finished - done
            eta = elapsed / new_done * (len(chunks) - finished) if new_done else 0
            print(f"  kawałek {idx:4d}: {g:,} partii w {dt:.0f}s | {finished}/{len(chunks)} "
                  f"| ETA {eta / 60:.0f} min", flush=True)

    print("Faza 2: scalanie", flush=True)
    stats = _merge(index_dir, tmp_dir, len(chunks))
    meta = {
        "version": INDEX_VERSION,
        "max_plies": max_plies,
        "key_method": KEY_METHOD,
        "pgn": os.path.basename(pgn),
        "pgn_path": os.path.abspath(pgn),
        "pgn_size": os.path.getsize(pgn),
        "built": time.strftime("%Y-%m-%d %H:%M"),
        **stats,
    }
    (index_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Gotowe: {stats['games']:,} partii, {stats['entries']:,} wpisów", flush=True)
    if not keep_tmp:
        for f in tmp_dir.iterdir():
            f.unlink()
        tmp_dir.rmdir()


# ---------------------------------------------------------------- zapytania

@dataclass
class MoveStat:
    uci: str
    games: int
    white: int
    draws: int
    black: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class GameIndex:
    """Odczyt gotowego indeksu (pliki zmapowane w pamięci)."""

    def __init__(self, index_dir: str | None = None):
        if index_dir is None:
            from .databases import active_index_dir
            index_dir = active_index_dir()
        self.dir = Path(index_dir) if index_dir else Path("__brak_indeksu__")
        meta = self.dir / "meta.json"
        self.available = meta.exists()
        if not self.available:
            return
        self.meta = json.loads(meta.read_text(encoding="utf-8"))
        self.keys = np.memmap(self.dir / "keys.u64", dtype=np.uint64, mode="r")
        self.info = np.memmap(self.dir / "info.u32", dtype=np.uint32, mode="r")
        self.count = np.memmap(self.dir / "count.u32", dtype=np.uint32, mode="r")

    @property
    def max_plies(self) -> int:
        return self.meta["max_plies"] if self.available else 0

    def query(self, board: chess.Board, elo_min: int | None = None, elo_max: int | None = None,
              include_unrated: bool = False) -> dict:
        """Statystyki ruchów z danej pozycji, ograniczone do zakresu rankingu.

        Zwraca {"total": liczba partii z tą pozycją, "moves": [MoveStat...]}.
        """
        if not self.available:
            return {"available": False, "total": 0, "moves": []}
        key = np.uint64(position_key(board))
        lo = int(np.searchsorted(self.keys, key, side="left"))
        hi = int(np.searchsorted(self.keys, key, side="right"))
        info = np.asarray(self.info[lo:hi])
        cnt = np.asarray(self.count[lo:hi]).astype(np.int64)
        bucket = (info >> 15) & 63
        mask = np.ones(len(info), dtype=bool)
        b_lo = BUCKET_MIN if elo_min is None else max(BUCKET_MIN, elo_min // 100)
        b_hi = BUCKET_MAX if elo_max is None else min(BUCKET_MAX, (elo_max - 1) // 100)
        rated = (bucket >= b_lo) & (bucket <= b_hi)
        mask = rated | (include_unrated & (bucket == 0))
        if elo_min is None and elo_max is None:
            mask[:] = True
        info, cnt = info[mask], cnt[mask]
        moves_code = info & 0x7FFF
        result = (info >> 21) & 3
        stats: dict[int, list[int]] = {}
        for code, res, c in zip(moves_code.tolist(), result.tolist(), cnt.tolist()):
            s = stats.setdefault(code, [0, 0, 0, 0])
            s[0] += c
            if res < 3:
                s[1 + res] += c
        total = sum(s[0] for s in stats.values())
        moves = []
        for code, s in stats.items():
            mv = decode_move(code)
            if mv is None:
                continue
            moves.append(MoveStat(mv.uci(), s[0], s[1], s[2], s[3]))
        moves.sort(key=lambda m: -m.games)
        return {"available": True, "total": total, "moves": moves,
                "beyond_index": board.ply() >= self.max_plies}


# ---------------------------------------------------------------- CLI

def _bench(n: int = 3000) -> None:
    pgn = CONFIG["pgn_path"]
    keys: list = []
    infos: list = []
    t0 = time.time()
    games = 0
    for raw in _iter_games(pgn, 0, 8 * 1024 * 1024):
        if _parse_game(raw, CONFIG["index_max_plies"], keys, infos):
            games += 1
        if games >= n:
            break
    dt = time.time() - t0
    print(f"{games} partii w {dt:.1f}s -> {games / dt:.0f} partii/s na proces, {len(keys)} rekordów")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Indeks bazy partii PGN")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--workers", type=int, default=3)
    b.add_argument("--max-plies", type=int, default=None)
    b.add_argument("--keep-tmp", action="store_true")
    b.add_argument("--pgn", help="plik PGN (domyślnie z konfiguracji)")
    b.add_argument("--index-dir", help="katalog indeksu (domyślnie wyznaczany z nazwy pliku)")
    sub.add_parser("bench")
    q = sub.add_parser("query")
    q.add_argument("moves", nargs="*", help="ruchy w notacji SAN lub UCI od pozycji startowej")
    q.add_argument("--elo-min", type=int)
    q.add_argument("--elo-max", type=int)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.workers, args.max_plies, args.keep_tmp, args.pgn, args.index_dir)
    elif args.cmd == "bench":
        _bench()
    elif args.cmd == "query":
        board = chess.Board()
        for m in args.moves:
            board.push_san(m)
        r = GameIndex().query(board, args.elo_min, args.elo_max)
        print(f"Partii: {r['total']:,}")
        for m in r["moves"][:15]:
            print(f"  {board.san(chess.Move.from_uci(m.uci)):7s} {m.games:9,}  "
                  f"+{m.white} ={m.draws} -{m.black}")


if __name__ == "__main__":
    sys.exit(main())
