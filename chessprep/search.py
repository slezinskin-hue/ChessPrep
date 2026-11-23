"""Szukanie linii nowych i grywalnych oraz takich, w których przeciwnik ma trudne ruchy.

Idea
----
Budujemy drzewo od zadanej pozycji:

* **nasz ruch** - ruchy silnika (MultiPV) mieszczące się w progu oceny (``min_eval``)
  i nie gorsze od najlepszego o więcej niż ``max_drop``. W pozycjach "teoretycznych"
  (dużo partii) bierzemy więcej linii silnika, żeby wyłapać rzadkie, ale dobre ruchy
  (nowinki: strata ≤ ``novelty_max_loss`` względem najlepszego; rzadkie = ≤ ``novelty_max_games``
  partii albo ≤ ``novelty_max_share`` udziału w partiach z tej pozycji);
* **ruch przeciwnika** - ruchy, które ludzie z zakresu rankingu faktycznie grają
  (Maia zmieszana ze statystykami partii, a gdy wczytano przeciwnika - z jego własnymi
  ruchami) + najlepsze ruchy silnika. Wszystkie oceniamy jednym przeszukaniem MultiPV,
  więc oceny są porównywalne.

Miary w pozycji przeciwnika (szansa wygranej = krzywa Lichess z centypionów):

* ``loss(m)``  - ile szansy wygranej przeciwnik oddaje ruchem m względem najlepszego;
  ruch z ``loss ≥ MISTAKE`` (10%) to błąd,
* ``error``    - jaki odsetek ludzi zagra tu błąd; w pozycjach bez partii przewidywanie
  modelu jest kalibrowane (``calibration.py``), bo Maia przeszacowuje wysokie wartości,
* ``trick``    - oczekiwana strata przeciwnika: Σ p(m)·loss(m) po błędach,
* ``ev``       - oczekiwany wynik (szansa wygranej) przy ludzkiej grze przeciwnika i naszej
  najlepszej grze (expectimax po drzewie); ``gain`` = ev - wynik przy najlepszej obronie,
* **trudny moment** - ``error ≥ hard_error`` i dobry ruch nie jest oczywisty
  (nie jest odbiciem, wymuszoną ucieczką z szacha, ani ruchem, który model uważa
  za naturalny ≥ ``obvious_p``); **jedyny ruch** - tylko jeden ruch nie jest błędem,
* **głębokość ukrycia** - najmniejsza głębokość, na której silnik widzi, że zły ruch jest
  błędem (Biswas-Regan; "ukryta" = głębiej niż 6).

Kolejność rozwijania: best-first po średniej geometrycznej szansy dotarcia warunkowej
(przeciwnik gra dobrze) i bezwarunkowej, z limitem udziału jednej rodziny wariantów
w budżecie. Transpozycje (ta sama pozycja po tej samej liczbie półruchów) są jednym węzłem.
Głębokość silnika może zależeć od szansy dotarcia (``depth_min`` .. ``depth``).

Wyniki: pułapki, trudne linie, nowinki. Najlepsze z każdej grupy są weryfikowane na
większej głębokości: nasze ruchy na ścieżce, a w pułapkach także to, czy błąd przeciwnika
nadal jest błędem.
"""
from __future__ import annotations

import heapq
import itertools
import json
import math
import threading
import time
import traceback
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass, field

import chess

from . import calibration as calib_mod
from . import engine as engine_mod
from . import explorer as explorer_mod
from . import human as human_mod
from . import opponent as opponent_mod
from .config import DATA_DIR

WIN_K = 0.00368208          # krzywa Lichess: cp -> szansa wygranej
MISTAKE = 0.10              # spadek szansy wygranej od tej wartości = błąd (jak "mistake" na Lichess)
MIN_ERROR_CHANCE = 0.08     # pułapka = co najmniej tyle ludzi popełnia tu błąd
WINNING_CP = 300            # od tej oceny uznajemy pozycję za wygraną - nie rozwijamy dalej
PRIOR_GAMES = 25            # ile partii "jest warta" Maia przy mieszaniu ze statystykami
SHALLOW_DEPTH = 6           # "pierwsze wrażenie" silnika (ukryte pułapki)
HIDDEN_DEPTHS = (2, 4, 6, 8, 10, 12, 14, 16)
CALIB_MAX_GAMES = 30        # poniżej tylu partii przewidywany błąd kalibrujemy
OPP_PRIOR = 3               # waga statystyk przeciwnika: n / (n + OPP_PRIOR)
CHECKPOINT_EVERY = 15 * 60  # zapis wyników częściowych (s)
MIN_TIME_LEFT = 5 * 60      # tryb "do godziny": tyle czasu musi zostać, gdy szukanie wychodzi z kolejki


def win_chance(cp: float) -> float:
    cp = max(-2000, min(2000, cp))
    return 1 / (1 + math.exp(-WIN_K * cp))


@dataclass
class SearchParams:
    fen: str = chess.STARTING_FEN
    color: str = "white"            # nasz kolor
    min_eval: int = -50             # minimalna ocena z NASZEJ perspektywy (cp)
    max_drop: int = 120             # maks. strata naszego ruchu względem najlepszego (cp)
    elo_min: int = 1600
    elo_max: int = 2000
    max_plies: int = 12             # głębokość drzewa (półruchy od pozycji startowej)
    budget: int = 120               # ile pozycji przeanalizować (0 = bez limitu, w trybie do godziny)
    my_candidates: int = 4          # MultiPV w naszych węzłach
    opp_coverage: float = 0.85
    opp_max_replies: int = 4
    depth: int = 16                 # głębokość silnika w trakcie szukania (ważne gałęzie)
    depth_min: int = 0              # głębokość w mało prawdopodobnych gałęziach (0 = jak depth)
    verify_depth: int = 21          # głębokość weryfikacji wyników
    use_lichess: bool = True
    lichess_source: str = "players"  # "players" | "masters" | "both"
    use_local_db: bool = True
    speeds: list = field(default_factory=lambda: ["blitz", "rapid", "classical"])
    # nowinki
    novelty_max_loss: int = 50      # nowinka może być gorsza od najlepszego ruchu najwyżej o tyle (cp)
    novelty_max_games: int = 10     # "rzadko grane" = najwyżej tyle partii z tym ruchem ...
    novelty_max_share: float = 0.02  # ... albo najwyżej taki udział w partiach z tej pozycji
    theory_min_games: int = 50      # pozycja jest "teorią", gdy ma co najmniej tyle partii
    novelty_multipv: int = 8        # ile linii silnika sprawdzać w pozycjach teoretycznych
    # trudne momenty
    hard_error: float = 0.25        # trudny moment = co najmniej tyle ludzi się tu myli
    obvious_p: float = 0.5          # dobry ruch z p(Maia) ≥ tyle uznajemy za oczywisty
    # ranking i kolejność
    min_reach: float = 0.002        # pułapki z mniejszą szansą dojścia idą na koniec ("rzadkie")
    family_quota: int = 4           # tyle wyników z jednej rodziny, zanim pokażemy kolejne z niej
    family_share: float = 0.5       # maks. udział jednej rodziny wariantów w budżecie
    # tryb "do godziny"
    deadline: float = 0.0           # znacznik czasu (s) końca szukania; 0 = liczba pozycji
    # historia partii (ruchy od pre_fen do fen) i przeciwnik
    pre_fen: str = ""
    pre_moves: list = field(default_factory=list)
    opponent: str = ""              # klucz książki przeciwnika (opponent.book_key)

    @property
    def side(self) -> chess.Color:
        return chess.WHITE if self.color == "white" else chess.BLACK


