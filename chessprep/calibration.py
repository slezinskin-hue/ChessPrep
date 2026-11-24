"""Kalibracja przewidywanego odsetka błędów (model ludzkich ruchów vs prawdziwe partie).

Modele ludzkich ruchów (Maia-2, Maia-3) przeszacowują odsetek błędów, i to tym bardziej,
im silniejsza populacja: uczono je na partiach online, a porównujemy z partiami klasycznymi.

* Maia-2, gracze Lichess 1900-3300 (nocne szukanie 23.11.2026): przewidywane 60-100% błędu
  to w partiach ~42%, przewidywane 30-60% to ~33% (``PRIOR``).
* Maia-3, Masters + TeraBaza 2000-3300 (przegląd 24.11.2026): przewidywane 10-30% to w
  partiach ~2,5%, 30-60% to ~7% (Maia-2 tak samo). Pozycje z partiami to teoria (ludzie mylą się
  tam rzadziej), więc jako punkt startowy bierzemy kompromis ``PRIOR_STRONG`` (~1/3 przewidywania).
  Między nimi interpolujemy wg rankingu (1800-2200) i populacji (``prior_for``).
* Błędy, które silnik widzi od razu (do głębokości 6), silni gracze popełniali ~2× rzadziej niż
  ukryte - bez pomiaru na bazie krzywa dla nich to ``SHALLOW_FACTOR`` × krzywa ogólna.

Krzywa, której używamy (od najlepszej):

1. **pomiar na bazie** (``measured``) - zadanie „Kalibruj model”: losowe pozycje z partii
   z lokalnej bazy (poza teorią), osobne krzywe dla błędów widocznych płytko i ukrytych. Używany,
   gdy szukanie porównuje z partiami klasycznymi (baza lokalna albo Masters), z najbliższego
   zakresu rankingu tego samego modelu;
2. **dane z wyszukiwań** - pozycje z >= ``MIN_GAMES`` partiami (teoria), każda pozycja raz,
   ściągane do punktu startowego tym mocniej, im mniej danych;
3. **punkt startowy**.

Plik ``data/calibration.json``: klucze ``model|źródło|ranking`` (dane z wyszukiwań),
``seen`` -> klucz -> skróty pozycji już zapisanych, ``measured`` -> ``model|ranking`` (pomiar).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from .config import DATA_DIR

FILE = DATA_DIR / "calibration.json"
BINS = 10            # przedziały przewidywanego błędu: 0-10%, 10-20%, ...
SHRINK = 10          # siła ściągania do krzywej bazowej (ile "pozycji" jest wart punkt bazowy)
MIN_GAMES = 30       # pozycja z wyszukiwania wchodzi do kalibracji, gdy ma tyle partii
MIN_MEASURED = 200   # od tylu pozycji pomiaru na bazie używamy go zamiast danych z wyszukiwań
MIN_RATIO_DATA = 20  # tyle pozycji z przewidywaniem >= 10%, żeby przeskalować puste przedziały
SHALLOW_FACTOR = 0.6  # bez pomiaru: błędy widoczne od razu = 0,6 × krzywa ogólna
CLASSES = ("shallow", "deep")  # błąd widoczny płytko (<= 6) / ukryty

# punkt startowy: (przewidywany błąd, faktyczny błąd)
PRIOR = [(0.0, 0.0), (0.05, 0.05), (0.18, 0.16), (0.40, 0.33), (0.71, 0.42), (1.0, 0.55)]
PRIOR_STRONG = [(0.0, 0.0), (0.05, 0.03), (0.2, 0.07), (0.4, 0.13), (1.0, 0.3)]
_GRID = (0.0, 0.05, 0.1, 0.18, 0.2, 0.3, 0.4, 0.55, 0.71, 0.85, 1.0)

# klucze sprzed 0.6 (bez źródła) - z czego pochodziły dane (ustalone z zapisanych wyszukiwań)
LEGACY_SOURCE = {"maia3-5m|2000-3200": "local+masters", "maia3-5m|1800-3200": "local+both"}
LEGACY_DEFAULT = "local+players"

_lock = threading.Lock()


def _elo_key(elo_min: int, elo_max: int) -> str:
    return f"{elo_min // 200 * 200}-{elo_max // 200 * 200}"


def _key(model: str, source: str, elo_min: int, elo_max: int) -> str:
    return f"{model}|{source}|{_elo_key(elo_min, elo_max)}"


def measured_key(model: str, elo_min: int, elo_max: int) -> str:
    return f"{model}|{_elo_key(elo_min, elo_max)}"


def source_of(use_local_db: bool, use_lichess: bool, lichess_source: str) -> str:
    """Nazwa populacji partii, z którą porównujemy model (część klucza kalibracji)."""
    parts = []
    if use_local_db:
        parts.append("local")
    if use_lichess:
        parts.append(lichess_source)
    return "+".join(parts) or "none"


def classical(source: str) -> bool:
    """Czy populacja to głównie partie klasyczne (baza lokalna OTB albo Masters)."""
    return "masters" in source or source == "local"


def _interp(points: list[tuple[float, float]], x: float) -> float:
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(1e-9, x1 - x0)
    return pts[-1][1]


def prior_for(model: str, source: str, elo_min: int) -> list[tuple[float, float]]:
    """Krzywa startowa: od ``PRIOR`` (słabsi gracze, online) do ``PRIOR_STRONG`` (silni, partie
    klasyczne) - płynnie wg rankingu 1800-2200; populacja online liczy się w połowie."""
    t = min(1.0, max(0.0, (elo_min - 1800) / 400))
    w = t * (1.0 if classical(source) else 0.5)
    if w <= 0:
        return PRIOR
    return [(x, round((1 - w) * _interp(PRIOR, x) + w * _interp(PRIOR_STRONG, x), 4)) for x in _GRID]


def _migrate(data: dict) -> bool:
    """Klucze ``model|ranking`` sprzed 0.6 -> ``model|źródło|ranking`` (dane się sumują)."""
    changed = False
    for k in [k for k in data if k not in ("measured", "seen") and k.count("|") == 1]:
        model, elo = k.split("|")
        nk = f"{model}|{LEGACY_SOURCE.get(k, LEGACY_DEFAULT)}|{elo}"
        data[nk] = _merge(data.get(nk), data.pop(k))
        changed = True
    return changed


def _load() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if _migrate(data):
        try:
            _write(data)
        except OSError:
            pass
    return data


def _write(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for i in range(10):  # Windows: replace nie przejdzie, gdy ktoś akurat czyta plik
        try:
            tmp.replace(FILE)
            return
        except PermissionError:
            if i == 9:
                raise
            time.sleep(0.2)


def _empty() -> list[list]:
    return [[0.0, 0.0, 0] for _ in range(BINS)]


def _merge(*bins_lists) -> list[list]:
    out = _empty()
    for bl in bins_lists:
        for o, b in zip(out, bl or []):
            o[0] += b[0]
            o[1] += b[1]
            o[2] += b[2]
    return out


def fit(bins: list[list], base: list[tuple[float, float]], shrink: float = SHRINK) -> list[tuple[float, float]]:
    """Krzywa z danych w przedziałach, ściągnięta do krzywej bazowej (mało danych = bliżej bazy).
    Przedziały bez danych: krzywa bazowa przeskalowana tak, jak średnio przedziały z danymi od 10%
    wzwyż (przedział 0-10% to głównie teoria i ma inny charakter, więc skali nie wyznacza).
    Wynik jest niemalejący i nigdy nie przekracza przewidywania."""
    data = []
    for sp, se, n in bins:
        if n:
            x = sp / n
            b = _interp(base, x)
            data.append((x, (se + shrink * b) / (n + shrink), b, n))
    high = [d for d in data if d[0] >= 0.1]
    ratio = 1.0
    if sum(n for *_, n in high) >= MIN_RATIO_DATA:
        den = sum(b * n for _, _, b, n in high)
        if den > 1e-9:
            ratio = sum(y * n for _, y, _, n in high) / den
    pts = [(0.0, 0.0)] + [(x, y) for x, y, _, _ in data]
    filled = {min(BINS - 1, int(x * BINS)) for x, _, _, _ in data}
    for i in range(BINS):
        if i not in filled:
            x = (i + 0.5) / BINS
            pts.append((x, _interp(base, x) * ratio))
    pts.append((1.0, _interp(base, 1.0) * ratio))
    pts.sort()
    out, top = [], 0.0
    for x, y in pts:
        top = max(top, min(y, x) if x > 0 else 0.0)
        out.append((round(x, 4), round(top, 4)))
    return out


def _scaled(curve: list[tuple[float, float]], k: float) -> list[tuple[float, float]]:
    return [(x, round(y * k, 4)) for x, y in curve]


def pos_hash(epd: str) -> str:
    return hashlib.blake2b(epd.encode(), digest_size=6).hexdigest()


def _elo_range(s: str | None) -> tuple[int, int] | None:
    try:
        a, b = (s or "").split("-")
        return int(a), int(b)
    except ValueError:
        return None


def find_measured(data: dict, model: str, elo_min: int, elo_max: int) -> tuple[str, dict] | tuple[None, None]:
    """Pomiar na bazie dla modelu: ten sam przedział rankingu, a gdy go nie ma - pomiar o zakresie
    najbardziej pokrywającym się z [elo_min, elo_max] (co najmniej połowa krótszego z zakresów)."""
    allm = data.get("measured") or {}
    k = measured_key(model, elo_min, elo_max)
    if k in allm:
        return k, allm[k]
    best, best_ov = (None, None), 0.0
    for key, m in allm.items():
        if not key.startswith(model + "|"):
            continue
        r = _elo_range(m.get("elo"))
        if not r:
            continue
        ov = min(r[1], elo_max) - max(r[0], elo_min)
        need = 0.5 * min(r[1] - r[0], elo_max - elo_min)
        if ov >= need and ov > best_ov:
            best, best_ov = (key, m), ov
    return best


class Calibration:
    """Krzywa: przewidywany błąd -> spodziewany faktyczny błąd."""

    def __init__(self, model: str, elo_min: int, elo_max: int, source: str = "players"):
        self.model = model
        self.source = source
        self.key = _key(model, source, elo_min, elo_max)
        data = _load()
        self.bins = data.get(self.key) or _empty()
        self.seen = set((data.get("seen") or {}).get(self.key) or [])
        self.new = _empty()                              # dane z bieżącego wyszukiwania
        self.new_seen: set[str] = set()
        self.prior = prior_for(model, source, elo_min)
        self.measured_key, self.measured = (find_measured(data, model, elo_min, elo_max)
                                            if classical(source) or source == "none" else (None, None))
        self._since_rebuild = 0
        self.version = 0
        self._rebuild()

    def _rebuild(self) -> None:
        m = self.measured
        if m and m.get("positions", 0) >= MIN_MEASURED:
            self.kind = "measured"
            base = fit(m["all"], self.prior)
            self.curves = {None: base}
            for c in CLASSES:
                self.curves[c] = fit(m[c], base) if m.get(c) else base
        else:
            data = _merge(self.bins, self.new)
            self.kind = "search" if any(b[2] for b in data) else "prior"
            base = fit(data, self.prior)
            self.curves = {None: base, "shallow": _scaled(base, SHALLOW_FACTOR), "deep": base}
        self._since_rebuild = 0
        self.version = getattr(self, "version", 0) + 1

    @property
    def points(self) -> list[tuple[float, float]]:
        return self.curves[None]

    def apply(self, predicted: float, cls: str | None = None) -> float:
        """Skalibrowany odsetek błędów (nigdy większy niż przewidywany). ``cls`` - czy błąd
        widać płytko (``"shallow"``) czy jest ukryty (``"deep"``)."""
        if predicted <= 0:
            return 0.0
        curve = self.curves.get(cls) or self.curves[None]
        return max(0.0, min(predicted, _interp(curve, min(1.0, predicted))))

    def add(self, predicted: float, actual: float, epd: str | None = None) -> None:
        """Pozycja z partiami: przewidywany vs faktyczny odsetek błędów. Każda pozycja liczy się
        raz (także między wyszukiwaniami), żeby powtórki tego samego otwarcia nie przeważały."""
        if epd is not None:
            h = pos_hash(epd)
            if h in self.seen or h in self.new_seen:
                return
            self.new_seen.add(h)
        i = min(BINS - 1, int(predicted * BINS))
        b = self.new[i]
        b[0] += predicted
        b[1] += actual
        b[2] += 1
        self._since_rebuild += 1

    def refresh(self) -> None:
        """Krzywa z danymi z bieżącego szukania (wołane przed ostatecznym przeliczeniem wyników)."""
        if self.kind != "measured" and self._since_rebuild:
            self._rebuild()

    def save(self) -> int:
        """Dopisuje dane z wyszukiwania do pliku; zwraca liczbę nowych pozycji."""
        added = sum(b[2] for b in self.new)
        if not added:
            return 0
        with _lock:
            data = _load()
            data[self.key] = _merge(data.get(self.key), self.new)
            seen = data.setdefault("seen", {})
            seen[self.key] = sorted(set(seen.get(self.key) or []) | self.new_seen)
            _write(data)
        self.bins = _merge(self.bins, self.new)
        self.seen |= self.new_seen
        self.new, self.new_seen = _empty(), set()
        return added

    def summary(self) -> dict:
        data = _merge(self.bins, self.new)
        out = {"kind": self.kind, "key": self.key,
               "positions": sum(b[2] for b in data),
               "bins": [{"pred": round(sp / n, 3), "actual": round(se / n, 3), "n": n}
                        for sp, se, n in data if n]}
        if self.measured:
            out["measured"] = {"key": self.measured_key,
                               **{k: self.measured.get(k) for k in ("positions", "date", "source", "elo")}}
        return out


def save_measured(model: str, elo_min: int, elo_max: int, result: dict) -> None:
    """Dopisuje pomiar na bazie (zadanie „Kalibruj model”) - kolejne kalibracje tego samego
    modelu i rankingu sumują się (więcej pozycji = dokładniejsza krzywa)."""
    with _lock:
        data = _load()
        key = measured_key(model, elo_min, elo_max)
        old = data.setdefault("measured", {}).get(key) or {}
        new = {k: _merge(old.get(k), result.get(k)) for k in ("all",) + CLASSES}
        new.update(positions=old.get("positions", 0) + result.get("positions", 0),
                   errors=old.get("errors", 0) + result.get("errors", 0),
                   source=result.get("source"), depth=result.get("depth"),
                   date=time.strftime("%Y-%m-%d %H:%M"), elo=f"{elo_min}-{elo_max}")
        data["measured"][key] = new
        _write(data)


def measured_info() -> dict:
    return {k: {kk: v.get(kk) for kk in ("positions", "date", "source", "elo", "errors")}
            for k, v in (_load().get("measured") or {}).items()}
