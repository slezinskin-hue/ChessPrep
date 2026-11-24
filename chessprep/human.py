"""Model "ludzkich" ruchów: Maia-2 albo Maia-3 (CSSLab, Uniwersytet w Toronto).

Oba modele przewidują, jaki ruch zagra człowiek o danym rankingu.

* **Maia-2** (pakiet ``maia2``) zna przedziały rankingu co 100 od <1100 do >=2000; powyżej
  2000 nie rozróżnia już siły. Wagi (~100 MB) pobierają się same do ``models/maia2``.
* **Maia-3** (pakiet ``maia3`` z https://github.com/CSSLab/maia3, model "Chessformer", 2026)
  zna rankingi ok. 600-2600, bierze pod uwagę ranking obu graczy i 7 poprzednich pozycji,
  i przewiduje trafniej (57,1% vs 52,0% trafionych ruchów wg autorów). Wagi pobierają się
  z Hugging Face do ``models/maia3``. Instalacja (osobno, z repozytorium autorów):
  ``.venv\\Scripts\\python -m pip install git+https://github.com/CSSLab/maia3.git``

Wybór modelu: ``human_model`` w konfiguracji: "auto" (Maia-3, jeśli jest zainstalowana,
inaczej Maia-2), "maia2", "maia3-5m", "maia3-23m".
"""
from __future__ import annotations

import threading
from collections import OrderedDict, deque

import chess

from .config import CONFIG, ROOT

MAIA_MIN, MAIA_MAX = 1100, 2000       # Maia-2
MAIA3_MIN, MAIA3_MAX = 600, 2600      # Maia-3 (zakres danych treningowych)
MODELS = {"maia2": "Maia-2", "maia3-5m": "Maia-3 (5M)", "maia3-23m": "Maia-3 (23M)"}


def maia3_installed() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("maia3") is not None
    except Exception:
        return False