class Node:
    __slots__ = ("id", "board", "parent", "move", "san", "reach", "prio", "children",
                 "expanded", "my_cp", "best_cp", "lines", "policy", "move_info", "trick",
                 "value", "local_games", "lichess_games", "terminal", "verified", "prob",
                 "error", "p_good", "only_move", "moment", "hidden", "move_games", "novelty",
                 "ply", "creach", "parents", "edge_p", "ev", "refuted", "reverified", "hidden_depth",
                 "calibrated", "err_raw", "family", "opp_games", "src_games", "maia_p", "games_total",
                 "edge_cp")

    _ids = itertools.count()

    def __init__(self, board: chess.Board, parent: "Node | None" = None, move: chess.Move | None = None,
                 reach: float = 1.0, prob: float = 1.0, prio: float = 1.0, creach: float = 1.0):
        self.id = next(Node._ids)
        self.board = board
        self.parent = parent             # pierwszy rodzic (wyznacza linię)
        self.move = move
        self.san = parent.board.san(move) if parent is not None and move is not None else ""
        self.ply = parent.ply + 1 if parent is not None else 0
        self.reach = reach               # szansa dotarcia przy naturalnej grze przeciwnika
        self.creach = creach             # j.w., ale zakładając, że przeciwnik gra dobre ruchy
        self.prio = prio
        self.prob = prob                 # p(ruch) gdy to był ruch przeciwnika, inaczej 1
        self.parents: list[tuple[Node, str]] = [(parent, move.uci())] if parent is not None else []
        self.children: dict[str, Node] = {}
        self.edge_p: dict[str, float] = {}  # p(ruch) dla każdej krawędzi do dziecka
        self.edge_cp: dict[str, int | None] = {}  # ocena ruchu z przeszukania w TEJ pozycji (koszt)
        self.expanded = False
        self.my_cp: int | None = None    # ocena pozycji z naszej perspektywy
        self.best_cp: int | None = None
        self.lines: list = []
        self.policy: dict[str, float] = {}
        self.maia_p: dict[str, float] = {}
        self.move_info: list[dict] = []  # (węzeł przeciwnika) ruchy z p, oceną i stratą
        self.trick = 0.0
        self.value = 0.0
        self.ev: float | None = None
        self.local_games: int | None = None
        self.lichess_games: int | None = None
        self.games_total = 0
        self.terminal = ""
        self.verified: bool | None = None
        self.refuted = False             # weryfikacja: "błąd" przeciwnika nie jest błędem
        self.reverified = False
        self.hidden_depth: int | None = None
        self.calibrated = False
        self.err_raw: float | None = None
        self.error = 0.0
        self.p_good = 1.0
        self.only_move = False
        self.moment: dict | None = None  # opis trudnego momentu (węzeł przeciwnika)
        self.hidden = False
        self.move_games: dict[str, int] = {}  # ile partii z każdym ruchem
        self.src_games: dict[str, tuple] = {}  # (lokalnie, Lichess) dla każdego ruchu
        self.novelty: dict | None = None      # (węzeł po naszym ruchu) opis nowinki
        self.family = ""
        self.opp_games = 0

    def path(self) -> list["Node"]:
        out, p = [], self
        while p is not None:
            out.append(p)
            p = p.parent
        return out[::-1]

    @property
    def games(self) -> int | None:
        vals = [g for g in (self.local_games, self.lichess_games) if g is not None]
        return sum(vals) if vals else None


class HumanPolicy:
    """Model ludzkich ruchów zmieszany ze statystykami partii z wybranego zakresu rankingu
    (i, jeśli wczytano przeciwnika, z jego własnymi ruchami)."""

    def __init__(self, params: SearchParams, maia, stats: explorer_mod.Stats,
                 book: opponent_mod.Book | None = None):
        self.p = params
        self.maia = maia
        self.stats = stats
        self.book = book

    def counts(self, board: chess.Board):
        """-> (partie per ruch razem, partie lokalnie, partie Lichess, {ruch: (lokalnie, Lichess)})"""
        merged: dict[str, int] = {}
        src: dict[str, list] = {}
        local_total = lichess_total = None
        if self.p.use_local_db and self.stats.local.available:
            r = self.stats.local.query(board, self.p.elo_min, self.p.elo_max)
            if not r.get("beyond_index"):
                local_total = r["total"]
                for m in r["moves"]:
                    merged[m.uci] = merged.get(m.uci, 0) + m.games
                    src.setdefault(m.uci, [0, 0])[0] += m.games
        if self.p.use_lichess and self.stats.lichess.enabled:
            r = self.stats.lichess_query(board, self.p.elo_min, self.p.elo_max, self.p.speeds,
                                         self.p.lichess_source)
            if r["available"]:
                lichess_total = r["total"]
                for m in r["moves"]:
                    merged[m["uci"]] = merged.get(m["uci"], 0) + m["games"]
                    src.setdefault(m["uci"], [0, 0])[1] += m["games"]
        return merged, local_total, lichess_total, {u: tuple(v) for u, v in src.items()}

    def __call__(self, board: chess.Board, history: list[chess.Board] | None = None):
        """-> (rozkład ruchów, rozkład modelu, partie per ruch, partie lokalnie, partie Lichess,
        partie przeciwnika z tą pozycją)"""
        maia = self.maia.probs(board, self.p.elo_min, self.p.elo_max, history)
        counts, lt, lit, _ = self.counts(board)
        n = sum(counts.values())
        legal = {m.uci() for m in board.legal_moves}
        if not maia:  # brak modelu - same statystyki albo rozkład równomierny
            maia = {u: 1 / len(legal) for u in legal} if legal else {}
        if n == 0:
            out = dict(maia)
        else:
            out = {u: (counts.get(u, 0) + PRIOR_GAMES * maia.get(u, 0.0)) / (n + PRIOR_GAMES) for u in legal}
        opp_n = 0
        if self.book is not None and board.turn != self.p.side:
            opp_n, od = self.book.dist(board)
            if od:
                lam = opp_n / (opp_n + OPP_PRIOR)
                out = {u: (1 - lam) * out.get(u, 0.0) + lam * od.get(u, 0.0) for u in legal}
        s = sum(out.values()) or 1.0
        out = {u: v / s for u, v in sorted(out.items(), key=lambda kv: -kv[1])}
        return out, maia, counts, lt, lit, opp_n


class Job:
    def __init__(self, params: SearchParams):
        self.id = uuid.uuid4().hex[:10]
        self.params = params
        self.status = "queued"
        self.message = ""
        self.progress = 0
        self.total = params.budget
        self.started = time.time()
        self.finished: float | None = None
        self.cancel = threading.Event()     # przerwij od razu (bez weryfikacji)
        self.finish = threading.Event()     # zakończ szukanie, ale zweryfikuj wyniki
        self.on_checkpoint = None           # zapis wyników częściowych
        self.result: dict | None = None
        self.error: str | None = None

    def info(self) -> dict:
        elapsed = (self.finished or time.time()) - self.started
        eta = None
        if self.status == "running":
            if self.params.deadline:
                eta = max(0.0, self.params.deadline - time.time())
            elif self.progress and self.total:
                eta = elapsed / self.progress * max(0, self.total - self.progress)
        return {"id": self.id, "status": self.status, "message": self.message,
                "progress": self.progress, "total": self.total, "elapsed": round(elapsed),
                "eta": round(eta) if eta is not None else None, "error": self.error,
                "deadline": self.params.deadline or None, "finishing": self.finish.is_set()}


def _is_recapture(board: chess.Board, move: chess.Move, last: chess.Move | None) -> bool:
    return last is not None and board.is_capture(move) and move.to_square == last.to_square


def _diversify(items: list, keyf, quota: int) -> list:
    """Najpierw najwyżej ``quota`` elementów z każdej grupy, potem reszta w tej samej kolejności."""
    if quota <= 0:
        return list(items)
    first, rest, cnt = [], [], Counter()
    for it in items:
        k = keyf(it)
        if cnt[k] < quota:
            cnt[k] += 1
            first.append(it)
        else:
            rest.append(it)
    return first + rest


