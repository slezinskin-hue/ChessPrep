"""Szukanie wariantów, które stawiają przeciwnikowi praktyczne problemy.

Idea
----
Budujemy drzewo od zadanej pozycji:

* **nasz ruch** - rozważamy ruchy silnika (MultiPV), których ocena z naszej
  perspektywy nie spada poniżej progu (``min_eval``) i nie jest gorsza od
  najlepszego o więcej niż ``max_drop``;
* **ruch przeciwnika** - rozważamy ruchy, które ludzie z wybranego zakresu
  rankingu faktycznie grają (Maia-2 zmieszana ze statystykami partii), aż
  pokryjemy ``opp_coverage`` prawdopodobieństwa, plus zawsze najlepszy ruch silnika.

Rozwijamy najpierw pozycje, do których przeciwnik najczęściej dojdzie
(best-first po prawdopodobieństwie dotarcia), w limicie ``budget`` analiz.

Miary (szansa wygranej liczona krzywą Lichess z centypionów):

* ``loss(m)`` - ile szansy wygranej przeciwnik oddaje ruchem m względem najlepszego,
* ``trick`` w węźle przeciwnika = Σ p(m)·loss(m) - oczekiwana strata przeciwnika tu i teraz,
* ``value`` - to samo zsumowane w głąb drzewa (przeciwnik może pomylić się teraz
  albo później), w naszych węzłach wybieramy najlepszy ruch pomniejszony o jego
  obiektywny koszt względem najlepszego ruchu silnika.

Na końcu ścieżki do najlepszych pułapek są weryfikowane na większej głębokości.
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
GOOD_LOSS = 0.08            # strata do tej wartości = ruch "w porządku"
WINNING_CP = 300            # od tej oceny uznajemy pozycję za wygraną - nie rozwijamy dalej
PRIOR_GAMES = 25            # ile partii "jest warta" Maia przy mieszaniu ze statystykami


def win_chance(cp: float) -> float:
    cp = max(-2000, min(2000, cp))
    return 1 / (1 + math.exp(-WIN_K * cp))


@dataclass
class SearchParams:
    fen: str = chess.STARTING_FEN
    color: str = "white"            # nasz kolor
    min_eval: int = -50             # minimalna ocena z NASZEJ perspektywy (cp)
    max_drop: int = 120             # maks. strata względem najlepszego ruchu (cp)
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
    use_local_db: bool = True
    speeds: list = field(default_factory=lambda: ["blitz", "rapid", "classical"])

    @property
    def side(self) -> chess.Color:
        return chess.WHITE if self.color == "white" else chess.BLACK


class Node:
    __slots__ = ("id", "board", "moves", "san_path", "parent", "move", "san", "reach", "children",
                 "expanded", "my_cp", "best_cp", "lines", "policy", "move_info", "trick",
                 "value", "local_games", "lichess_games", "terminal", "verified", "prob")

    _ids = itertools.count()

    def __init__(self, board: chess.Board, parent: "Node | None" = None, move: chess.Move | None = None,
                 reach: float = 1.0, prob: float = 1.0):
        self.id = next(Node._ids)
        self.board = board
        self.parent = parent
        self.move = move
        self.san = parent.board.san(move) if parent is not None and move is not None else ""
        self.reach = reach
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
            r = self.stats.lichess.query(board, self.p.elo_min, self.p.elo_max, self.p.speeds)
            if r["available"]:
                lichess_total = r["total"]
                for m in r["moves"]:
                    merged[m["uci"]] = merged.get(m["uci"], 0) + m["games"]
        return merged, local_total, lichess_total

    def __call__(self, board: chess.Board) -> tuple[dict[str, float], int | None, int | None]:
        maia = self.maia.probs(board, self.p.elo_min, self.p.elo_max)
        counts, lt, lit = self.counts(board)
        n = sum(counts.values())
        legal = {m.uci() for m in board.legal_moves}
        if not maia:  # brak Maii - same statystyki albo rozkład równomierny
            maia = {u: 1 / len(legal) for u in legal} if legal else {}
        if n == 0:
            return maia, lt, lit
        out = {}
        for u in legal:
            out[u] = (counts.get(u, 0) + PRIOR_GAMES * maia.get(u, 0.0)) / (n + PRIOR_GAMES)
        s = sum(out.values()) or 1.0
        out = {u: v / s for u, v in sorted(out.items(), key=lambda kv: -kv[1])}
        return out, lt, lit


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
            self.job.progress = self.analysed

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
        lines = self.eng.analyse(node.board, depth=p.depth, multipv=p.my_candidates)
        self.analysed += 1
        if not lines:
            return []
        node.lines = lines
        best = self.my_cp(lines[0])
        node.best_cp = node.my_cp = best
        if best >= WINNING_CP:
            node.terminal = "wygrana"
        if best < p.min_eval:
            node.terminal = "poniżej progu"
            return []
        out = []
        for line in lines:
            cp = self.my_cp(line)
            if cp < p.min_eval or best - cp > p.max_drop:
                continue
            mv = chess.Move.from_uci(line.move)
            nb = node.board.copy(stack=False)
            nb.push(mv)
            child = Node(nb, node, mv, node.reach)
            child.my_cp = cp
            node.children[line.move] = child
            out.append(child)
        return out if not node.terminal else []

    def _expand_opponent(self, node: Node) -> list[Node]:
        p = self.p
        b = node.board
        policy, lt, lit = self.policy(b)
        node.policy = policy
        node.local_games, node.lichess_games = lt, lit
        lines = self.eng.analyse(b, depth=p.depth, multipv=3)
        self.analysed += 1
        if not lines:
            return []
        node.lines = lines
        evals: dict[str, int] = {l.move: self.my_cp(l) for l in lines}
        # ruchy "ludzkie" do sprawdzenia
        chosen, cum = [], 0.0
        for u, pr in policy.items():
            if len(chosen) >= p.opp_max_replies + 2 or (cum >= p.opp_coverage and len(chosen) >= 1):
                break
            chosen.append(u)
            cum += pr
        missing = [chess.Move.from_uci(u) for u in chosen if u not in evals]
        if missing:
            for u, l in self.eng.eval_moves(b, missing, p.depth).items():
                evals[u] = self.my_cp(l)
        best_u = lines[0].move
        opp_best = min(evals.values())  # przeciwnik minimalizuje naszą ocenę
        node.best_cp = node.my_cp = opp_best
        wc_best = win_chance(opp_best)
        info = []
        trick = 0.0
        for u, cp in sorted(evals.items(), key=lambda kv: -policy.get(kv[0], 0)):
            loss = max(0.0, win_chance(cp) - wc_best)
            pr = policy.get(u, 0.0)
            trick += pr * loss
            info.append({"uci": u, "san": b.san(chess.Move.from_uci(u)), "p": round(pr, 4),
                         "cp": cp, "loss": round(loss, 4), "best": u == best_u})
        node.move_info = info
        node.trick = trick
        if opp_best >= WINNING_CP:
            node.terminal = "wygrana"
            return []
        out = []
        replies = 0
        for d in info:
            u = d["uci"]
            if d["loss"] > GOOD_LOSS and not d["best"]:
                continue  # przeciwnik się pomylił - dalej nie trzeba
            if not d["best"] and replies >= p.opp_max_replies:
                continue
            if d["cp"] < p.min_eval:
                continue  # nie powinno się zdarzyć (minimax), ale na wszelki wypadek
            mv = chess.Move.from_uci(u)
            nb = b.copy(stack=False)
            nb.push(mv)
            pr = d["p"]
            child = Node(nb, node, mv, node.reach * max(pr, 0.0), prob=pr)
            child.my_cp = d["cp"]
            node.children[u] = child
            out.append(child)
            replies += 1
        return out

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
        root_board = chess.Board(p.fen)
        root = Node(root_board)
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
                prio = child.reach
                if child.parent and child.parent.board.turn != self.side and child.prob < 0.1:
                    prio = child.parent.reach * 0.1  # najlepszy ruch silnika też chcemy sprawdzić
                heapq.heappush(heap, (-prio, child.ply, next(tie), child))
        self.backup(root)
        self._status("Weryfikacja najlepszych wariantów…")
        traps = self.collect_traps()
        if not self._cancelled():
            self.verify(traps[:12])
        self.backup(root)
        traps = self.collect_traps()
        self._fill_rarity(traps[:25])
        return self.export(traps)

    # ------------------------------------------------------------ wyniki
    def collect_traps(self) -> list[Node]:
        traps = [n for n in self.nodes.values()
                 if n.board.turn != self.side and n.expanded and n.trick >= 0.03
                 and n.verified is not False]
        traps.sort(key=lambda n: -(n.reach * n.trick + 0.15 * n.trick))
        return traps

    def verify(self, traps: list[Node]) -> None:
        checked: dict[int, bool] = {}
        for t in traps:
            ok = True
            for n in t.path()[1:]:
                if n.parent.board.turn != self.side:
                    continue  # sprawdzamy tylko pozycje po NASZYCH ruchach
                if n.id not in checked:
                    if self._cancelled():
                        return
                    self._status(f"Weryfikacja: {self._line_text(n)}")
                    lines = self.eng.analyse(n.board, depth=p_depth(self.p), multipv=1)
                    cp = self.my_cp(lines[0]) if lines else 0
                    n.my_cp = cp
                    checked[n.id] = cp >= self.p.min_eval
                    n.verified = checked[n.id]
                ok = ok and checked[n.id]
            t.verified = ok if t.verified is not False else False
            if not ok:
                self._prune_failed(t)

    def _prune_failed(self, trap: Node) -> None:
        for n in trap.path()[1:]:
            if n.verified is False and n.parent is not None:
                n.parent.children.pop(n.move.uci(), None)
                break

    def _fill_rarity(self, traps: list[Node]) -> None:
        seen = set()
        for t in traps:
            for n in t.path():
                if n.id in seen or n.local_games is not None or n.lichess_games is not None:
                    continue
                seen.add(n.id)
                _, n.local_games, n.lichess_games = self.policy.counts(n.board)

    def _node_json(self, n: Node) -> dict:
        return {
            "id": n.id, "san": n.san, "uci": n.move.uci() if n.move else "", "fen": n.board.fen(),
            "ply": n.ply, "reach": round(n.reach, 4), "prob": round(n.prob, 4),
            "eval": n.my_cp, "trick": round(n.trick, 4), "value": round(n.value, 4),
            "local_games": n.local_games, "lichess_games": n.lichess_games,
            "terminal": n.terminal, "verified": n.verified, "moves_info": n.move_info,
            "mine": n.board.turn == self.side,
            "children": [self._node_json(c) for c in sorted(n.children.values(), key=lambda c: -c.value)],
        }

    def export(self, traps: list[Node]) -> dict:
        trap_list = []
        for t in traps[:40]:
            path = t.path()
            good = [d for d in t.move_info if d["loss"] <= GOOD_LOSS]
            bad = [d for d in t.move_info if d["loss"] > GOOD_LOSS]
            trap_list.append({
                "node": t.id, "fen": t.board.fen(), "line": [n.san for n in path[1:]],
                "line_uci": [n.move.uci() for n in path[1:]],
                "reach": round(t.reach, 4), "trick": round(t.trick, 4),
                "error_chance": round(sum(d["p"] for d in bad), 4),
                "only_move": len(good) <= 1,
                "good": good, "bad": bad, "eval": t.my_cp, "verified": t.verified,
                "local_games": t.local_games, "lichess_games": t.lichess_games,
                "path_games": [{"san": n.san, "local": n.local_games, "lichess": n.lichess_games}
                               for n in path[1:]],
            })
        return {"params": asdict(self.p), "analysed": self.analysed,
                "engine": self.eng.name, "maia": self.maia.error is None,
                "lichess_error": self.stats.lichess.error,
                "traps": trap_list, "tree": self._node_json(self.root)}


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
