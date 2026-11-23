"""Kalibracja przewidywanego odsetka błędów (model ludzkich ruchów vs prawdziwe partie).

Maia przeszacowuje wysokie odsetki błędów: na nocnym szukaniu (23.11.2026, 219 pozycji
z >= 100 partiami, ranking 1900-3300) przewidywane 60-100% błędu to w partiach ~42%,
a przewidywane 30-60% to ~33%. W pozycjach bez partii (tam, gdzie liczy się tylko model)
skalujemy więc przewidywany błąd krzywą kalibracji.

Krzywa startuje od tego pomiaru, a każde wyszukiwanie dopisuje nowe dane z pozycji,
w których są partie (``data/calibration.json``, osobno dla modelu i przedziału rankingu).
"""
from __future__ import annotations

import json
import threading

from .config import DATA_DIR

FILE = DATA_DIR / "calibration.json"
BINS = 10            # przedziały przewidywanego błędu: 0-10%, 10-20%, ...
MIN_BIN = 15         # od tylu pozycji w przedziale ufamy własnym danym
MIN_GAMES = 100      # pozycja wchodzi do kalibracji, gdy ma tyle partii

# punkt startowy: (przewidywany błąd, faktyczny błąd) z pomiaru opisanego wyżej
PRIOR = [(0.0, 0.0), (0.05, 0.05), (0.18, 0.16), (0.40, 0.33), (0.71, 0.42), (1.0, 0.55)]

_lock = threading.Lock()


def _key(model: str, elo_min: int, elo_max: int) -> str:
    return f"{model}|{elo_min // 200 * 200}-{elo_max // 200 * 200}"


def _load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _interp(points: list[tuple[float, float]], x: float) -> float:
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(1e-9, x1 - x0)
    return pts[-1][1]


class Calibration:
    """Krzywa: przewidywany błąd -> spodziewany faktyczny błąd."""

    def __init__(self, model: str, elo_min: int, elo_max: int):
        self.key = _key(model, elo_min, elo_max)
        self.bins = _load().get(self.key) or [[0.0, 0.0, 0] for _ in range(BINS)]
        self.new = [[0.0, 0.0, 0] for _ in range(BINS)]  # dane z bieżącego wyszukiwania
        self._points = self._build()

    def _build(self) -> list[tuple[float, float]]:
        own = []
        for i, (sp, se, n) in enumerate(self.bins):
            if n >= MIN_BIN:
                own.append((sp / n, se / n))
        if not own:
            return PRIOR
        # własne punkty zastępują punkty startowe z tego samego zakresu
        lo, hi = min(p for p, _ in own), max(p for p, _ in own)
        pts = [pt for pt in PRIOR if pt[0] < lo - 0.05 or pt[0] > hi + 0.05] + own
        if not any(p == 0.0 for p, _ in pts):
            pts.append((0.0, 0.0))
        return pts

    @property
    def points(self) -> list[tuple[float, float]]:
        return self._points

    def apply(self, predicted: float) -> float:
        """Skalibrowany odsetek błędów (nigdy większy niż przewidywany)."""
        if predicted <= 0:
            return 0.0
        return max(0.0, min(predicted, _interp(self._points, min(1.0, predicted))))

    def add(self, predicted: float, actual: float) -> None:
        i = min(BINS - 1, int(predicted * BINS))
        b = self.new[i]
        b[0] += predicted
        b[1] += actual
        b[2] += 1

    def save(self) -> int:
        """Dopisuje dane z wyszukiwania do pliku; zwraca liczbę nowych pozycji."""
        added = sum(b[2] for b in self.new)
        if not added:
            return 0
        with _lock:
            data = _load()
            cur = data.get(self.key) or [[0.0, 0.0, 0] for _ in range(BINS)]
            for c, b in zip(cur, self.new):
                c[0] += b[0]
                c[1] += b[1]
                c[2] += b[2]
            data[self.key] = cur
            DATA_DIR.mkdir(exist_ok=True)
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(FILE)
        self.new = [[0.0, 0.0, 0] for _ in range(BINS)]
        return added

    def summary(self) -> list[dict]:
        return [{"pred": round(sp / n, 3), "actual": round(se / n, 3), "n": n}
                for sp, se, n in self.bins if n]
