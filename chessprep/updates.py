"""Sprawdzanie i instalowanie aktualizacji modeli ludzkich ruchów.

Przy starcie okno pyta ``/api/updates`` - sprawdzamy (bez danych użytkownika):

* **kod Mai-3** - commit zainstalowanej paczki (``direct_url.json`` z instalacji przez pip
  z GitHuba) vs najnowszy commit na github.com/CSSLab/maia3 (pokazujemy datę i opis),
* **wagi Mai-3** - suma SHA-256 pobranego pliku ``.pt`` vs plik na Hugging Face (zmiana samego
  README nie jest aktualizacją),
* **Maia-2** - wersja paczki vs PyPI.

Nic nie instaluje się samo. Po kliknięciu „Aktualizuj”:

* wagi pobieramy od razu (to tylko plik; model ładuje się od nowa),
* paczki (pip) instalujemy **przy następnym uruchomieniu programu, zanim załaduje się torch**
  (``run_pending`` w ``run.py``) - wymiana bibliotek załadowanych w działającym procesie mogłaby
  na Windows zepsuć środowisko. Najpierw suchy przebieg pip pokazuje, co się zmieni (np. torch).
  Maia-3 instalujemy z przypiętego commita - tego, który pokazało sprawdzenie.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from importlib import metadata
from pathlib import Path

from .config import ROOT

MAIA3_REPO = "CSSLab/maia3"
MAIA3_GIT = f"git+https://github.com/{MAIA3_REPO}.git"
WEIGHTS_DIR = ROOT / "models" / "maia3"
PENDING = ROOT / "models" / "pending-update.json"
RESULT = ROOT / "models" / "update-result.json"
UA = {"User-Agent": "ChessPrep-update-check"}
_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
WATCH = ("torch", "numpy")  # zmiany tych paczek pokazujemy wyraźnie

_state = {"running": False, "message": "", "error": None, "restart": False, "done": None}
_lock = threading.Lock()
_cache: dict = {}


def _get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - stałe adresy https
        return json.loads(r.read().decode("utf-8"))


def _installed_maia3_commit() -> str | None:
    try:
        dist = metadata.distribution("maia3")
    except metadata.PackageNotFoundError:
        return None
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        return json.loads(raw).get("vcs_info", {}).get("commit_id")
    except json.JSONDecodeError:
        return None


def local_weights() -> dict[str, dict]:
    """Pobrane wagi Mai-3: repozytorium Hugging Face -> {"rev", "files": {nazwa: ścieżka}}."""
    out = {}
    if not WEIGHTS_DIR.is_dir():
        return out
    for d in WEIGHTS_DIR.glob("models--*"):
        ref = d / "refs" / "main"
        if not ref.is_file():
            continue
        rev = ref.read_text().strip()
        snap = d / "snapshots" / rev
        files = {f.name: f for f in snap.glob("*.pt")} if snap.is_dir() else {}
        out[d.name[len("models--"):].replace("--", "/")] = {"rev": rev, "files": files}
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def check(force: bool = False) -> dict:
    """Co można zaktualizować. Wynik pamiętany przez godzinę."""
    if not force and _cache.get("t", 0) > time.time() - 3600:
        return _with_files(_cache["res"])
    items, errors = [], []
    commit = _installed_maia3_commit()
    if commit:
        try:
            c = _get_json(f"https://api.github.com/repos/{MAIA3_REPO}/commits/HEAD")
            if c["sha"] != commit:
                msg = (c.get("commit", {}).get("message") or "").strip().splitlines()
                date = (c.get("commit", {}).get("committer", {}).get("date") or "")[:10]
                items.append({"id": "maia3-code", "name": "Maia-3 (program)", "installed": commit[:7],
                              "latest": c["sha"][:7], "sha": c["sha"], "restart": True,
                              "info": f"{date}: {msg[0][:100] if msg else ''}"})
        except Exception as exc:  # noqa: BLE001 - brak sieci itd.
            errors.append(f"GitHub: {type(exc).__name__}")
    for repo, loc in local_weights().items():
        try:
            d = _get_json(f"https://huggingface.co/api/models/{repo}?blobs=true")
            remote = {s["rfilename"]: (s.get("lfs") or {}).get("sha256") for s in d.get("siblings", [])}
            changed = [name for name, path in loc["files"].items()
                       if remote.get(name) and remote[name] != _sha256(path)]
            if changed:
                items.append({"id": f"weights:{repo}", "name": f"Maia-3 – wagi {repo.split('/')[-1]}",
                              "installed": loc["rev"][:7], "latest": d["sha"][:7], "restart": False,
                              "info": ", ".join(changed)})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Hugging Face: {type(exc).__name__}")
    try:
        ver = metadata.version("maia2")
        latest = _get_json("https://pypi.org/pypi/maia2/json")["info"]["version"]
        if latest != ver:
            items.append({"id": "maia2", "name": "Maia-2", "installed": ver, "latest": latest,
                          "restart": True, "info": ""})
    except metadata.PackageNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        errors.append(f"PyPI: {type(exc).__name__}")
    res = {"updates": items, "errors": errors, "checked": time.strftime("%H:%M")}
    _cache.update(t=time.time(), res=res)
    return _with_files(res)


def _with_files(res: dict) -> dict:
    """Do wyniku dokładamy: zaplanowaną instalację i wynik ostatniej (pokazywany raz)."""
    out = dict(res)
    out["pending"] = _read_json(PENDING)
    last = _read_json(RESULT)
    if last is not None:
        out["last_install"] = last
        try:
            RESULT.unlink()
        except OSError:
            pass
    return out


def status() -> dict:
    return dict(_state)


def _spec(item: dict) -> str:
    if item["id"] == "maia3-code":
        return f"{MAIA3_GIT}@{item['sha']}"
    return f"maia2=={item['latest']}"


def _dry_run(specs: list[str]) -> list[dict]:
    """Co pip by zainstalował: [{"name", "from", "to"}]."""
    with tempfile.TemporaryDirectory() as td:
        rep = Path(td) / "report.json"
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--dry-run", "--quiet",
                            "--report", str(rep), *specs],
                           capture_output=True, text=True, timeout=900, **_NO_WINDOW)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
            raise RuntimeError("pip: " + " ".join(tail))
        data = json.loads(rep.read_text(encoding="utf-8"))
    out = []
    for it in data.get("install", []):
        md = it.get("metadata", {})
        name = md.get("name", "?")
        try:
            cur = metadata.version(name)
        except metadata.PackageNotFoundError:
            cur = None
        out.append({"name": name, "from": cur, "to": md.get("version")})
    return out


def _download_weights(repo: str) -> None:
    from maia3.model_registry import resolve_checkpoint_path, resolve_model_spec
    resolve_checkpoint_path(resolve_model_spec(repo), cache_dir=str(WEIGHTS_DIR))


def _run(items: list[dict]) -> None:
    try:
        weights = [i for i in items if i["id"].startswith("weights:")]
        pkgs = [i for i in items if not i["id"].startswith("weights:")]
        for i in weights:
            _state["message"] = f"Pobieram nowe wagi {i['id'][8:]}…"
            _download_weights(i["id"][8:])
            from . import human
            human.reset_shared()  # następne zapytanie załaduje nowe wagi
        msg = "Nowe wagi w użyciu." if weights else ""
        if pkgs:
            _state["message"] = "Sprawdzam, co się zmieni (pip)…"
            specs = [_spec(i) for i in pkgs]
            changes = _dry_run(specs)
            PENDING.parent.mkdir(parents=True, exist_ok=True)
            PENDING.write_text(json.dumps({"specs": specs, "items": [i["name"] for i in pkgs],
                                           "changes": changes, "created": time.strftime("%Y-%m-%d %H:%M")},
                                          ensure_ascii=False), encoding="utf-8")
            ch = ", ".join(f"{c['name']} {c['from'] or '–'} → {c['to']}" for c in changes) or "bez zmian paczek"
            watch = [c["name"] for c in changes if c["name"].lower() in WATCH]
            msg += (f" Zainstaluję przy następnym uruchomieniu programu: {ch}."
                    + (f" Uwaga: zmieni się {', '.join(watch)} (duże pobieranie)." if watch else ""))
            _state["restart"] = True
        _state["message"] = msg.strip()
        _state["done"] = time.time()
        _cache.clear()
    except Exception as exc:  # noqa: BLE001
        _state["error"] = f"{type(exc).__name__}: {exc}"
        _state["message"] = "Aktualizacja nie powiodła się"
    finally:
        _state["running"] = False


def apply(ids: list[str]) -> dict:
    """Aktualizuje tylko to, co pokazało ostatnie sprawdzenie (inne identyfikatory są odrzucane)."""
    known = {i["id"]: i for i in (_cache.get("res") or {}).get("updates", [])}
    items = [known[i] for i in ids if i in known]
    if not items:
        return {**status(), "error": "Brak takiej aktualizacji - sprawdź jeszcze raz"}
    with _lock:
        if _state["running"]:
            return status()
        _state.update(running=True, message="Aktualizacja…", error=None, done=None, restart=False)
    threading.Thread(target=_run, args=(items,), daemon=True).start()
    return status()


def cancel_pending() -> None:
    try:
        PENDING.unlink()
    except OSError:
        pass


def run_pending() -> None:
    """Wołane z run.py przed załadowaniem programu (i torcha): instaluje zaplanowane paczki."""
    job = _read_json(PENDING)
    if not job:
        return
    try:
        PENDING.unlink()
    except OSError:
        return
    print("ChessPrep: instaluję aktualizację modeli:", ", ".join(job.get("items", [])), flush=True)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", *job.get("specs", [])],
                       capture_output=True, text=True, timeout=3600, **_NO_WINDOW)
    ok = r.returncode == 0
    tail = " ".join((r.stderr or r.stdout).strip().splitlines()[-3:]) if not ok else ""
    RESULT.write_text(json.dumps({"ok": ok, "items": job.get("items", []), "error": tail,
                                  "time": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False),
                      encoding="utf-8")
    print("ChessPrep: aktualizacja", "zakończona" if ok else "nie powiodła się: " + tail, flush=True)