def _sample_elos(lo: int, hi: int, a: int, b: int) -> list[int]:
    """Do 4 punktów rankingu z zakresu [lo, hi] przyciętego do [a, b]."""
    lo, hi = max(a - 100, min(b, lo)), max(a - 100, min(b, hi))
    if hi < lo:
        lo, hi = hi, lo
    elos = list(range((lo // 100) * 100, hi + 1, 100)) or [lo]
    if len(elos) > 4:
        step = (len(elos) - 1) / 3
        elos = [elos[round(i * step)] for i in range(4)]
    return elos


class _Cache:
    def __init__(self, size: int = 20000):
        self.d: OrderedDict = OrderedDict()
        self.size = size

    def get(self, key):
        v = self.d.get(key)
        if v is not None:
            self.d.move_to_end(key)
        return v

    def put(self, key, value):
        self.d[key] = value
        if len(self.d) > self.size:
            self.d.popitem(last=False)


class Maia:
    """Maia-2."""

    name = "maia2"
    rating_range = (MAIA_MIN, MAIA_MAX)

    def __init__(self, model_type: str | None = None):
        self.model_type = model_type or CONFIG["maia_type"]
        self._model = None
        self._prepared = None
        self._lock = threading.Lock()
        self._cache = _Cache()
        self.error: str | None = None

    @property
    def label(self) -> str:
        return MODELS[self.name]

    @property
    def loaded(self) -> bool:
        return self._model is not None

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
        return _sample_elos(elo_min, elo_max, MAIA_MIN, MAIA_MAX)

    def probs(self, board: chess.Board, elo_min: int, elo_max: int,
              history: list[chess.Board] | None = None) -> dict[str, float]:
        """Prawdopodobieństwa ruchów (UCI -> p) dla gracza na ruchu - średnia po kilku
        rankingach z zakresu. ``history`` (poprzednie pozycje) Maia-2 nie używa."""
        if board.is_game_over():
            return {}
        if not self.load():
            return {}
        elos = self._elos(elo_min, elo_max)
        key = (board.epd(), tuple(elos))
        hit = self._cache.get(key)
        if hit is not None:
            return hit

        import torch
        from maia2.inference import _masked_softmax, preprocessing
        from maia2.utils import mirror_move

        all_moves_dict, elo_dict, rev = self._prepared
        fen = board.fen()
        boards, es, eo, legal = [], [], [], []
        for e in elos:
            b, s, o, l = preprocessing(fen, e, e, elo_dict, all_moves_dict)
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
        self._cache.put(key, out)
        return out


class Maia3:
    """Maia-3 (pakiet ``maia3``), wywoływana bezpośrednio, bez procesu UCI.

    Gdy Maia-3 nie da się załadować (brak sieci przy pierwszym pobraniu wag, niezgodna wersja
    pakietu itd.), zapytania obsługuje Maia-2 (``fallback``), a błąd zostaje w ``error``.
    """

    rating_range = (MAIA3_MIN, MAIA3_MAX)

    def __init__(self, name: str = "maia3-5m"):
        self._name = name
        self._model = None
        self._cfg = None
        self._lock = threading.Lock()
        self._cache = _Cache()
        self.error: str | None = None
        self.fallback: Maia | None = None

    @property
    def name(self) -> str:
        return self.fallback.name if self.fallback is not None else self._name

    @property
    def label(self) -> str:
        if self.fallback is not None:
            return f"{self.fallback.label} (Maia-3 nie działa)"
        return MODELS.get(self._name, self._name)

    @property
    def loaded(self) -> bool:
        return self._model is not None or (self.fallback is not None and self.fallback.loaded)

    def load(self) -> bool:
        if self._model is not None:
            return True
        if self.fallback is not None:
            return self.fallback.load()
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch
                from maia3.model_registry import resolve_checkpoint_path
                from maia3.uci import load_model, parse_args
                from maia3.utils import get_all_possible_moves
                torch.set_num_threads(2)
                cache = ROOT / "models" / "maia3"
                cache.mkdir(parents=True, exist_ok=True)
                cfg = parse_args(["--model", self._name, "--device", "cpu", "--no-use-amp",
                                  "--cache-dir", str(cache)])
                try:  # wagi z dysku - nowe wersje instaluje użytkownik (updates.py)
                    cfg.checkpoint_path = resolve_checkpoint_path(cfg.model_spec, cache_dir=str(cache),
                                                                  local_files_only=True)
                except Exception:  # noqa: BLE001 - jeszcze nie pobrane
                    cfg.checkpoint_path = resolve_checkpoint_path(cfg.model_spec, cache_dir=str(cache))
                self._model = load_model(cfg)
                self._cfg = cfg
                self._all = get_all_possible_moves()
                self._all_dict = {m: i for i, m in enumerate(self._all)}
            except BaseException as exc:  # parse_args może zgłosić SystemExit
                self.error = f"{type(exc).__name__}: {exc}"
                print(f"[maia3] Nie udało się załadować modelu: {self.error} - używam Mai-2")
                self.fallback = Maia()
        if self.fallback is not None:
            return self.fallback.load()
        return True

    def _run(self, board: chess.Board, elo_min: int, elo_max: int,
             history: list[chess.Board] | None):
        import torch
        from maia3.dataset import get_historical_tokens, get_legal_moves_mask, tokenize_board
        from maia3.utils import mirror_move

        cfg = self._cfg
        if elo_max > MAIA3_MAX:
            # zakres sięga ponad dane treningowe (np. 2000-3300): populacja jest silniejsza niż
            # dół zakresu - przesuwamy dół o tyle, o ile zakres wychodzi ponad model, najwyżej do
            # 2400 (2000-3300 -> 2400-2600, 1800-2700 -> 1900-2600; przegląd 24.11)
            elo_min = max(elo_min, min(MAIA3_MAX - 200, elo_min + (elo_max - MAIA3_MAX)))
        elos = _sample_elos(elo_min, elo_max, MAIA3_MIN, MAIA3_MAX)
        hist = deque(maxlen=cfg.history)
        for b in (history or [])[-(cfg.history - 1):]:
            hist.append(tokenize_board(b))
        hist.append(tokenize_board(board))
        tokens = get_historical_tokens(hist, cfg, base=0.0, inc=0.0, clk_left_before=0.0, clk_ponder=0.0)
        batch = tokens.unsqueeze(0).repeat(len(elos), *([1] * tokens.dim()))
        e = torch.tensor(elos, dtype=torch.long)
        mask = get_legal_moves_mask(board, self._all_dict)
        with self._lock, torch.no_grad():
            logits_move, logits_value, _ = self._model(batch, e, e)
            logits = logits_move.float().masked_fill(~mask.unsqueeze(0), float("-inf"))
            p = torch.softmax(logits, dim=-1).mean(dim=0)
            loss, draw, win = torch.softmax(logits_value.float(), dim=-1).mean(dim=0).tolist()
        black = board.turn == chess.BLACK
        out = {}
        for i in mask.nonzero().flatten().tolist():
            u = self._all[i]
            if black:
                u = mirror_move(u)
            try:
                mv = chess.Move.from_uci(u)
            except ValueError:
                continue
            if board.is_legal(mv):
                out[u] = out.get(u, 0.0) + float(p[i])
        s = sum(out.values()) or 1.0
        out = {u: v / s for u, v in sorted(out.items(), key=lambda kv: -kv[1])}
        return out, (win, draw, loss)

    def _hist_key(self, history):
        return tuple(b.epd() for b in (history or [])[-(self._cfg.history - 1):]) if self._cfg else ()

    def probs(self, board: chess.Board, elo_min: int, elo_max: int,
              history: list[chess.Board] | None = None) -> dict[str, float]:
        if board.is_game_over() or not self.load():
            return {}
        if self.fallback is not None:
            return self.fallback.probs(board, elo_min, elo_max, history)
        key = ("p", board.epd(), elo_min, elo_max, self._hist_key(history))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        out, wdl = self._run(board, elo_min, elo_max, history)
        self._cache.put(key, out)
        self._cache.put(("v",) + key[1:], wdl)
        return out

    def wdl(self, board: chess.Board, elo_min: int, elo_max: int,
            history: list[chess.Board] | None = None) -> tuple[float, float, float] | None:
        """Przewidywany wynik partii ludzi (wygrana, remis, przegrana) dla strony na ruchu."""
        if board.is_game_over() or not self.load() or self.fallback is not None:
            return None
        key = ("v", board.epd(), elo_min, elo_max, self._hist_key(history))
        hit = self._cache.get(key)
        if hit is None:
            self.probs(board, elo_min, elo_max, history)
            hit = self._cache.get(key)
        return hit


_shared = None
_shared_name: str | None = None


def resolve_name(name: str | None = None) -> str:
    name = (name or CONFIG.get("human_model") or "auto").lower()
    if name == "auto":
        return "maia3-5m" if maia3_installed() else "maia2"
    if name.startswith("maia3") and not maia3_installed():
        return "maia2"
    return name if name in MODELS else "maia2"


def shared():
    """Wspólny model ludzkich ruchów (wg ustawienia ``human_model``)."""
    global _shared, _shared_name
    name = resolve_name()
    if _shared is None or _shared_name != name:
        _shared = Maia() if name == "maia2" else Maia3(name)
        _shared_name = name
    return _shared


def reset_shared() -> None:
    """Następne ``shared()`` utworzy model od nowa (np. po pobraniu nowych wag)."""
    global _shared, _shared_name
    _shared, _shared_name = None, None
