"""Model "ludzkich" ruchów: Maia-2 (CSSLab, Uniwersytet w Toronto).

Maia-2 przewiduje, jaki ruch zagra człowiek o danym rankingu. Model zna
przedziały rankingu co 100 od <1100 do >=2000 - powyżej 2000 nie rozróżnia
już siły, dlatego dla wysokich zakresów warto mieszać go ze statystykami bazy
(robi to ``search.HumanPolicy``).

Wagi (~100 MB) pobierają się automatycznie przy pierwszym użyciu do models/maia2
(oficjalny link autorów, sprawdzany sumą SHA-256).
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import chess

from .config import CONFIG, ROOT

MAIA_MIN, MAIA_MAX = 1100, 2000


class Maia:
    def __init__(self, model_type: str | None = None):
        self.model_type = model_type or CONFIG["maia_type"]
        self._model = None
        self._prepared = None
        self._lock = threading.Lock()
        self._cache: OrderedDict = OrderedDict()
        self.error: str | None = None

    def load(self) -> bool:
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch
                from maia2 import inference, model
                torch.set_num_threads(2)
                save_root = ROOT / "models" / "maia2"
                save_root.mkdir(parents=True, exist_ok=True)
                self._model = model.from_pretrained(type=self.model_type, device="cpu",
                                                    save_root=str(save_root))
                self._model.eval()
                self._prepared = inference.prepare()
            except Exception as exc:  # brak sieci, brak pakietu itd.
                self.error = f"{type(exc).__name__}: {exc}"
                print(f"[maia] Nie udało się załadować modelu: {self.error}")
                return False
        return True

    @staticmethod
    def _elos(elo_min: int, elo_max: int) -> list[int]:
        lo = max(MAIA_MIN - 100, min(MAIA_MAX, elo_min))
        hi = max(MAIA_MIN - 100, min(MAIA_MAX, elo_max))
        if hi < lo:
            lo, hi = hi, lo
        elos = list(range((lo // 100) * 100, hi + 1, 100)) or [lo]
        if len(elos) > 4:  # wystarczy kilka punktów z zakresu
            step = (len(elos) - 1) / 3
            elos = [elos[round(i * step)] for i in range(4)]
        return elos

    def probs(self, board: chess.Board, elo_min: int, elo_max: int,
              elo_self_offset: int = 0) -> dict[str, float]:
        """Prawdopodobieństwa ruchów (UCI -> p) dla gracza na ruchu.

        Wynik to średnia po kilku rankingach z zakresu [elo_min, elo_max].
        Ranking przeciwnika (czyli nasz) przyjmujemy jako ten sam + offset.
        """
        if board.is_game_over():
            return {}
        if not self.load():
            return {}
        elos = self._elos(elo_min, elo_max)
        key = (board.epd(), tuple(elos), elo_self_offset)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit

        import torch
        from maia2.inference import _masked_softmax, preprocessing
        from maia2.utils import mirror_move

        all_moves_dict, elo_dict, rev = self._prepared
        fen = board.fen()
        boards, es, eo, legal = [], [], [], []
        for e in elos:
            b, s, o, l = preprocessing(fen, e, e + elo_self_offset, elo_dict, all_moves_dict)
            boards.append(b), es.append(s), eo.append(o), legal.append(l)
        with self._lock, torch.no_grad():
            logits, _, _ = self._model(torch.stack(boards), torch.tensor(es), torch.tensor(eo))
            p = _masked_softmax(logits, torch.stack(legal)).mean(dim=0)
        idx = legal[0].nonzero().flatten().tolist()
        black = board.turn == chess.BLACK
        out = {}
        for i in idx:
            mv = rev[i]
            out[mirror_move(mv) if black else mv] = float(p[i])
        out = dict(sorted(out.items(), key=lambda kv: -kv[1]))
        self._cache[key] = out
        if len(self._cache) > 20000:
            self._cache.popitem(last=False)
        return out


_shared: Maia | None = None


def shared() -> Maia:
    global _shared
    if _shared is None:
        _shared = Maia()
    return _shared
