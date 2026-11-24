"""Kalibracja modelu ludzkich ruchów na partiach z lokalnej bazy (zadanie „Kalibruj model”).

Losujemy pozycje z prawdziwych partii z wybranego zakresu rankingu (półruchy 16-60, poza
teorią: < ``calibration.MIN_GAMES`` partii w indeksie). Dla każdej liczymy przewidywany odsetek
błędów tak samo jak w szukaniu (model + silnik) i sprawdzamy, czy gracz naprawdę się pomylił.
To dokładnie warunki, w których leżą pułapki „bez partii”, więc z tych danych powstaje krzywa
kalibracji (osobno dla błędów widocznych płytko i ukrytych) - zob. ``calibration.py``.
"""
from __future__ import annotations

import io
import os
import random
import time
from dataclasses import dataclass

import chess
import chess.pgn

from . import calibration as calib_mod
from . import engine as engine_mod
from . import explorer as explorer_mod
from . import human as human_mod

PLY_MIN, PLY_MAX = 16, 60
READ_BYTES = 48 * 1024


@dataclass
class CalibParams:
    elo_min: int = 2000
    elo_max: int = 2600
    budget: int = 2000          # liczba pozycji
    depth: int = 14             # głębokość silnika do oceny ruchów
    deadline: float = 0.0
    kind: str = "calibration"
    color: str = "kalibracja"   # dla listy kolejki
    fen: str = chess.STARTING_FEN


def _elo(h: chess.pgn.Headers, key: str) -> int | None:
    try:
        return int(h.get(key, ""))
    except ValueError:
        return None


def _random_game(f, size: int, rnd: random.Random) -> chess.pgn.Game | None:
    f.seek(rnd.randrange(0, max(1, size - 1024)))
    raw = f.read(READ_BYTES)
    i = raw.find(b"\n[Event ")
    if i < 0:
        return None
    j = raw.find(b"\n[Event ", i + 8)
    chunk = raw[i + 1: j if j > 0 else len(raw)]
    try:
        return chess.pgn.read_game(io.StringIO(chunk.decode("utf-8", errors="replace")))
    except Exception:  # noqa: BLE001 - uszkodzona partia: losujemy kolejną
        return None


def _sample(f, size: int, rnd: random.Random, p: CalibParams, stats: explorer_mod.Stats):
    """-> (pozycja, historia, zagrany ruch) albo None."""
    game = _random_game(f, size, rnd)
    if game is None:
        return None
    moves = list(game.mainline_moves())
    if len(moves) <= PLY_MIN:
        return None
    ply = rnd.randrange(PLY_MIN, min(PLY_MAX, len(moves) - 1) + 1)
    b = game.board()
    hist = []
    for mv in moves[:ply]:
        hist.append(b.copy(stack=False))
        b.push(mv)
    elo = _elo(game.headers, "WhiteElo" if b.turn == chess.WHITE else "BlackElo")
    if elo is None or not (p.elo_min <= elo <= p.elo_max) or b.is_game_over():
        return None
    if stats.local.available and ply < stats.local.max_plies:
        if stats.local.query(b, p.elo_min, p.elo_max)["total"] >= calib_mod.MIN_GAMES:
            return None  # teoria - tam liczą się partie, nie model
    return b, hist[-7:], moves[ply].uci()


