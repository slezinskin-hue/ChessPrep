"""Szukanie linii nowych i grywalnych oraz takich, w których przeciwnik ma trudne ruchy.

Idea
----
Budujemy drzewo od zadanej pozycji:

* **nasz ruch** - ruchy silnika (MultiPV) mieszczące się w progu oceny (``min_eval``)
  i nie gorsze od najlepszego o więcej niż ``max_drop``. W pozycjach "teoretycznych"
  (dużo partii) bierzemy więcej linii silnika, żeby wyłapać rzadkie, ale dobre ruchy
  (nowinki: strata ≤ ``novelty_max_loss`` względem najlepszego, ≤ ``novelty_max_games`` partii);
* **ruch przeciwnika** - ruchy, które ludzie z zakresu rankingu faktycznie grają
  (Maia-2 zmieszana ze statystykami partii) + najlepsze ruchy silnika. Wszystkie
  oceniamy jednym przeszukaniem MultiPV, więc oceny są porównywalne.

Miary w pozycji przeciwnika (szansa wygranej = krzywa Lichess z centypionów):

* ``loss(m)``  - ile szansy wygranej przeciwnik oddaje ruchem m względem najlepszego;
  ruch z ``loss ≥ MISTAKE`` (10%) to błąd,
* ``error``    - jaki odsetek ludzi zagra tu błąd (gdy silnik dowiódł, że wszystkie
  pozostałe ruchy też są błędami, ich masa też się liczy),
* ``trick``    - oczekiwana strata przeciwnika: Σ p(m)·loss(m) po błędach,
* **trudny moment** - ``error ≥ hard_error`` i dobry ruch nie jest oczywisty
  (nie jest odbiciem, wymuszoną ucieczką z szacha, ani ruchem, który Maia uważa
  za naturalny ≥ ``obvious_p``); **jedyny ruch** - tylko jeden ruch nie jest błędem,
* **ukryta pułapka** - silnik na małej głębokości (6) też wybrałby błędny ruch
  (por. jk_182, "Finding Traps with Stockfish": ruchy, które silnik odrzuca dopiero
  głębiej, łatwiej przeoczyć człowiekowi).

Kolejność rozwijania: best-first po prawdopodobieństwie dotarcia, przy czym po ruchu
przeciwnika liczymy je *warunkowo* - zakładając, że przeciwnik zagrał dobrze
(p(m)/p(dobre)). Dzięki temu po trudnym momencie badamy linię dalej i znajdujemy
serie trudnych ruchów, a nie tylko pojedyncze pułapki.

Wyniki: pułapki (pojedyncze pozycje), trudne linie (ścieżki z kilkoma trudnymi
momentami), nowinki. Najlepsze z każdej grupy są weryfikowane na większej głębokości.
"""
from __future__ import annotations

import heapq
import itertools
import json
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

import chess

from . import engine as engine_mod
from . import explorer as explorer_mod
from . import human as human_mod
from .config import DATA_DIR