class Searcher:
    def __init__(self, params: SearchParams, job: Job | None = None,
                 eng: engine_mod.Engine | None = None, maia=None,
                 stats: explorer_mod.Stats | None = None):
        self.p = params
        self.job = job
        self.eng = eng or engine_mod.shared()
        self.maia = maia or human_mod.shared()
        if hasattr(self.maia, "load"):
            self.maia.load()  # Maia-3, która nie działa, przełącza się tu na Maię-2 (nazwa do kalibracji)
        self.stats = stats or explorer_mod.shared()
        self.book = opponent_mod.shared().get(params.opponent) if params.opponent else None
        self.policy = HumanPolicy(params, self.maia, self.stats, self.book)
        self.side = params.side
        self.analysed = 0
        self.nodes: dict[int, Node] = {}
        self.tt: dict[tuple[str, int], Node] = {}
        self.fam_count: Counter = Counter()
        self.calib = calib_mod.Calibration(getattr(self.maia, "name", "maia2"), params.elo_min, params.elo_max)
        self.t0 = time.time()
        self._last_ckpt = time.time()
        self.stats_info: dict = {}
        self.pre_boards = self._pre_boards()
        self._alive_ids: set[int] | None = None

    # ------------------------------------------------------------ pomocnicze
    def _pre_boards(self) -> list[chess.Board]:
        p = self.p
        if not p.pre_fen:
            return []
        try:
            b = chess.Board(p.pre_fen)
            out = []
            for u in p.pre_moves:
                out.append(b.copy(stack=False))
                b.push(chess.Move.from_uci(u))
            return out[-8:]
        except (ValueError, AssertionError):
            return []

    def _history(self, node: Node) -> list[chess.Board]:
        boards = [x.board for x in node.path()[:-1]]
        return (self.pre_boards + boards)[-8:]

    def _status(self, msg: str) -> None:
        if self.job:
            self.job.message = msg
            self.job.progress = self.analysed if not self.job.total else min(self.analysed, self.job.total)

    def _cancelled(self) -> bool:
        return bool(self.job and self.job.cancel.is_set())

    def _finishing(self) -> bool:
        return bool(self.job and self.job.finish.is_set())

    def _stop_expanding(self) -> bool:
        if self._cancelled() or self._finishing():
            return True
        p = self.p
        if p.deadline:
            total = max(60.0, p.deadline - self.t0)
            reserve = max(600.0, 0.12 * total) if total > 3600 else 0.2 * total  # czas na weryfikację
            if time.time() >= p.deadline - reserve:
                return True
        return p.budget > 0 and self.analysed >= p.budget

    def _verify_time_over(self) -> bool:
        return self._cancelled() or bool(self.p.deadline and time.time() > self.p.deadline + 300)

    def my_cp(self, line: engine_mod.Line) -> int:
        return line.cp_for(self.side)

    def _line_text(self, node: Node) -> str:
        return " ".join(n.san for n in node.path()[1:]) or "(start)"

    def node_depth(self, node: Node) -> int:
        """Głębokość silnika: pełna dla prawdopodobnych gałęzi, mniejsza dla mało prawdopodobnych."""
        p = self.p
        lo = p.depth_min if 0 < p.depth_min < p.depth else p.depth
        if lo >= p.depth:
            return p.depth
        r = max(node.reach, 1e-9)
        if r >= 0.01:
            return p.depth
        if r <= 0.001:
            return lo
        t = math.log10(r / 0.001)  # 0..1
        return int(round(lo + t * (p.depth - lo)))

    @staticmethod
    def _prio(creach: float, reach: float) -> float:
        return math.sqrt(max(creach, 1e-12) * max(reach, 1e-12))

    def _add_child(self, parent: Node, mv: chess.Move, pr: float, cond: float, mult: float,
                   my_cp: int | None, novelty: dict | None = None) -> Node | None:
        """Dziecko węzła; gdy ta sama pozycja już jest w drzewie (transpozycja) - łączymy węzły.
        Zwraca węzeł do wstawienia do kolejki albo None."""
        u = mv.uci()
        nb = parent.board.copy(stack=False)
        nb.push(mv)
        key = (nb.epd(), parent.ply + 1)
        reach = parent.reach * pr
        creach = parent.creach * cond * mult
        parent.edge_p[u] = pr
        parent.edge_cp[u] = my_cp
        ex = self.tt.get(key)
        if ex is not None:
            if ex is parent or ex.parent is None:
                return None
            parent.children[u] = ex
            ex.parents.append((parent, u))
            # nasze alternatywne kolejności ruchów: maksimum; ruchy przeciwnika: suma
            ex.reach = min(1.0, max(ex.reach, reach) if parent.board.turn == self.side else ex.reach + reach)
            ex.creach = max(ex.creach, creach)
            new = self._prio(ex.creach, ex.reach)
            if not ex.expanded and new > ex.prio * 1.01:
                ex.prio = new
                return ex
            return None
        child = Node(nb, parent, mv, reach, prob=pr, creach=creach)
        child.prio = self._prio(creach, reach)
        child.my_cp = my_cp
        child.novelty = novelty
        child.family = parent.family if parent.ply >= 2 else (parent.family + " " + u).strip()
        self.tt[key] = child
        self.nodes[child.id] = child
        parent.children[u] = child
        return child

    # ------------------------------------------------------------ rozwijanie
    def expand(self, node: Node) -> list[Node]:
        node.expanded = True
        b = node.board
        if b.is_game_over():
            node.terminal = b.result()
            return []
        if b.turn == self.side:
            return self._expand_mine(node)
        return self._expand_opponent(node)

    def _expand_mine(self, node: Node) -> list[Node]:
        p = self.p
        counts, lt, lit, src = self.policy.counts(node.board)
        node.move_games, node.local_games, node.lichess_games, node.src_games = counts, lt, lit, src
        total = node.games or 0
        theory = total >= p.theory_min_games
        multipv = max(p.my_candidates, p.novelty_multipv if theory else 0)
        lines = self.eng.analyse(node.board, depth=self.node_depth(node), multipv=multipv)
        self.analysed += 1
        self.fam_count[node.family] += 1
        if not lines:
            return []
        node.lines = lines
        best = self.my_cp(lines[0])
        node.best_cp = node.my_cp = best
        if best < p.min_eval:
            node.terminal = "poniżej progu"
            return []
        if best >= WINNING_CP:
            node.terminal = "wygrana"
            return []
        out = []
        for i, line in enumerate(lines):
            cp = self.my_cp(line)
            if cp < p.min_eval or best - cp > p.max_drop:
                continue
            games = counts.get(line.move, 0)
            share = games / total if total else 0.0
            rare = games <= p.novelty_max_games or (total > 0 and share <= p.novelty_max_share)
            is_novelty = theory and rare and best - cp <= p.novelty_max_loss
            mv = chess.Move.from_uci(line.move)
            # dodatkowe ruchy spoza top N (w pozycjach teoretycznych: boczne warianty i nowinki)
            # dostają niższy priorytet, żeby nie rozpraszać budżetu
            mult = 1.0 if i < p.my_candidates else (0.4 if is_novelty else 0.25)
            nov = None
            if is_novelty:
                loc, lich = src.get(line.move, (0, 0))
                nov = {"games": games, "parent_games": total, "share": round(share, 4),
                       "local": loc, "lichess": lich, "best_cp": best,
                       "best_move": lines[0].move, "loss": best - cp, "tn": games == 0}
            child = self._add_child(node, mv, 1.0, 1.0, mult, cp, nov)
            if child is not None:
                out.append(child)
        return out

    def _expand_opponent(self, node: Node) -> list[Node]:
        p = self.p
        b = node.board
        policy, maia, counts, lt, lit, opp_n = self.policy(b, self._history(node))
        node.policy, node.maia_p, node.move_games = policy, maia, counts
        node.local_games, node.lichess_games, node.opp_games = lt, lit, opp_n
        node.games_total = sum(counts.values())
        depth = self.node_depth(node)
        # płytszy przegląd tylko po to, by znać najlepsze ruchy silnika; dokładne oceny niżej
        lines = self.eng.analyse(b, depth=max(10, depth - 4), multipv=3)
        self.analysed += 1
        self.fam_count[node.family] += 1
        if not lines:
            return []
        node.lines = lines
        evals: dict[str, int] = {l.move: self.my_cp(l) for l in lines}
        pvs: dict[str, list[str]] = {l.move: l.pv for l in lines}
        chosen, cum = [], 0.0
        for u, pr in policy.items():
            if len(chosen) >= p.opp_max_replies + 2 or (cum >= p.opp_coverage and len(chosen) >= 1):
                break
            chosen.append(u)
            cum += pr
        # Wszystkie kandydaty (ludzkie + najlepsze silnika) oceniamy JEDNYM przeszukaniem
        # MultiPV z ograniczeniem do tych ruchów - wtedy oceny są ze sobą porównywalne.
        cands = list(dict.fromkeys(chosen + [l.move for l in lines]))
        if len(cands) > 1:
            full = self.eng.analyse(b, depth=depth, multipv=len(cands),
                                    moves=[chess.Move.from_uci(u) for u in cands])
            if full:
                evals = {l.move: self.my_cp(l) for l in full}
                pvs.update({l.move: l.pv for l in full})
        self._score_opponent(node, evals, pvs)

        # dane do kalibracji modelu: pozycje z dużą liczbą partii
        if node.games_total >= calib_mod.MIN_GAMES and not opp_n:
            bad = [d["uci"] for d in node.move_info if d["loss"] >= MISTAKE]
            if bad:
                self.calib.add(sum(maia.get(u, 0.0) for u in bad),
                               sum(counts.get(u, 0) for u in bad) / node.games_total)

        if node.best_cp >= WINNING_CP:
            node.terminal = "wygrana"
            return []
        out = []
        replies = 0
        for d in node.move_info:
            u = d["uci"]
            if d["loss"] >= MISTAKE and not d["best"]:
                continue  # przeciwnik się pomylił - dalej nie trzeba
            if not d["best"] and replies >= p.opp_max_replies:
                continue
            if d["cp"] < p.min_eval:
                continue  # nie powinno się zdarzyć (minimax), ale na wszelki wypadek
            pr = d["p"]
            cond = pr / node.p_good if d["loss"] < MISTAKE else 0.05
            child = self._add_child(node, chess.Move.from_uci(u), pr, max(cond, 0.05), 1.0, d["cp"])
            if child is not None:
                out.append(child)
            replies += 1
        return out

    def _score_opponent(self, node: Node, evals: dict[str, int], pvs: dict[str, list[str]],
                        shallow: bool = True) -> None:
        """Straty, odsetek błędów, kalibracja i trudny moment w pozycji przeciwnika."""
        b = node.board
        policy = node.policy
        best_u = min(evals, key=evals.get)  # przeciwnik minimalizuje naszą ocenę
        opp_best = evals[best_u]
        node.best_cp = node.my_cp = opp_best
        wc_best = win_chance(opp_best)
        info = []
        for u, cp in sorted(evals.items(), key=lambda kv: -policy.get(kv[0], 0)):
            loss = max(0.0, win_chance(cp) - wc_best)
            d = {"uci": u, "san": b.san(chess.Move.from_uci(u)), "p": round(policy.get(u, 0.0), 4),
                 "maia": round(node.maia_p.get(u, 0.0), 4), "games": node.move_games.get(u, 0),
                 "cp": cp, "loss": round(loss, 4), "best": u == best_u}
            if loss >= MISTAKE:  # jak ukarać błąd: linia silnika po złym ruchu
                d["pv"] = refutation(b, pvs.get(u, []))
            info.append(d)
        # Czy pozostałe (nieocenione) ruchy też są błędami? Tak, jeśli drugi najlepszy ruch
        # silnika (spośród ocenionych są wszystkie z top-3) jest już błędem.
        ranked = sorted(info, key=lambda d: d["loss"])
        others_bad = len(ranked) >= 2 and ranked[1]["loss"] >= MISTAKE
        rest = max(0.0, 1 - sum(d["p"] for d in info))
        error = sum(d["p"] for d in info if d["loss"] >= MISTAKE) + (rest if others_bad else 0.0)
        node.err_raw = round(error, 4)
        node.calibrated = False
        # W pozycjach bez (mało) partii liczy się sam model, który przeszacowuje wysokie odsetki
        # błędów - skalujemy je krzywą kalibracji (zob. calibration.py).
        scale_bad = 1.0
        if node.games_total < CALIB_MAX_GAMES and node.opp_games < 5 and error > 0:
            # Kalibrujemy tylko część pochodzącą z modelu: rozkład to mieszanka partii (waga n)
            # i modelu (waga PRIOR_GAMES), więc poprawka modelu liczy się z wagą modelu.
            w_model = PRIOR_GAMES / (node.games_total + PRIOR_GAMES)
            mp = node.maia_p
            err_model = sum(mp.get(d["uci"], 0.0) for d in info if d["loss"] >= MISTAKE)
            if others_bad:
                err_model += max(0.0, 1 - sum(mp.get(d["uci"], 0.0) for d in info))
            err_model = min(1.0, err_model)
            cal = max(0.0, error - w_model * (err_model - self.calib.apply(err_model)))
            if cal < error - 1e-4:
                scale_bad = cal / error
                scale_good = (1 - cal) / max(1e-9, 1 - error)
                for d in info:
                    d["p"] = round(d["p"] * (scale_bad if d["loss"] >= MISTAKE else scale_good), 4)
                error = cal
                node.calibrated = True
        trick = sum(d["p"] * d["loss"] for d in info if d["loss"] >= MISTAKE)
        if others_bad:  # masa nieocenionych ruchów to też błędy - doliczamy minimalną stratę
            trick += rest * scale_bad * ranked[1]["loss"]
        good = [d for d in info if d["loss"] < MISTAKE]
        bad = [d for d in info if d["loss"] >= MISTAKE]
        node.move_info = info
        node.trick = trick
        node.error = error
        node.p_good = max(1e-6, sum(d["p"] for d in good))
        node.only_move = len(good) == 1 and others_bad
        for d in info:  # p krawędzi do dzieci po kalibracji
            if d["uci"] in node.children:
                node.edge_p[d["uci"]] = d["p"]
        node.moment = None
        self._classify_moment(node, good, bad, shallow)

    def _classify_moment(self, node: Node, good: list[dict], bad: list[dict], shallow: bool = True) -> None:
        """Czy to trudny moment dla przeciwnika i dlaczego."""
        p = self.p
        b = node.board
        if not bad or node.error < MIN_ERROR_CHANCE:
            return
        if shallow and node.hidden_depth is None:
            # "pierwsze wrażenie" silnika: czy płytko też wybrałby błąd?
            shallow = self.eng.analyse(b, depth=SHALLOW_DEPTH, multipv=1)
            bad_set = {d["uci"] for d in bad}
            node.hidden = bool(shallow) and shallow[0].move in bad_set
        if node.error < p.hard_error:
            return
        # przy kilku dobrych ruchach "trudny wybór" dopiero, gdy myli się większość ludzi
        if not node.only_move and node.error < max(0.5, p.hard_error):
            return
        legal = b.legal_moves.count()
        reasons_obvious = []
        if node.only_move:
            g = good[0]
            mv = chess.Move.from_uci(g["uci"])
            if legal == 1:
                reasons_obvious.append("jedyny legalny ruch")
            if b.is_check() and legal <= 3:
                reasons_obvious.append("wymuszona ucieczka z szacha")
            if _is_recapture(b, mv, node.move):
                reasons_obvious.append("odbicie")
            if g["maia"] >= p.obvious_p:
                reasons_obvious.append("naturalny ruch")
        else:
            # kilka dobrych ruchów - trudne tylko, gdy żaden nie jest naturalny
            if max((d["maia"] for d in good), default=0) >= p.obvious_p:
                reasons_obvious.append("naturalny ruch")
        if reasons_obvious:
            return
        bad_sorted = sorted(bad, key=lambda d: -d["p"])
        node.moment = {
            "type": "only" if node.only_move else "hard",
            "error": round(node.error, 4),
            "good": [{"san": d["san"], "uci": d["uci"], "p": d["p"], "cp": d["cp"]} for d in good],
            "bad": [{"san": d["san"], "uci": d["uci"], "p": d["p"], "cp": d["cp"], "pv": d.get("pv")}
                    for d in bad_sorted[:3]],
            "hidden": node.hidden,
            "hidden_depth": node.hidden_depth,
            "calibrated": node.calibrated, "games": node.games_total,
        }

    # ------------------------------------------------------------ wartości
    def compute_reach(self) -> None:
        """Szansa dotarcia jako suma po wszystkich drogach (transpozycje)."""
        order = sorted(self.nodes.values(), key=lambda n: n.ply)
        for n in order:
            if n.parent is None:
                n.reach = 1.0
                continue
            mine = opp = 0.0
            for par, u in n.parents:
                if par.children.get(u) is not n:
                    continue  # krawędź odcięta przez weryfikację
                if par.board.turn == self.side:
                    # różne kolejności NASZYCH ruchów to alternatywy - wybieramy jedną
                    mine = max(mine, par.reach)
                else:
                    # różne ruchy przeciwnika prowadzące do tej samej pozycji - szanse się sumują
                    opp += par.reach * par.edge_p.get(u, 1.0)
            n.reach = min(1.0, max(mine, opp))

    def backup(self) -> None:
        memo: dict[int, float] = {}

        def val(n: Node) -> float:
            if n.id in memo:
                return memo[n.id]
            memo[n.id] = 0.0
            if not n.children:
                v = n.trick
            elif n.board.turn == self.side:
                best_val = -1.0
                wc_best = win_chance(n.best_cp if n.best_cp is not None else 0)
                for c in n.children.values():
                    best_val = max(best_val, val(c) - max(0.0, wc_best - win_chance(c.my_cp or 0)))
                v = max(0.0, best_val)
            else:
                v = n.trick + sum(n.edge_p.get(u, c.prob) * val(c) for u, c in n.children.items())
            n.value = v
            memo[n.id] = v
            return v

        val(self.root)

    def compute_ev(self) -> None:
        """Oczekiwany wynik (szansa wygranej z naszej perspektywy) przy ludzkiej grze
        przeciwnika i naszej najlepszej grze - expectimax po drzewie."""
        memo: dict[int, float] = {}
        mine_res = {"1-0": 1.0 if self.side == chess.WHITE else 0.0,
                    "0-1": 1.0 if self.side == chess.BLACK else 0.0, "1/2-1/2": 0.5}

        def ev(n: Node) -> float:
            if n.id in memo:
                return memo[n.id]
            memo[n.id] = win_chance(n.my_cp or 0)
            if n.terminal in mine_res:
                v = mine_res[n.terminal]
            elif n.board.turn == self.side:
                vals = [ev(c) for c in n.children.values()]
                v = max(vals) if vals else win_chance(n.my_cp if n.my_cp is not None else 0)
            elif not n.move_info:
                v = win_chance(n.my_cp or 0)
            else:
                s = tot = 0.0
                for d in n.move_info:
                    c = n.children.get(d["uci"])
                    s += d["p"] * (ev(c) if c is not None else win_chance(d["cp"]))
                    tot += d["p"]
                rest = max(0.0, 1 - tot)
                if rest:
                    ranked = sorted(n.move_info, key=lambda d: d["loss"])
                    worst_for_them = ranked[1]["cp"] if len(ranked) >= 2 and ranked[1]["loss"] >= MISTAKE \
                        else (n.best_cp or 0)
                    s += rest * win_chance(worst_for_them)
                v = s
            n.ev = v
            memo[n.id] = v
            return v

        ev(self.root)

    def gain(self, n: Node) -> float:
        """Ile oczekiwanego wyniku dają nam ludzkie błędy w tej pozycji (vs najlepsza obrona)."""
        if n.ev is None or n.best_cp is None:
            return 0.0
        return n.ev - win_chance(n.best_cp)

    def cost(self, n: Node) -> int:
        """Ile oddajemy (cp) naszymi ruchami na drodze do pozycji względem najlepszych ruchów silnika."""
        c = 0
        for x in n.path()[1:]:
            par = x.parent
            cp = par.edge_cp.get(x.move.uci())  # ta sama analiza co best_cp - porównywalne
            if par.board.turn == self.side and par.best_cp is not None and cp is not None:
                c += max(0, par.best_cp - cp)
        return c

    # ------------------------------------------------------------ główna pętla
    def run(self) -> dict:
        p = self.p
        root = Node(chess.Board(p.fen))
        self.root = root
        self.nodes[root.id] = root
        self.tt[(root.board.epd(), 0)] = root
        tie = itertools.count()
        heap: list = [(-1.0, 0, next(tie), root)]
        demotes = 0
        while heap and not self._stop_expanding():
            negp, _, _, node = heapq.heappop(heap)
            if node.expanded:
                continue
            # jedna rodzina wariantów nie może zabrać więcej niż family_share budżetu
            if (p.family_share < 1 and self.analysed > 100 and node.family
                    and self.fam_count[node.family] > p.family_share * self.analysed
                    and demotes < len(heap)):
                heapq.heappush(heap, (negp * 0.5, node.ply, next(tie), node))
                demotes += 1
                continue
            demotes = 0
            self._status(f"Analiza: {self._line_text(node)}")
            for child in self.expand(node):
                if child.ply >= p.max_plies:
                    continue
                heapq.heappush(heap, (-child.prio, child.ply, next(tie), child))
            self._maybe_checkpoint()
        res = self.results(verify=not self._cancelled())
        self.calib.save()
        return res

    def _maybe_checkpoint(self) -> None:
        if not self.job or not self.job.on_checkpoint:
            return
        if time.time() - self._last_ckpt < CHECKPOINT_EVERY:
            return
        self._last_ckpt = time.time()
        try:
            res = self.results(verify=False, rarity=False)
            res["partial"] = True
            self.job.on_checkpoint(res)
        except Exception:  # pragma: no cover - zapis częściowy nie może przerwać szukania
            traceback.print_exc()

    def _caps(self) -> tuple[int, int, int]:
        a = self.analysed
        return max(40, min(300, a // 8)), max(30, min(150, a // 16)), max(30, min(150, a // 16))

    def results(self, verify: bool, rarity: bool = True) -> dict:
        self._mark_alive()
        self.compute_reach()
        self.backup()
        self.compute_ev()
        traps, lines, novs = self.collect_traps(), self.collect_hard_lines(), self.collect_novelties()
        ver = {"traps": 0, "lines": 0, "novelties": 0}
        if verify:
            t0 = time.time()
            nt = min(60, max(10, round(0.2 * min(len(traps), self._caps()[0]))))
            nh = min(25, max(8, round(0.2 * min(len(lines), self._caps()[1]))))
            nn = min(25, max(10, round(0.2 * min(len(novs), self._caps()[2]))))
            self._status("Weryfikacja najlepszych wyników…")
            self.verify_paths(traps[:nt] + [h["node"] for h in lines[:nh]])
            for i, t in enumerate(traps[:nt]):
                if self._verify_time_over():
                    break
                self._status(f"Weryfikacja pułapki {i + 1}/{nt}: {self._line_text(t)}")
                self.reverify_opponent(t)
                ver["traps"] += 1
            for h in lines[:nh]:
                if self._verify_time_over():
                    break
                for m in h["moments"]:
                    self.reverify_opponent(m)
                ver["lines"] += 1
            ver["novelties"] = self.verify_novelties(novs[:nn])
            self._mark_alive()
            self.compute_reach()
            self.backup()
            self.compute_ev()
            traps, lines, novs = self.collect_traps(), self.collect_hard_lines(), self.collect_novelties()
            ver["seconds"] = round(time.time() - t0)
        ct, ch, cn = self._caps()
        traps, lines, novs = traps[:ct], lines[:ch], novs[:cn]
        if rarity:  # zapytania do Lichess - nie przy zapisie częściowym
            self._fill_rarity(traps[:60] + [h["node"] for h in lines[:30]])
        n_nodes = len(self.nodes)
        n_trans = sum(max(0, len(n.parents) - 1) for n in self.nodes.values())
        self.stats_info = {"nodes": n_nodes, "transpositions": n_trans, "analysed": self.analysed,
                           "verified": ver, "calibration": self.calib.summary() or None,
                           "calibration_points": [list(p) for p in self.calib.points],
                           "elapsed": round(time.time() - self.t0)}
        return self.export(traps, lines, novs)

    # ------------------------------------------------------------ wyniki: pułapki
    def _mark_alive(self) -> None:
        """Które węzły są nadal w drzewie (weryfikacja odcina krawędzie). Węzeł, do którego
        prowadzi kilka dróg (transpozycje), żyje, gdy żyje którakolwiek z nich; jeśli odcięto
        jego pierwszą drogę, linią węzła staje się inna żywa droga."""
        alive: set[int] = {self.root.id}
        for n in sorted(self.nodes.values(), key=lambda x: x.ply):
            if n.parent is None:
                continue
            ok = [(par, u) for par, u in n.parents if par.id in alive and par.children.get(u) is n]
            if not ok:
                continue
            alive.add(n.id)
            if (n.parent, n.move.uci()) not in ok:
                par, u = ok[0]
                n.parent, n.move = par, chess.Move.from_uci(u)
                n.san = par.board.san(n.move)
                n.prob = par.edge_p.get(u, n.prob)
                n.novelty = None  # opis nowinki dotyczył ruchu z poprzedniej pozycji
        self._alive_ids = alive

    def _alive(self, n: Node) -> bool:
        """Węzeł nadal w drzewie (nie odcięty przez weryfikację)."""
        if self._alive_ids is None:
            self._mark_alive()
        return n.id in self._alive_ids

    def _hidden_factor(self, n: Node) -> float:
        if n.hidden_depth is None:
            return 0.9
        return 0.8 + 0.2 * min(1.0, n.hidden_depth / 12)

    def _family_key(self, n: Node, plies: int = 4) -> tuple:
        return tuple(x.move.uci() for x in n.path()[1:plies + 1])

    def collect_traps(self) -> list[Node]:
        cands = [n for n in self.nodes.values()
                 if n.board.turn != self.side and n.expanded and n.verified is not False
                 and not n.refuted and n.error >= MIN_ERROR_CHANCE and n.trick >= 0.01 and self._alive(n)]
        # oczekiwana strata przeciwnika na partię od pozycji startowej (× stopień ukrycia)
        cands.sort(key=lambda n: -(n.reach * n.trick * self._hidden_factor(n)))
        main = [n for n in cands if n.reach >= self.p.min_reach]
        rare = [n for n in cands if n.reach < self.p.min_reach]
        return _diversify(main, self._family_key, self.p.family_quota) + rare

    # ------------------------------------------------------------ wyniki: trudne linie
    def collect_hard_lines(self) -> list[dict]:
        """Ścieżki, na których przeciwnik ma kilka trudnych momentów.

        Wynik = oczekiwana liczba błędów: Σ dojście_k · błąd_k po momentach na ścieżce
        (premia za jedyne ruchy). Późne momenty liczą się mniej, bo rzadko się do nich dochodzi.
        """
        cands = []
        for n in self.nodes.values():
            if n.moment is None or n.refuted or n.verified is False or not self._alive(n):
                continue
            moments = [x for x in n.path() if x.moment is not None and not x.refuted]
            if not moments:
                continue
            survive = 1.0
            for x in moments:
                survive *= max(0.0, 1 - x.moment["error"])
            exp_err = sum(x.reach * x.moment["error"] * (1.15 if x.moment["type"] == "only" else 1.0)
                          for x in moments)
            score = exp_err * (1 + 0.1 * (len(moments) - 1))
            cands.append({"node": n, "moments": moments, "survive": survive, "score": score,
                          "exp_errors": exp_err, "entry_reach": moments[0].reach})
        # usuń linie będące prefiksem innej linii z większą liczbą momentów
        ids_with_ext = set()
        for c in cands:
            for x in c["moments"][:-1]:
                ids_with_ext.add(x.id)
        out = [c for c in cands if c["node"].id not in ids_with_ext]
        out.sort(key=lambda c: -c["score"])
        dedup, seen = [], set()
        for c in out:
            key = c["node"].board.epd()
            if key not in seen:
                seen.add(key)
                dedup.append(c)
        return _diversify(dedup, lambda c: tuple(x.id for x in c["moments"][:2]), 2)

    # ------------------------------------------------------------ wyniki: nowinki
    def _follow_value(self, n: Node, plies: int = 5) -> float:
        """Oczekiwana strata przeciwnika w kilku ruchach po nowince (szansa liczona od nowinki)."""
        def fv(x: Node, r: float, depth: int) -> float:
            if depth > plies or not x.expanded:
                return 0.0
            if x.board.turn != self.side:
                return r * x.trick + sum(fv(c, r * x.edge_p.get(u, c.prob), depth + 1)
                                         for u, c in x.children.items())
            return max((fv(c, r, depth + 1) for c in x.children.values()), default=0.0)
        return fv(n, 1.0, 0)

    def collect_novelties(self) -> list[Node]:
        out = [n for n in self.nodes.values()
               if n.novelty is not None and n.verified is not False and self._alive(n)]

        def score(n: Node) -> float:
            nv = n.novelty
            theory = min(1.0, math.log10(1 + (nv["parent_games"] or 0)) / 5)  # 100k partii -> 1
            quality = 1 - nv["loss"] / max(1, self.p.novelty_max_loss) * 0.5
            follow = self._follow_value(n)
            out_of_book = n.expanded and (n.games or 0) == 0
            nv["follow"] = round(follow, 4)
            nv["out_of_book"] = bool(out_of_book)
            # N/TN = mało partii; ruch rzadki tylko względnie (mały udział w ogromnej bazie) niżej
            kind = 1.2 if nv["tn"] else 1.0 if nv["games"] <= self.p.novelty_max_games else 0.6
            nv["rare_only"] = nv["games"] > self.p.novelty_max_games
            return theory * quality * (0.15 + 5 * follow) * kind * (1.15 if out_of_book else 1.0)

        for n in out:
            n.novelty["score"] = round(score(n), 4)
        out.sort(key=lambda n: -n.novelty["score"])
        dedup, seen = [], set()
        for n in out:
            key = n.board.epd()
            if key not in seen:
                seen.add(key)
                dedup.append(n)
        return _diversify(dedup, lambda n: self._family_key(n), self.p.family_quota)

    # ------------------------------------------------------------ weryfikacja
    def _verify_node(self, n: Node, cache: dict) -> bool:
        """Pozycja po naszym ruchu - ocena na głębokości weryfikacji musi trzymać próg."""
        if n.id in cache:
            return cache[n.id]
        self._status(f"Weryfikacja: {self._line_text(n)}")
        lines = self.eng.analyse(n.board, depth=p_depth(self.p), multipv=1)
        cp = self.my_cp(lines[0]) if lines else (0 if not n.board.is_checkmate() else 10 ** 5)
        n.my_cp = cp
        ok = cp >= self.p.min_eval
        cache[n.id] = ok
        n.verified = ok
        return ok

    def _cut(self, n: Node) -> None:
        """Usuń pozycję z drzewa: wszystkie krawędzie do niej (każdą kolejnością ruchów)."""
        for par, u in n.parents:
            if par.children.get(u) is n:
                del par.children[u]

    def verify_paths(self, targets: list[Node]) -> None:
        cache: dict[int, bool] = {}
        for t in targets:
            # zła pozycja jest odcinana wszędzie; gdy do celu prowadzi inna droga
            # (transpozycja), sprawdzamy także ją
            for _ in range(5):
                if self._verify_time_over():
                    return
                if not self._alive(t):
                    t.verified = False
                    break
                cut = False
                for n in t.path()[1:]:
                    if n.parent.board.turn != self.side:
                        continue  # sprawdzamy tylko pozycje po NASZYCH ruchach
                    if not self._verify_node(n, cache):
                        self._cut(n)
                        self._mark_alive()
                        cut = True
                        break
                if not cut:
                    if t.verified is not False:
                        t.verified = True
                    break

    def reverify_opponent(self, n: Node) -> None:
        """Czy "błąd" przeciwnika nadal jest błędem na głębokości weryfikacji + głębokość ukrycia."""
        if n.reverified or not n.move_info:
            return
        n.reverified = True
        cands = [d["uci"] for d in n.move_info]
        if len(cands) >= 2:
            lines = self.eng.analyse(n.board, depth=p_depth(self.p), multipv=len(cands),
                                     moves=[chess.Move.from_uci(u) for u in cands])
            if lines:
                evals = {l.move: self.my_cp(l) for l in lines}
                pvs = {l.move: l.pv for l in lines}
                for d in n.move_info:  # ruchy bez oceny zostawiamy z poprzednią
                    evals.setdefault(d["uci"], d["cp"])
                # bez płytkiej analizy - ukrycie liczy dokładniej _hidden_depth niżej
                self._score_opponent(n, evals, pvs, shallow=False)
        if n.error < MIN_ERROR_CHANCE or not any(d["loss"] >= MISTAKE for d in n.move_info):
            n.refuted = True
            n.moment = None
            return
        self._hidden_depth(n)
        if n.moment is not None:
            n.moment["hidden"] = n.hidden
            n.moment["hidden_depth"] = n.hidden_depth

    def _hidden_depth(self, n: Node) -> None:
        """Najmniejsza głębokość, na której silnik widzi, że najczęstszy zły ruch jest błędem."""
        bad = [d for d in n.move_info if d["loss"] >= MISTAKE]
        good = [d for d in n.move_info if d["loss"] < MISTAKE]
        if not bad or not good:
            return
        bm = max(bad, key=lambda d: d["p"])["uci"]
        gm = min(good, key=lambda d: d["loss"])["uci"]
        moves = [chess.Move.from_uci(gm), chess.Move.from_uci(bm)]
        found = None
        for d in HIDDEN_DEPTHS:
            if d >= p_depth(self.p):
                break
            ls = self.eng.analyse(n.board, depth=d, multipv=2, moves=moves)
            ev = {l.move: self.my_cp(l) for l in ls}
            if gm in ev and bm in ev and win_chance(ev[bm]) - win_chance(ev[gm]) >= MISTAKE:
                found = d
                break
        n.hidden_depth = found if found is not None else p_depth(self.p)
        n.hidden = n.hidden_depth > SHALLOW_DEPTH

    def verify_novelties(self, novs: list[Node]) -> int:
        done = 0
        for n in novs:
            if self._verify_time_over():
                break
            self.verify_paths([n])
            done += 1
            if n.verified is False:
                continue
            parent = n.parent
            self._status(f"Weryfikacja nowinki: {self._line_text(n)}")
            best = self.eng.analyse(parent.board, depth=p_depth(self.p), multipv=1)
            if best and n.my_cp is not None:
                best_cp = self.my_cp(best[0])
                loss = max(0, best_cp - n.my_cp)
                n.novelty.update({"best_cp": best_cp, "best_move": best[0].move, "loss": loss})
                if loss > self.p.novelty_max_loss:
                    n.verified = False
                    n.novelty["rejected"] = True
        return done

    def _fill_rarity(self, nodes: list[Node]) -> None:
        seen = set()
        for t in nodes:
            for n in t.path():
                if n.id in seen or n.local_games is not None or n.lichess_games is not None:
                    continue
                seen.add(n.id)
                _, n.local_games, n.lichess_games, _ = self.policy.counts(n.board)

    # ------------------------------------------------------------ eksport
    def _node_json(self, n: Node, parent: Node | None = None, u: str | None = None) -> dict:
        data = {
            "id": n.id, "san": n.san, "uci": n.move.uci() if n.move else "", "fen": n.board.fen(),
            "ply": n.ply, "reach": round(n.reach, 5), "prob": round(n.prob, 4),
            "eval": n.my_cp, "trick": round(n.trick, 4), "value": round(n.value, 4),
            "ev": round(n.ev, 4) if n.ev is not None else None,
            "error": round(n.error, 4), "only_move": n.only_move, "moment": n.moment,
            "hidden": n.hidden, "hidden_depth": n.hidden_depth, "novelty": n.novelty,
            "local_games": n.local_games, "lichess_games": n.lichess_games,
            "terminal": n.terminal, "verified": n.verified, "refuted": n.refuted,
            "calibrated": n.calibrated, "moves_info": n.move_info,
            "mine": n.board.turn == self.side,
        }
        if parent is not None and n.parent is not parent:
            # transpozycja: ten sam węzeł jest już w drzewie inną drogą
            mv = chess.Move.from_uci(u)
            data.update(uci=u, san=parent.board.san(mv), prob=round(parent.edge_p.get(u, 1.0), 4),
                        transposition=True, children=[])
            return data
        data["children"] = [self._node_json(c, n, cu)
                            for cu, c in sorted(n.children.items(), key=lambda kv: -kv[1].value)]
        return data

    @staticmethod
    def _path_json(n: Node) -> dict:
        path = n.path()
        return {"line": [x.san for x in path[1:]], "line_uci": [x.move.uci() for x in path[1:]]}

    def _opp_info(self, n: Node, bad: set[str]) -> dict | None:
        return self.book.info(n.board, bad) if self.book is not None else None

    def export(self, traps: list[Node], hard_lines: list[dict], novelties: list[Node]) -> dict:
        trap_list = []
        for t in traps:
            good = [d for d in t.move_info if d["loss"] < MISTAKE]
            bad = [d for d in t.move_info if d["loss"] >= MISTAKE]
            g = self.gain(t)
            trap_list.append({
                "node": t.id, "fen": t.board.fen(), **self._path_json(t),
                "reach": round(t.reach, 5), "trick": round(t.trick, 4),
                "error_chance": round(t.error, 4), "error_raw": t.err_raw, "calibrated": t.calibrated,
                "games_total": t.games_total,
                "only_move": t.only_move, "hidden": t.hidden, "hidden_depth": t.hidden_depth,
                "hard": t.moment is not None, "rare": t.reach < self.p.min_reach,
                "good": good, "bad": bad, "eval": t.my_cp, "verified": t.verified,
                "reverified": t.reverified, "cost": self.cost(t),
                "ev": round(t.ev, 4) if t.ev is not None else None, "gain": round(g, 4),
                "per100": round(t.reach * g * 100, 3),
                "local_games": t.local_games, "lichess_games": t.lichess_games,
                "opponent": self._opp_info(t, {d["uci"] for d in bad}),
            })
        hard_list = []
        for h in hard_lines:
            n = h["node"]
            moments = []
            for x in h["moments"]:
                moments.append({**x.moment, "ply": x.ply, "fen": x.board.fen(),
                                "reach": round(x.reach, 5), **self._path_json(x)})
            hard_list.append({"node": n.id, "fen": n.board.fen(), **self._path_json(n),
                              "moments": moments, "survive": round(h["survive"], 4),
                              "entry_reach": round(h["entry_reach"], 5),
                              "exp_errors": round(h["exp_errors"], 4),
                              "score": round(h["score"], 5), "verified": n.verified,
                              "cost": self.cost(n),
                              "local_games": n.local_games, "lichess_games": n.lichess_games})
        nov_list = []
        for n in novelties:
            parent = n.parent
            nv = n.novelty
            best_san = ""
            try:
                best_san = parent.board.san(chess.Move.from_uci(nv["best_move"]))
            except (ValueError, KeyError):
                pass
            # co dalej: najczęstsze odpowiedzi przeciwnika i najlepsza pułapka w poddrzewie
            replies = sorted(n.move_info, key=lambda d: -d["p"])[:3] if n.expanded else []
            follow_trap = None
            stack = list(n.children.values())  # sama nowinka ma osobne pole "error"
            seen = set()
            while stack:
                x = stack.pop()
                if x.id in seen:
                    continue
                seen.add(x.id)
                if (x.board.turn != self.side and x.trick >= 0.01 and x.error >= MIN_ERROR_CHANCE
                        and not x.refuted):
                    if follow_trap is None or x.trick * x.reach > follow_trap.trick * follow_trap.reach:
                        follow_trap = x
                stack.extend(x.children.values())
            nov_list.append({
                "node": n.id, "fen": n.board.fen(), "parent_fen": parent.board.fen(),
                **self._path_json(n), "san": n.san, "eval": n.my_cp,
                "best_san": best_san, "best_cp": nv["best_cp"], "loss": nv["loss"],
                "games": nv["games"], "parent_games": nv["parent_games"], "tn": nv["tn"],
                "share": nv.get("share"), "local": nv.get("local"), "lichess": nv.get("lichess"),
                "rare_only": nv.get("rare_only", False),
                "follow": nv.get("follow"), "out_of_book": nv.get("out_of_book"),
                "score": nv.get("score"), "verified": n.verified, "value": round(n.value, 4),
                "error": round(n.error, 4), "trick": round(n.trick, 4),
                "reach": round(n.reach, 5),
                "replies": replies,
                "follow_trap": ({**self._path_json(follow_trap), "trick": round(follow_trap.trick, 4),
                                 "error": round(follow_trap.error, 4)} if follow_trap else None),
            })
        opp_meta = self.book.meta if self.book is not None else None
        return {"params": asdict(self.p), "analysed": self.analysed,
                "engine": self.eng.name, "maia": getattr(self.maia, "error", None) is None,
                "human_model": getattr(self.maia, "label", "Maia-2"),
                "lichess_error": self.stats.lichess.error,
                "stats": self.stats_info, "opponent": opp_meta,
                "traps": trap_list, "hard_lines": hard_list, "novelties": nov_list,
                "tree": self._node_json(self.root)}


REFUTATION_PLIES = 9  # zły ruch + 4 ruchy kary z każdej strony


def refutation(board: chess.Board, pv: list[str], plies: int = REFUTATION_PLIES) -> dict | None:
    """Linia silnika zaczynająca się od złego ruchu -> {"uci": [...], "san": [...]}."""
    b = board.copy(stack=False)
    uci, san = [], []
    for u in pv[:plies]:
        try:
            mv = chess.Move.from_uci(u)
        except ValueError:
            break
        if mv not in b.legal_moves:
            break
        san.append(b.san(mv))
        uci.append(u)
        b.push(mv)
    return {"uci": uci, "san": san} if len(uci) >= 2 else None


def p_depth(p: SearchParams) -> int:
    return max(p.verify_depth, p.depth)


def validate_history(params: SearchParams) -> None:
    """Ruchy od pre_fen muszą prowadzić do pozycji wyszukiwania - inaczej je pomijamy."""
    if not params.pre_fen:
        params.pre_moves = []
        return
    try:
        b = chess.Board(params.pre_fen)
        for u in params.pre_moves:
            mv = chess.Move.from_uci(u)
            if not b.is_legal(mv):
                raise ValueError(u)
            b.push(mv)
        if b.epd() != chess.Board(params.fen).epd():
            raise ValueError("inna pozycja")
    except (ValueError, AssertionError):
        params.pre_fen, params.pre_moves = "", []


# ---------------------------------------------------------------- zadania w tle

class JobManager:
    """Wyszukiwania w tle; jedno naraz (jeden silnik), kolejne czekają w kolejce."""

    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._queue: deque[Job] = deque()      # kolejka FIFO, obsługuje ją jeden wątek
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None
        self.dir = DATA_DIR / "searches"
        self.dir.mkdir(parents=True, exist_ok=True)

    def start(self, params: SearchParams) -> Job:
        validate_history(params)
        job = Job(params)
        job.on_checkpoint = lambda res, j=job: self._save(j, res)
        self.jobs[job.id] = job
        with self._cv:
            self._queue.append(job)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._loop, daemon=True)
                self._worker.start()
            self._cv.notify()
        return job

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        with self._cv:
            if job.status == "queued":  # jeszcze nie ruszyło - wyjmij z kolejki od razu
                try:
                    self._queue.remove(job)
                except ValueError:
                    pass
                job.status = "cancelled"
                job.finished = time.time()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                job = self._queue.popleft()
                if job.status != "queued":
                    continue
                job.status = "running"
            self._run(job)

    def _save(self, job: Job, res: dict) -> None:
        res["id"] = job.id
        res["created"] = time.strftime("%Y-%m-%d %H:%M")
        tmp = self.dir / f"{job.id}.tmp"
        tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / f"{job.id}.json")

    def _run(self, job: Job) -> None:
        job.started = time.time()
        dl = job.params.deadline
        if dl and dl - job.started < MIN_TIME_LEFT:
            # czekało w kolejce za długo - przy tak krótkim czasie nic sensownego nie wyjdzie
            job.status = "error"
            job.error = (f"Godzina końca ({time.strftime('%H:%M', time.localtime(dl))}) minęła albo jest "
                         "za blisko, zanim szukanie wyszło z kolejki")
            job.finished = time.time()
            return
        try:
            res = Searcher(job.params, job).run()
            res["partial"] = False
            self._save(job, res)
            job.result = res
            job.status = "cancelled" if job.cancel.is_set() else "done"
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    def queued(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status == "queued"]

    def saved(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("*.json"), key=lambda f: -f.stat().st_mtime):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pr = d.get("params", {})
            out.append({"id": d.get("id", f.stem), "created": d.get("created"), "fen": pr.get("fen"),
                        "color": pr.get("color"), "traps": len(d.get("traps", [])),
                        "hard": len(d.get("hard_lines", [])), "novelties": len(d.get("novelties", [])),
                        "elo": f"{pr.get('elo_min')}-{pr.get('elo_max')}",
                        "partial": bool(d.get("partial")) and d.get("id") not in self._running_ids(),
                        "opponent": (d.get("opponent") or {}).get("name")})
        return out

    def _running_ids(self) -> set:
        return {j.id for j in self.jobs.values() if j.status in ("queued", "running")}

    def load(self, job_id: str) -> dict | None:
        f = self.dir / f"{job_id}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        job = self.jobs.get(job_id)
        return job.result if job else None

    def delete(self, job_id: str) -> None:
        f = self.dir / f"{job_id}.json"
        if f.exists():
            f.unlink()
