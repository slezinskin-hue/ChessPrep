"""Wybór bazy partii (PGN) i budowa jej indeksu w tle.

Każdy plik PGN ma własny katalog indeksu w ``data/indexes/<nazwa>-<rozmiar>``,
więc po przełączeniu z powrotem na wcześniej używaną bazę nie trzeba niczego
liczyć od nowa. Indeks buduje osobny proces (``python -m chessprep.dbindex build``),
żeby aplikacja działała normalnie; postęp czytamy z jego logu.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config as config_mod
from .config import CONFIG, DATA_DIR, ROOT

INDEXES_DIR = DATA_DIR / "indexes"
LEGACY_INDEX = DATA_DIR / "index"  # pierwszy indeks (sprzed wyboru baz)


def _read_meta(d: Path) -> dict | None:
    try:
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def index_dir_for(pgn: str) -> Path:
    """Katalog indeksu dla danego pliku PGN (istniejący lub docelowy)."""
    path = Path(pgn)
    size = path.stat().st_size if path.exists() else 0
    meta = _read_meta(LEGACY_INDEX)
    if meta and meta.get("pgn") == path.name and meta.get("pgn_size") == size:
        return LEGACY_INDEX
    stem = re.sub(r"[^\w.-]+", "_", path.stem)[:60] or "baza"
    return INDEXES_DIR / f"{stem}-{size}"


def active_pgn() -> str | None:
    pgn = CONFIG.get("pgn_path") or ""
    return pgn if pgn else None


def active_index_dir() -> Path | None:
    pgn = active_pgn()
    return index_dir_for(pgn) if pgn else None


_PROGRESS = re.compile(r"\|\s*(\d+)/(\d+)\s*\|\s*ETA\s*(\d+)\s*min")
_MERGE = re.compile(r"scalanie\s+(\d+)/256")


class IndexManager:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.building_pgn: str | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self.on_ready = None    # po zbudowaniu/zmianie indeksu (przeładowanie statystyk)
        self.on_release = None  # przed budową (zamknięcie zmapowanego indeksu)

    # ------------------------------------------------------------ stan
    def _log_path(self, pgn: str) -> Path:
        return index_dir_for(pgn) / "build.log"

    @property
    def building(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def progress(self) -> dict:
        pgn = self.building_pgn or active_pgn()
        out = {"running": self.building, "pct": None, "eta_min": None, "phase": None, "line": ""}
        if not pgn:
            return out
        log = self._log_path(pgn)
        if not log.exists():
            return out
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return out
        if not lines:
            return out
        out["line"] = lines[-1].strip()
        for ln in reversed(lines):
            m = _MERGE.search(ln)
            if m:
                out.update(phase="scalanie", pct=round(90 + 10 * int(m.group(1)) / 256, 1), eta_min=1)
                break
            m = _PROGRESS.search(ln)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                out.update(phase="czytanie partii", pct=round(90 * done / total, 1), eta_min=int(m.group(3)))
                break
            if ln.startswith("Faza 2"):
                out.update(phase="scalanie", pct=90.0)
                break
            if ln.startswith("Faza 1"):
                out.update(phase="czytanie partii", pct=0.0)
                break
        return out

    def status(self) -> dict:
        pgn = active_pgn()
        info: dict = {"pgn": pgn, "exists": bool(pgn and Path(pgn).exists()), "error": self.error,
                      "building": self.progress()}
        if pgn:
            d = index_dir_for(pgn)
            meta = _read_meta(d)
            info["index_dir"] = str(d)
            info["index"] = meta
            info["size_mb"] = round(Path(pgn).stat().st_size / 2 ** 20) if info["exists"] else None
            info["resumable"] = (d / "tmp").exists() and not self.building and meta is None
        return info

    # ------------------------------------------------------------ akcje
    def select(self, pgn: str, auto_build: bool = True) -> dict:
        path = Path(pgn)
        if not path.exists():
            raise FileNotFoundError(pgn)
        if self.building:
            raise RuntimeError("Trwa indeksowanie innej bazy - przerwij je najpierw.")
        CONFIG["pgn_path"] = str(path)
        config_mod.save_local({"pgn_path": str(path)})
        self.error = None
        if _read_meta(index_dir_for(str(path))) is None and auto_build:
            self.start_build()
        elif self.on_ready:
            self.on_ready()
        return self.status()

    def clear(self) -> None:
        if self.building:
            raise RuntimeError("Trwa indeksowanie - przerwij je najpierw.")
        CONFIG["pgn_path"] = ""
        config_mod.save_local({"pgn_path": ""})
        if self.on_ready:
            self.on_ready()

    def start_build(self, workers: int | None = None) -> None:
        with self._lock:
            if self.building:
                return
            pgn = active_pgn()
            if not pgn or not Path(pgn).exists():
                raise FileNotFoundError(pgn or "brak bazy")
            if self.on_release:
                self.on_release()  # zwolnij pliki starego indeksu (Windows blokuje zmapowane pliki)
            d = index_dir_for(pgn)
            d.mkdir(parents=True, exist_ok=True)
            workers = workers or max(1, min(3, (os.cpu_count() or 2) - 1))
            log = open(d / "build.log", "w", encoding="utf-8")
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            self.proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "chessprep.dbindex", "build", "--pgn", pgn,
                 "--index-dir", str(d), "--workers", str(workers)],
                cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=flags)
            self.building_pgn = pgn
            self.error = None
            threading.Thread(target=self._watch, args=(self.proc, log, pgn), daemon=True).start()

    def _watch(self, proc: subprocess.Popen, log, pgn: str) -> None:
        code = proc.wait()
        log.close()
        if code == 0:
            self.error = None
        elif self.error != "Przerwano":
            self.error = f"Indeksowanie zakończone błędem (kod {code}) - szczegóły w build.log"
        self.building_pgn = None
        if code == 0 and self.on_ready and pgn == active_pgn():
            self.on_ready()

    def cancel(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        self.error = "Przerwano"
        # cały proces z procesami-pomocnikami (multiprocessing)
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)


_shared: IndexManager | None = None


def shared() -> IndexManager:
    global _shared
    if _shared is None:
        _shared = IndexManager()
    return _shared