WIN_K = 0.00368208          # krzywa Lichess: cp -> szansa wygranej
MISTAKE = 0.10              # spadek szansy wygranej od tej wartości = błąd (jak "mistake" na Lichess)
MIN_ERROR_CHANCE = 0.08     # pułapka = co najmniej tyle ludzi popełnia tu błąd
WINNING_CP = 300            # od tej oceny uznajemy pozycję za wygraną - nie rozwijamy dalej
PRIOR_GAMES = 25            # ile partii "jest warta" Maia przy mieszaniu ze statystykami
SHALLOW_DEPTH = 6           # "pierwsze wrażenie" silnika (ukryte pułapki)


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
    budget: int = 120               # ile pozycji przeanalizować
    my_candidates: int = 4          # MultiPV w naszych węzłach
    opp_coverage: float = 0.85
    opp_max_replies: int = 4
    depth: int = 16                 # głębokość silnika w trakcie szukania
    verify_depth: int = 21          # głębokość weryfikacji wyników
    use_lichess: bool = True
    lichess_source: str = "players"  # "players" | "masters" | "both"
    use_local_db: bool = True
    speeds: list = field(default_factory=lambda: ["blitz", "rapid", "classical"])
    # nowinki
    novelty_max_loss: int = 50      # nowinka może być gorsza od najlepszego ruchu najwyżej o tyle (cp)
    novelty_max_games: int = 10     # "rzadko grane" = najwyżej tyle partii z tym ruchem
    theory_min_games: int = 50      # pozycja jest "teorią", gdy ma co najmniej tyle partii
    novelty_multipv: int = 8        # ile linii silnika sprawdzać w pozycjach teoretycznych
    # trudne momenty
    hard_error: float = 0.25        # trudny moment = co najmniej tyle ludzi się tu myli
    obvious_p: float = 0.5          # dobry ruch z p(Maia) ≥ tyle uznajemy za oczywisty

    @property
    def side(self) -> chess.Color:
        return chess.WHITE if self.color == "white" else chess.BLACK


class Node:
    __slots__ = ("id", "board", "parent", "move", "san", "reach", "prio", "children",
                 "expanded", "my_cp", "best_cp", "lines", "policy", "move_info", "trick",
                 "value", "local_games", "lichess_games", "terminal", "verified", "prob",
                 "error", "p_good", "only_move", "moment", "hidden", "move_games", "novelty")

    _ids = itertools.count()

    def __init__(self, board: chess.Board, parent: "Node | None" = None, move: chess.Move | None = None,
                 reach: float = 1.0, prob: float = 1.0, prio: float = 1.0):
        self.id = next(Node._ids)
        self.board = board
        self.parent = parent
        self.move = move
        self.san = parent.board.san(move) if parent is not None and move is not None else ""
        self.reach = reach               # szansa dotarcia przy naturalnej grze przeciwnika
        self.prio = prio                 # j.w., ale zakładając, że przeciwnik gra dobre ruchy
        self.prob = prob                 # p(ruch) gdy to był ruch przeciwnika, inaczej 1
        self.children: dict[str, Node] = {}
        self.expanded = False
        self.my_cp: int | None = None    # ocena pozycji z naszej perspektywy
        self.best_cp: int | None = None
        self.lines: list = []
        self.policy: dict[str, float] = {}
        self.move_info: list[dict] = []  # (węzeł przeciwnika) ruchy z p, oceną i stratą
        self.trick = 0.0
        self.value = 0.0
        self.local_games: int | None = None
        self.lichess_games: int | None = None
        self.terminal = ""
        self.verified: bool | None = None
        self.error = 0.0
        self.p_good = 1.0
        self.only_move = False
        self.moment: dict | None = None  # opis trudnego momentu (węzeł przeciwnika)
        self.hidden = False
        self.move_games: dict[str, int] = {}  # (nasz węzeł) ile partii z każdym ruchem
        self.novelty: dict | None = None      # (węzeł po naszym ruchu) opis nowinki

    @property
    def ply(self) -> int:
        n, p = 0, self
        while p.parent is not None:
            n += 1
            p = p.parent
        return n

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
    """Maia-2 zmieszana ze statystykami partii z wybranego zakresu rankingu."""

    def __init__(self, params: SearchParams, maia: human_mod.Maia, stats: explorer_mod.Stats):
        self.p = params
        self.maia = maia
        self.stats = stats

    def counts(self, board: chess.Board) -> tuple[dict[str, int], int | None, int | None]:
        merged: dict[str, int] = {}
        local_total = lichess_total = None
        if self.p.use_local_db and self.stats.local.available:
            r = self.stats.local.query(board, self.p.elo_min, self.p.elo_max)
            if not r.get("beyond_index"):
                local_total = r["total"]
                for m in r["moves"]:
                    merged[m.uci] = merged.get(m.uci, 0) + m.games
        if self.p.use_lichess and self.stats.lichess.enabled:
            r = self.stats.lichess_query(board, self.p.elo_min, self.p.elo_max, self.p.speeds,
                                         self.p.lichess_source)
            if r["available"]:
                lichess_total = r["total"]
                for m in r["moves"]:
                    merged[m["uci"]] = merged.get(m["uci"], 0) + m["games"]
        return merged, local_total, lichess_total

    def __call__(self, board: chess.Board):
        """-> (rozkład ruchów, rozkład Maii, liczby partii per ruch, partie lokalnie, partie Lichess)"""
        maia = self.maia.probs(board, self.p.elo_min, self.p.elo_max)
        counts, lt, lit = self.counts(board)
        n = sum(counts.values())
        legal = {m.uci() for m in board.legal_moves}
        if not maia:  # brak Maii - same statystyki albo rozkład równomierny
            maia = {u: 1 / len(legal) for u in legal} if legal else {}
        if n == 0:
            return maia, maia, counts, lt, lit
        out = {}
        for u in legal:
            out[u] = (counts.get(u, 0) + PRIOR_GAMES * maia.get(u, 0.0)) / (n + PRIOR_GAMES)
        s = sum(out.values()) or 1.0
        out = {u: v / s for u, v in sorted(out.items(), key=lambda kv: -kv[1])}
        return out, maia, counts, lt, lit


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
        self.cancel = threading.Event()
        self.result: dict | None = None
        self.error: str | None = None

    def info(self) -> dict:
        elapsed = (self.finished or time.time()) - self.started
        eta = None
        if self.status == "running" and self.progress:
            eta = elapsed / self.progress * max(0, self.total - self.progress)
        return {"id": self.id, "status": self.status, "message": self.message,
                "progress": self.progress, "total": self.total, "elapsed": round(elapsed),
                "eta": round(eta) if eta is not None else None, "error": self.error}