def run(job) -> dict:
    from .search import MISTAKE, QUICK_HIDDEN, SHALLOW_DEPTH, SearchParams, find_hidden_depth, win_chance
    from .databases import active_pgn

    p: CalibParams = job.params
    pgn = active_pgn()
    if not pgn or not os.path.isfile(pgn):
        raise RuntimeError("Brak lokalnej bazy partii (PGN) - wybierz ją w ustawieniach")
    maia = human_mod.shared()
    if not maia.load():
        raise RuntimeError("Nie udało się załadować modelu ludzkich ruchów")
    eng = engine_mod.shared()
    shallow_eng = engine_mod.shallow()
    stats = explorer_mod.shared()
    if not stats.local.available:
        raise RuntimeError("Indeks bazy nie jest gotowy - bez niego nie da się odsiać pozycji teoretycznych")
    sp = SearchParams()  # te same zasady wyboru kandydatów co w szukaniu
    rnd = random.Random()
    size = os.path.getsize(pgn)
    bins = {k: calib_mod._empty() for k in ("all",) + calib_mod.CLASSES}
    n = errors = tries = 0
    t0 = time.time()
    job.total = p.budget

    def add(cls, pred, actual):
        i = min(calib_mod.BINS - 1, int(pred * calib_mod.BINS))
        for k in ("all", cls):
            if k is None:
                continue
            bb = bins[k][i]
            bb[0] += pred
            bb[1] += actual
            bb[2] += 1

    with open(pgn, "rb") as f:
        while n < p.budget and not job.cancel.is_set() and not job.finish.is_set():
            tries += 1
            if tries > p.budget * 200:
                break  # w bazie prawie nie ma partii z tego zakresu rankingu
            smp = _sample(f, size, rnd, p, stats)
            if smp is None:
                continue
            b, hist, played = smp
            mover = b.turn
            probs = maia.probs(b, p.elo_min, p.elo_max, hist)
            if not probs:
                continue
            # kandydaci jak w szukaniu (_expand_opponent): ruchy ludzi do opp_coverage
            # (najwyżej opp_max_replies + 2) + 3 najlepsze ruchy płytkiego przeglądu silnika
            cands, cum = [], 0.0
            for u, pr in probs.items():
                if len(cands) >= sp.opp_max_replies + 2 or (cum >= sp.opp_coverage and cands):
                    break
                cands.append(u)
                cum += pr
            top = eng.analyse(b, depth=max(10, p.depth - 4), multipv=3)
            if not top:
                continue
            cands = list(dict.fromkeys(cands + [l.move for l in top]))
            evald = list(dict.fromkeys(cands + [played]))  # zagrany oceniamy zawsze
            lines = eng.analyse(b, depth=p.depth, multipv=len(evald),
                                moves=[chess.Move.from_uci(u) for u in evald])
            ev_all = {l.move: l.cp_for(mover) for l in lines}
            if played not in ev_all:
                continue
            best = max(ev_all.values())
            loss_all = {u: max(0.0, win_chance(best) - win_chance(cp)) for u, cp in ev_all.items()}
            loss = {u: loss_all[u] for u in cands if u in loss_all}
            ranked = sorted(loss.values())
            others_bad = len(ranked) >= 2 and ranked[1] >= MISTAKE
            bad = [u for u in loss if loss[u] >= MISTAKE]
            good = [u for u in loss if loss[u] < MISTAKE]
            # przewidywany błąd dokładnie jak err_model w szukaniu
            pred = sum(probs.get(u, 0.0) for u in bad)
            if others_bad:
                pred += max(0.0, 1 - sum(probs.get(u, 0.0) for u in loss))
            pred = min(1.0, pred)
            actual = 1.0 if loss_all[played] >= MISTAKE else 0.0
            cls = None
            if pred >= 0.02 and bad and good:
                bm = max(bad, key=lambda u: probs.get(u, 0.0))
                gm = min(good, key=lambda u: loss[u])
                hd = find_hidden_depth(shallow_eng, b, gm, bm, QUICK_HIDDEN)
                cls = "shallow" if hd is not None and hd <= SHALLOW_DEPTH else "deep"
            add(cls, pred, actual)
            n += 1
            errors += int(actual)
            job.progress = n
            el = time.time() - t0
            job.message = (f"Kalibracja: {n}/{p.budget} pozycji, błędów w partiach {errors} "
                           f"({el / max(1, n):.1f} s/poz.)")
    result = {"all": bins["all"], **{c: bins[c] for c in calib_mod.CLASSES},
              "positions": n, "errors": errors, "depth": p.depth,
              "source": os.path.basename(pgn), "model": getattr(maia, "name", "maia2")}
    if n:
        calib_mod.save_measured(result["model"], p.elo_min, p.elo_max, result)
    result["summary"] = {k: [{"pred": round(sp / c, 3), "actual": round(se / c, 3), "n": c}
                             for sp, se, c in bins[k] if c] for k in bins}
    result["seconds"] = round(time.time() - t0)
    result["saved"] = n
    if tries > p.budget * 200 and n < p.budget:
        result["note"] = "W bazie jest bardzo mało partii graczy z tego zakresu rankingu"
    return result
