"""Konfiguracja aplikacji.

Wartości domyślne są tutaj; nadpisuje je plik ``config.local.json`` w katalogu
głównym projektu (nie trafia do gita - trzymamy tam np. token Lichess).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
LOCAL_CONFIG = ROOT / "config.local.json"

DEFAULTS: dict = {
    # Silnik
    "stockfish_path": str(ROOT / "stockfish" / "stockfish-windows-x86-64-universal.exe"),
    "engine_threads": max(1, (os.cpu_count() or 2) - 1),
    "engine_hash_mb": 512,
    # Baza partii
    "pgn_path": str(ROOT / "games" / "TeraBaza.pgn"),
    "index_dir": str(DATA_DIR / "index"),
    "index_max_plies": 30,
    # Lichess
    "lichess_token": "",
    # Model ludzkich ruchów: "auto" (Maia-3, jeśli zainstalowana), "maia2", "maia3-5m", "maia3-23m"
    "human_model": "auto",
    # Maia-2 ("rapid" lub "blitz")
    "maia_type": "rapid",
    # Serwer lokalny
    "port": 8777,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if LOCAL_CONFIG.exists():
        try:
            cfg.update(json.loads(LOCAL_CONFIG.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover
            print(f"[config] Nie mogę odczytać {LOCAL_CONFIG}: {exc}")
    return cfg


def save_local(updates: dict) -> None:
    """Zapisuje wybrane klucze do config.local.json (zachowując resztę)."""
    current: dict = {}
    if LOCAL_CONFIG.exists():
        try:
            current = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    current.update(updates)
    LOCAL_CONFIG.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")


CONFIG = load()