def _is_recapture(board: chess.Board, move: chess.Move, last: chess.Move | None) -> bool:
    return last is not None and board.is_capture(move) and move.to_square == last.to_square


class Searcher:
    def __init__(self, params: SearchParams, job: Job | None = None,
                 eng: engine_mod.Engine | None = None, maia: human_mod.Maia | None = None,
                 stats: explorer_mod.Stats | None = None):
        self.p = params
        self.job = job
        self.eng = eng or engine_mod.shared()
        self.maia = maia or human_mod.shared()
        self.stats = stats or explorer_mod.shared()
        self.policy = HumanPolicy(params, self.maia, self.stats)
        self.side = params.side
        self.analysed = 0
        self.nodes: dict[int, Node] = {}

    # ------------------------------------------------------------ pomocnicze
    def _status(self, msg: str) -> None:
        if self.job:
            self.job.message = msg
            self.job.progress = min(self.analysed, self.job.total)

    def _cancelled(self) -> bool:
        return bool(self.job and self.job.cancel.is_set())

    def my_cp(self, line: engine_mod.Line) -> int:
        return line.cp_for(self.side)

    def _line_text(self, node: Node) -> str:
        return " ".join(n.san for n in node.path()[1:]) or "(start)"

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
        counts, lt, lit = self.policy.counts(node.board)
        node.move_games, node.local_games, node.lichess_games = counts, lt, lit
        theory = (node.games or 0) >= p.theory_min_games
        multipv = max(p.my_candidates, p.novelty_multipv if theory else 0)
        lines = self.eng.analyse(node.board, depth=p.depth, multipv=multipv)
        self.analysed += 1
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
            is_novelty = (theory and games <= p.novelty_max_games and best - cp <= p.novelty_max_loss)
            mv = chess.Move.from_uci(line.move)
            nb = node.board.copy(stack=False)
            nb.push(mv)
            # dodatkowe ruchy spoza top N (w pozycjach teoretycznych: boczne warianty i nowinki)
            # dostają niższy priorytet, żeby nie rozpraszać budżetu
            prio = node.prio if i < p.my_candidates else node.prio * (0.4 if is_novelty else 0.25)
            child = Node(nb, node, mv, node.reach, prio=prio)
            child.my_cp = cp
            if is_novelty:
                child.novelty = {"games": games, "parent_games": node.games, "best_cp": best,
                                 "best_move": lines[0].move, "loss": best - cp, "tn": games == 0}
            node.children[line.move] = child
            out.append(child)
        return out

    def _expand_opponent(self, node: Node) -> list[Node]:
        p = self.p
        b = node.board
        policy, maia, counts, lt, lit = self.policy(b)
        node.policy = policy
        node.local_games, node.lichess_games = lt, lit
        # płytszy przegląd tylko po to, by znać najlepsze ruchy silnika; dokładne oceny niżej
        lines = self.eng.analyse(b, depth=max(10, p.depth - 4), multipv=3)
        self.analysed += 1
        if not lines:
            return []
        node.lines = lines
        evals: dict[str, int] = {l.move: self.my_cp(l) for l in lines}
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
            full = self.eng.analyse(b, depth=p.depth, multipv=len(cands),
                                    moves=[chess.Move.from_uci(u) for u in cands])
            if full:
                evals = {l.move: self.my_cp(l) for l in full}
        best_u = min(evals, key=evals.get)  # przeciwnik minimalizuje naszą ocenę
        opp_best = evals[best_u]
        node.best_cp = node.my_cp = opp_best
        wc_best = win_chance(opp_best)

        info = []
        trick = 0.0
        for u, cp in sorted(evals.items(), key=lambda kv: -policy.get(kv[0], 0)):
            loss = max(0.0, win_chance(cp) - wc_best)
            pr = policy.get(u, 0.0)
            if loss >= MISTAKE:  # liczą się tylko prawdziwe błędy, nie drobne niedokładności
                trick += pr * loss
            info.append({"uci": u, "san": b.san(chess.Move.from_uci(u)), "p": round(pr, 4),
                         "maia": round(maia.get(u, 0.0), 4), "games": counts.get(u, 0),
                         "cp": cp, "loss": round(loss, 4), "best": u == best_u})
        good = [d for d in info if d["loss"] < MISTAKE]
        bad = [d for d in info if d["loss"] >= MISTAKE]
        # Czy pozostałe (nieocenione) ruchy też są błędami? Tak, jeśli drugi najlepszy ruch
        # silnika (spośród ocenionych są wszystkie z top-3) jest już błędem.
        ranked = sorted(info, key=lambda d: d["loss"])
        others_bad = len(ranked) >= 2 and ranked[1]["loss"] >= MISTAKE
        evaluated_mass = sum(d["p"] for d in info)
        error = sum(d["p"] for d in bad) + (max(0.0, 1 - evaluated_mass) if others_bad else 0.0)
        if others_bad:  # masa nieocenionych ruchów to też błędy - doliczamy minimalną stratę
            trick += max(0.0, 1 - evaluated_mass) * ranked[1]["loss"]
        node.move_info = info
        node.trick = trick
        node.error = error
        node.p_good = max(1e-6, sum(d["p"] for d in good))
        node.only_move = len(good) == 1 and others_bad
        self._classify_moment(node, good, bad)

        if opp_best >= WINNING_CP:
            node.terminal = "wygrana"
            return []
        out = []
        replies = 0
        for d in info:
            u = d["uci"]
            if d["loss"] >= MISTAKE and not d["best"]:
                continue  # przeciwnik się pomylił - dalej nie trzeba
            if not d["best"] and replies >= p.opp_max_replies:
                continue
            if d["cp"] < p.min_eval:
                continue  # nie powinno się zdarzyć (minimax), ale na wszelki wypadek
            mv = chess.Move.from_uci(u)
            nb = b.copy(stack=False)
            nb.push(mv)
            pr = d["p"]
            cond = pr / node.p_good if d["loss"] < MISTAKE else 0.05
            child = Node(nb, node, mv, node.reach * pr, prob=pr,
                         prio=node.prio * max(cond, 0.05))
            child.my_cp = d["cp"]
            node.children[u] = child
            out.append(child)
            replies += 1
        return out

    def _classify_moment(self, node: Node, good: list[dict], bad: list[dict]) -> None:
        """Czy to trudny moment dla przeciwnika i dlaczego."""
        p = self.p
        b = node.board
        if not bad or node.error < MIN_ERROR_CHANCE:
            return
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
            "bad": [{"san": d["san"], "uci": d["uci"], "p": d["p"], "cp": d["cp"]} for d in bad_sorted[:3]],
            "hidden": node.hidden,
        }

    # ------------------------------------------------------------ wartości
    def backup(self, node: Node) -> float:
        if not node.children:
            node.value = node.trick
            return node.value
        if node.board.turn == self.side:
            best_val = -1.0
            wc_best = win_chance(node.best_cp if node.best_cp is not None else 0)
            for c in node.children.values():
                v = self.backup(c) - max(0.0, wc_best - win_chance(c.my_cp or 0))
                best_val = max(best_val, v)
            node.value = max(0.0, best_val)
        else:
            node.value = node.trick + sum(c.prob * self.backup(c) for c in node.children.values())
        return node.value

    # ------------------------------------------------------------ główna pętla
    def run(self) -> dict:
        p = self.p
        root = Node(chess.Board(p.fen))
        self.root = root
        self.nodes[root.id] = root
        tie = itertools.count()
        heap: list = [(-1.0, 0, next(tie), root)]
        while heap and self.analysed < p.budget and not self._cancelled():
            _, _, _, node = heapq.heappop(heap)
            if node.expanded:
                continue
            self._status(f"Analiza: {self._line_text(node)}")
            for child in self.expand(node):
                self.nodes[child.id] = child
                if child.ply >= p.max_plies:
                    continue
                heapq.heappush(heap, (-child.prio, child.ply, next(tie), child))
        self.backup(root)
        traps = self.collect_traps()
        lines = self.collect_hard_lines()
        novelties = self.collect_novelties()
        if not self._cancelled():
            self._status("Weryfikacja najlepszych wyników…")
            self.verify_paths([t for t in traps[:10]] + [h["node"] for h in lines[:8]])
            self.verify_novelties(novelties[:10])
        self.backup(root)
        traps = self.collect_traps()
        lines = self.collect_hard_lines()
        novelties = self.collect_novelties()
        self._fill_rarity([t for t in traps[:25]] + [h["node"] for h in lines[:15]])
        return self.export(traps, lines, novelties)

    # ------------------------------------------------------------ wyniki: pułapki
    def _alive(self, n: Node) -> bool:
        """Węzeł nadal w drzewie (nie odcięty przez weryfikację)."""
        while n.parent is not None:
            if n.parent.children.get(n.move.uci()) is not n:
                return False
            n = n.parent
        return True

    def collect_traps(self) -> list[Node]:
        cands = [n for n in self.nodes.values()
                 if n.board.turn != self.side and n.expanded and n.verified is not False
                 and n.error >= MIN_ERROR_CHANCE and n.trick >= 0.01 and self._alive(n)]
        cands.sort(key=lambda n: -(n.reach * n.trick + 0.15 * n.trick))
        traps, seen = [], set()
        for n in cands:  # ta sama pozycja przez transpozycję - tylko raz
            key = n.board.epd()
            if key not in seen:
                seen.add(key)
                traps.append(n)
        return traps

    # ------------------------------------------------------------ wyniki: trudne linie
    def collect_hard_lines(self) -> list[dict]:
        """Ścieżki, na których przeciwnik ma kilka trudnych momentów.

        Dla każdego węzła z trudnym momentem bierzemy ścieżkę od korzenia; momenty
        na ścieżce to wszystkie trudne pozycje przeciwnika po drodze. Zostawiamy
        najdłuższe (nie będące początkiem innej, bogatszej linii).
        """
        cands = []
        for n in self.nodes.values():
            if n.moment is None or n.verified is False or not self._alive(n):
                continue
            moments = [x for x in n.path() if x.moment is not None]
            survive = 1.0
            for x in moments:
                survive *= max(0.0, 1 - x.moment["error"])
            fail = 1 - survive
            first = moments[0]
            only = sum(1 for x in moments if x.moment["type"] == "only")
            # wynik: jak często dojdziemy do pierwszego momentu × szansa, że przeciwnik
            # gdzieś się pomyli; premia za liczbę momentów i jedyne ruchy
            score = (0.3 + first.reach) * fail * (1 + 0.25 * (len(moments) - 1) + 0.15 * only)
            cands.append({"node": n, "moments": moments, "survive": survive, "score": score,
                          "entry_reach": first.reach})
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
        return dedup

    # ------------------------------------------------------------ wyniki: nowinki
    def collect_novelties(self) -> list[Node]:
        out = [n for n in self.nodes.values()
               if n.novelty is not None and n.verified is not False and self._alive(n)]

        def score(n: Node) -> float:
            nv = n.novelty
            theory = min(1.0, math.log10(1 + (nv["parent_games"] or 0)) / 5)  # 100k partii -> 1
            quality = 1 - nv["loss"] / max(1, self.p.novelty_max_loss) * 0.5
            follow = n.value if n.expanded else 0.0
            return theory * quality * (1 + 6 * follow) * (1.2 if nv["tn"] else 1.0)

        for n in out:
            n.novelty["score"] = round(score(n), 4)
        out.sort(key=lambda n: -n.novelty["score"])
        dedup, seen = [], set()
        for n in out:
            key = n.board.epd()
            if key not in seen:
                seen.add(key)
                dedup.append(n)
        return dedup

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

    def verify_paths(self, targets: list[Node]) -> None:
        cache: dict[int, bool] = {}
        for t in targets:
            if self._cancelled():
                return
            ok = True
            for n in t.path()[1:]:
                if n.parent.board.turn != self.side:
                    continue  # sprawdzamy tylko pozycje po NASZYCH ruchach
                if not self._verify_node(n, cache):
                    ok = False
                    n.parent.children.pop(n.move.uci(), None)  # odetnij złą gałąź
                    break
            if t.verified is not False:
                t.verified = ok

    def verify_novelties(self, novs: list[Node]) -> None:
        cache: dict[int, bool] = {}
        for n in novs:
            if self._cancelled():
                return
            self.verify_paths([n])
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
            cache[n.id] = n.verified is not False

    def _fill_rarity(self, nodes: list[Node]) -> None:
        seen = set()
        for t in nodes:
            for n in t.path():
                if n.id in seen or n.local_games is not None or n.lichess_games is not None:
                    continue
                seen.add(n.id)
                _, n.local_games, n.lichess_games = self.policy.counts(n.board)

    # ------------------------------------------------------------ eksport
    def _node_json(self, n: Node) -> dict:
        return {
            "id": n.id, "san": n.san, "uci": n.move.uci() if n.move else "", "fen": n.board.fen(),
            "ply": n.ply, "reach": round(n.reach, 4), "prob": round(n.prob, 4),
            "eval": n.my_cp, "trick": round(n.trick, 4), "value": round(n.value, 4),
            "error": round(n.error, 4), "only_move": n.only_move, "moment": n.moment,
            "hidden": n.hidden, "novelty": n.novelty,
            "local_games": n.local_games, "lichess_games": n.lichess_games,
            "terminal": n.terminal, "verified": n.verified, "moves_info": n.move_info,
            "mine": n.board.turn == self.side,
            "children": [self._node_json(c) for c in sorted(n.children.values(), key=lambda c: -c.value)],
        }

    @staticmethod
    def _path_json(n: Node) -> dict:
        path = n.path()
        return {"line": [x.san for x in path[1:]], "line_uci": [x.move.uci() for x in path[1:]]}

    def export(self, traps: list[Node], hard_lines: list[dict], novelties: list[Node]) -> dict:
        trap_list = []
        for t in traps[:40]:
            good = [d for d in t.move_info if d["loss"] < MISTAKE]
            bad = [d for d in t.move_info if d["loss"] >= MISTAKE]
            trap_list.append({
                "node": t.id, "fen": t.board.fen(), **self._path_json(t),
                "reach": round(t.reach, 4), "trick": round(t.trick, 4),
                "error_chance": round(t.error, 4), "only_move": t.only_move, "hidden": t.hidden,
                "hard": t.moment is not None,
                "good": good, "bad": bad, "eval": t.my_cp, "verified": t.verified,
                "local_games": t.local_games, "lichess_games": t.lichess_games,
            })
        hard_list = []
        for h in hard_lines[:30]:
            n = h["node"]
            moments = []
            for x in h["moments"]:
                moments.append({**x.moment, "ply": x.ply, "fen": x.board.fen(),
                                "reach": round(x.reach, 4), **self._path_json(x)})
            hard_list.append({"node": n.id, "fen": n.board.fen(), **self._path_json(n),
                              "moments": moments, "survive": round(h["survive"], 4),
                              "entry_reach": round(h["entry_reach"], 4),
                              "score": round(h["score"], 4), "verified": n.verified,
                              "local_games": n.local_games, "lichess_games": n.lichess_games})
        nov_list = []
        for n in novelties[:30]:
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
            while stack:
                x = stack.pop()
                if x.board.turn != self.side and x.trick >= 0.01 and x.error >= MIN_ERROR_CHANCE:
                    if follow_trap is None or x.trick * x.reach > follow_trap.trick * follow_trap.reach:
                        follow_trap = x
                stack.extend(x.children.values())
            nov_list.append({
                "node": n.id, "fen": n.board.fen(), "parent_fen": parent.board.fen(),
                **self._path_json(n), "san": n.san, "eval": n.my_cp,
                "best_san": best_san, "best_cp": nv["best_cp"], "loss": nv["loss"],
                "games": nv["games"], "parent_games": nv["parent_games"], "tn": nv["tn"],
                "score": nv.get("score"), "verified": n.verified, "value": round(n.value, 4),
                "error": round(n.error, 4), "trick": round(n.trick, 4),
                "replies": replies,
                "follow_trap": ({**self._path_json(follow_trap), "trick": round(follow_trap.trick, 4),
                                 "error": round(follow_trap.error, 4)} if follow_trap else None),
            })
        return {"params": asdict(self.p), "analysed": self.analysed,
                "engine": self.eng.name, "maia": self.maia.error is None,
                "lichess_error": self.stats.lichess.error,
                "traps": trap_list, "hard_lines": hard_list, "novelties": nov_list,
                "tree": self._node_json(self.root)}


def p_depth(p: SearchParams) -> int:
    return max(p.verify_depth, p.depth)


# ---------------------------------------------------------------- zadania w tle

class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()  # jedno wyszukiwanie naraz (jeden silnik)
        self.dir = DATA_DIR / "searches"
        self.dir.mkdir(parents=True, exist_ok=True)

    def start(self, params: SearchParams) -> Job:
        job = Job(params)
        self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        with self._lock:
            if job.cancel.is_set():
                job.status = "cancelled"
                return
            job.status = "running"
            job.started = time.time()
            try:
                res = Searcher(job.params, job).run()
                res["id"] = job.id
                res["created"] = time.strftime("%Y-%m-%d %H:%M")
                job.result = res
                (self.dir / f"{job.id}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                job.status = "cancelled" if job.cancel.is_set() else "done"
            except Exception as exc:  # pragma: no cover
                import traceback
                traceback.print_exc()
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished = time.time()

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
                        "elo": f"{pr.get('elo_min')}-{pr.get('elo_max')}"})
        return out

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
