"""Testy regresji: dojście przy transpozycjach i kalibracja (bez silnika i bez Mai).

Uruchomienie: ``.venv\\Scripts\\python tests\\test_reach_calib.py`` (albo pytest).
Plik kalibracji jest podmieniany na tymczasowy - prawdziwe dane nie są ruszane.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chess  # noqa: E402

from chessprep import calibration as C  # noqa: E402
from chessprep import search as S  # noqa: E402


def _tmp_calib() -> None:
    C.FILE = pathlib.Path(tempfile.mkdtemp()) / "calibration.json"


class _Dummy:
    name = "fake"

    def __getattr__(self, k):
        raise AttributeError(k)


class _Stats:
    class local:
        available = False

    class lichess:
        enabled = False
        error = None


def _searcher(fen=chess.STARTING_FEN, color="white"):
    _tmp_calib()
    p = S.SearchParams(fen=fen, color=color, use_lichess=False, use_local_db=False)
    sr = S.Searcher(p, eng=_Dummy(), maia=_Dummy(), stats=_Stats())
    root = S.Node(chess.Board(fen))
    sr.root = root
    sr.nodes[root.id] = root
    sr.tt[(root.board.epd(), 0)] = root
    return sr, root


def _play(sr, node, san, p=1.0):
    mv = node.board.parse_san(san)
    sr._add_child(node, mv, p, 1.0, 1.0, 0)
    return node.children[mv.uci()]


def test_reach_our_orders_not_summed():
    sr, root = _searcher()
    a = _play(sr, root, "Nf3"); c = _play(sr, a, "Nf6", 0.5); e1 = _play(sr, c, "Nc3")
    b = _play(sr, root, "Nc3"); d = _play(sr, b, "Nf6", 0.5); e2 = _play(sr, d, "Nf3")
    assert e1 is e2
    e1.expanded, e1.error = True, 0.2
    assert abs(e1.reach - 0.5) < 1e-9          # priorytet w trakcie szukania: maksimum
    sr._mark_alive(); sr.compute_reach(reparent=True)
    assert abs(e1.reach - 0.5) < 1e-9          # 0.5.0 liczyło tu 1.0
    path = e1.path()
    assert path[0] is root and path[-1] is e1


def test_reach_opponent_orders_summed_and_cut():
    sr, root = _searcher()
    a = _play(sr, root, "e4")
    b = _play(sr, a, "Nf6", 0.3); c = _play(sr, b, "Nc3"); e1 = _play(sr, c, "Nc6", 0.5)
    d = _play(sr, a, "Nc6", 0.2); f = _play(sr, d, "Nc3"); e2 = _play(sr, f, "Nf6", 0.5)
    assert e1 is e2
    e1.novelty = {"x": 1}
    sr._mark_alive(); sr.compute_reach()
    assert abs(e1.reach - 0.25) < 1e-9         # 0.3·0.5 + 0.2·0.5
    sr._cut(c); sr._mark_alive(); sr.compute_reach()
    assert abs(e1.reach - 0.1) < 1e-9          # została tylko druga droga


def test_queue_refresh_uses_exact_reach():
    sr, root = _searcher()
    a = _play(sr, root, "e4")
    b = _play(sr, a, "Nf6", 0.3); c = _play(sr, b, "Nc3"); e1 = _play(sr, c, "Nc6", 0.5)
    d = _play(sr, a, "Nc6", 0.2); f = _play(sr, d, "Nc3"); _play(sr, f, "Nf6", 0.5)
    for n in (root, a, b, c, d, f):
        n.expanded = True
    assert abs(e1.reach - 0.15) < 1e-9         # maksimum po drogach
    sr.p.max_plies = 20
    bumped = sr._refresh_queue()
    assert e1 in bumped and abs(e1.reach - 0.25) < 1e-9


def test_skip_only_lichess():
    x = S.Node(chess.Board())
    y = S.Node(chess.Board(), x, chess.Move.from_uci("e2e4"))
    x.local_games, x.lichess_games = 0, 0
    assert S.Searcher._skip_sources(y) == (False, True)
    assert y.lichess_skipped


def test_fit_ignores_low_bin_for_scale():
    base = C.PRIOR
    bins = C._empty()
    bins[0] = [0.02 * 200, 0.045 * 200, 200]   # tylko teoria (0-10%)
    curve = C.fit(bins, base)
    assert abs(C._interp(curve, 0.6) - C._interp(base, 0.6)) < 0.01
    bins[3] = [0.35 * 40, 0.05 * 40, 40]        # dane z 30-40%: skala ~0,25
    curve = C.fit(bins, base)
    assert C._interp(curve, 0.8) < 0.5 * C._interp(base, 0.8)
    ys = [y for _, y in curve]
    assert ys == sorted(ys)


def test_prior_interpolation():
    assert C.prior_for("maia3-5m", "local+masters", 1700) is C.PRIOR
    strong = C.prior_for("maia3-5m", "local+masters", 2200)
    assert abs(C._interp(strong, 0.4) - 0.13) < 1e-6
    half = C.prior_for("maia2", "players", 2200)
    assert abs(C._interp(half, 0.4) - (0.33 + 0.13) / 2) < 1e-6


def test_migration_dedupe_and_measured():
    _tmp_calib()
    C.FILE.write_text(json.dumps({"maia3-5m|2000-3200": [[0.4 * 5, 0.1 * 5, 5]] + [[0, 0, 0]] * 9,
                                  "maia2|1600-2000": [[0, 0, 0]] * 10}))
    cal = C.Calibration("maia3-5m", 2000, 3300, "local+masters")
    assert cal.kind == "search" and sum(b[2] for b in cal.bins) == 5
    data = json.loads(C.FILE.read_text())
    assert "maia3-5m|local+masters|2000-3200" in data and "maia2|local+players|1600-2000" in data
    # ta sama pozycja liczy się raz - także w kolejnym wyszukiwaniu
    cal.add(0.4, 0.1, "x")
    cal.add(0.4, 0.1, "x")
    assert sum(b[2] for b in cal.new) == 1
    cal.save()
    cal2 = C.Calibration("maia3-5m", 2000, 3300, "local+masters")
    cal2.add(0.4, 0.1, "x")
    assert sum(b[2] for b in cal2.new) == 0
    # bez pomiaru: błąd widoczny od razu dostaje mniejszą wartość niż ukryty
    assert cal2.apply(0.5, "shallow") < cal2.apply(0.5, "deep")
    # pomiar 2000-2600 obejmuje szukanie 2000-3300 (pokrycie zakresów), ale nie gracze Lichess
    bins = {k: C._empty() for k in ("all", "shallow", "deep")}
    for k, act in (("shallow", 0.03), ("deep", 0.10)):
        for kk in ("all", k):
            bins[kk][3] = [bins[kk][3][0] + 0.35 * 150, bins[kk][3][1] + act * 150, bins[kk][3][2] + 150]
    C.save_measured("maia3-5m", 2000, 2600, {**bins, "positions": 300, "errors": 20, "source": "t", "depth": 14})
    cal3 = C.Calibration("maia3-5m", 2000, 3300, "local+masters")
    assert cal3.kind == "measured" and cal3.apply(0.35, "shallow") < cal3.apply(0.35, "deep")
    assert C.Calibration("maia3-5m", 2000, 3300, "players").kind != "measured"


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, type(exc).__name__, exc)
    sys.exit(1 if fails else 0)
