"""Eksport wyników do PGN z rozdziałami i do studium Lichess.

Każda grupa wyników (rodzina wariantów) to osobny rozdział: od pozycji startowej
(z ruchami, które doprowadziły do pozycji wyszukiwania) przez wszystkie warianty
z rodziny, z komentarzami, błędami przeciwnika i karą za nie.

API Lichess (wymaga tokenu z uprawnieniem study:write):
  POST /api/study                          - nowe studium (tworzy też pusty rozdział)
  POST /api/study/{id}/import-pgn          - nowy rozdział z PGN
  DELETE /api/study/{id}/{chapterId}       - usunięcie rozdziału
  POST /api/token/test                     - uprawnienia tokenu
"""
from __future__ import annotations

import re
from concurrent.futures import TimeoutError as FutureTimeout

import chess
import chess.pgn
import requests

from .explorer import _HTTP_POOL

API = "https://lichess.org"
MISTAKE = 0.10
MAX_CHAPTERS = 64
HEADERS = {"User-Agent": "ChessPrep (github.com/slezinskin-hue/ChessPrep)"}
TOKEN_URL = ("https://lichess.org/account/oauth/token/create?scopes[]=study:write&scopes[]=study:read"
             "&description=ChessPrep")


class StudyError(RuntimeError):
    pass


# ------------------------------------------------------------------ PGN

def _fmt(cp: int | None, color: str) -> str:
    if cp is None:
        return "?"
    if abs(cp) >= 90_000:
        return "mat"
    w = cp if color == "white" else -cp  # oceny w wynikach są z perspektywy "mojego" koloru
    return f"{w / 100:+.2f}"


def _pct(x: float | None) -> str:
    return f"{(x or 0) * 100:.0f}%"


def _follow(node: chess.pgn.GameNode, ucis: list[str]) -> chess.pgn.GameNode | None:
    """Idzie (i w razie potrzeby dopisuje) wariant od węzła; None, gdy ruch nielegalny."""
    for u in ucis:
        mv = chess.Move.from_uci(u)
        if not node.board().is_legal(mv):
            return None
        node = node.variation(mv) if node.has_variation(mv) else node.add_variation(mv)
    return node


def _add_comment(node: chess.pgn.GameNode, text: str) -> None:
    if text and text not in node.comment:
        node.comment = (node.comment + " " + text).strip() if node.comment else text


def _add_bad(node: chess.pgn.GameNode, d: dict, color: str) -> None:
    """Zły ruch przeciwnika jako wariant z "?" i karą (linią silnika)."""
    mv = chess.Move.from_uci(d["uci"])
    if not node.board().is_legal(mv):
        return
    var = node.variation(mv) if node.has_variation(mv) else node.add_variation(mv)
    var.nags.add(chess.pgn.NAG_MISTAKE)
    pv = (d.get("pv") or {}).get("uci") or []
    _add_comment(var, f"zagra ok. {_pct(d.get('p'))} graczy, ocena {_fmt(d.get('cp'), color)}"
                      + ("; kara:" if len(pv) > 1 else ""))
    cur = var
    for u in pv[1:]:
        m = chess.Move.from_uci(u)
        if not cur.board().is_legal(m):
            break
        cur = cur.variation(m) if cur.has_variation(m) else cur.add_variation(m)


def _add_good(node: chess.pgn.GameNode, d: dict, only: bool) -> None:
    mv = chess.Move.from_uci(d["uci"])
    if not node.board().is_legal(mv):
        return
    if node.has_variation(mv):
        g = node.variation(mv)
    else:
        g = node.add_variation(mv)
    node.promote_to_main(mv)  # dobra obrona = główna linia
    g.nags.add(chess.pgn.NAG_GOOD_MOVE)
    if only:
        _add_comment(g, "jedyny dobry ruch")


def _trap(root, t: dict, color: str, num: int) -> None:
    end = _follow(root, t["line_uci"])
    if end is None:
        return
    for d in t.get("good", [])[:2]:
        _add_good(end, d, t.get("only_move"))
    for d in t.get("bad", [])[:3]:
        _add_bad(end, d, color)
    label = "PUŁAPKA" + (" (ukryta)" if t.get("hidden") else "")
    _add_comment(end, f"{label}: przeciwnik myli się w {_pct(t.get('error_chance'))} partii "
                      f"(dojście {_pct(t.get('reach'))}).")
    if t.get("note"):
        _add_comment(end, "Notatka: " + t["note"])


def _hard(root, h: dict, color: str, num: int) -> None:
    if _follow(root, h["line_uci"]) is None:
        return
    for j, m in enumerate(h.get("moments", [])):
        before = m["line_uci"]
        node = _follow(root, before)
        if node is None:
            continue
        kind = "JEDYNY RUCH" if m.get("type") == "only" else "TRUDNY WYBÓR"
        _add_comment(node, f"{kind} ({j + 1}/{len(h['moments'])}): myli się {_pct(m.get('error'))}.")
        for d in m.get("good", []):
            _add_good(node, d, m.get("type") == "only")
        for d in m.get("bad", [])[:2]:
            _add_bad(node, d, color)
    if h.get("note"):
        _add_comment(_follow(root, h["line_uci"]), "Notatka: " + h["note"])


def _novelty(root, n: dict, color: str, num: int) -> None:
    end = _follow(root, n["line_uci"])
    if end is None:
        return
    end.nags.add(146)  # N - nowinka
    end.nags.add(chess.pgn.NAG_SPECULATIVE_MOVE)
    games = "nigdy nie grane" if n.get("games") == 0 else f"grane {n.get('games')}×"
    _add_comment(end, f"NOWINKA: {games} na {n.get('parent_games') or 0} partii; ocena "
                      f"{_fmt(n.get('eval'), color)}, najlepszy ruch silnika {n.get('best_san') or '?'} "
                      f"{_fmt(n.get('best_cp'), color)}.")
    ft = n.get("follow_trap")
    if ft:
        node = _follow(root, ft["line_uci"])
        if node is not None:
            _add_comment(node, f"Pułapka po nowince: myli się {_pct(ft.get('error'))}.")
    if n.get("note"):
        _add_comment(end, "Notatka: " + n["note"])


def build_chapters(result: dict, kind: str, groups: list[dict], prefix: dict | None = None,
                   notes: dict[int, str] | None = None) -> list[dict]:
    """groups: [{"name": str, "items": [indeksy w result[kind]]}] -> [{"name", "pgn"}]."""
    params = result["params"]
    color = params["color"]
    key = {"traps": "traps", "hard": "hard_lines", "nov": "novelties"}[kind]
    items = result.get(key) or []
    add = {"traps": _trap, "hard": _hard, "nov": _novelty}[kind]

    # ruchy od pozycji startowej do pozycji wyszukiwania (jeśli pasują)
    start_fen, pre = params["fen"], []
    if prefix and prefix.get("start_fen") and prefix.get("moves"):
        b = chess.Board(prefix["start_fen"])
        ok = True
        for u in prefix["moves"]:
            mv = chess.Move.from_uci(u)
            if not b.is_legal(mv):
                ok = False
                break
            b.push(mv)
        if ok and b.epd() == chess.Board(params["fen"]).epd():
            start_fen, pre = prefix["start_fen"], prefix["moves"]

    out = []
    for g in groups[:MAX_CHAPTERS]:
        game = chess.pgn.Game()
        board = chess.Board(start_fen)
        if start_fen != chess.STARTING_FEN:
            game.setup(board)
        name = (g.get("name") or "ChessPrep")[:100]
        game.headers["Event"] = name
        game.headers["White"] = "?"
        game.headers["Black"] = "?"
        game.headers["Annotator"] = "ChessPrep (Stockfish + Maia-2)"
        root = game
        for u in pre:
            root = root.add_main_variation(chess.Move.from_uci(u))
        _add_comment(root, f"Pozycja wyszukiwania - grasz {'białymi' if color == 'white' else 'czarnymi'}, "
                           f"przeciwnicy {params['elo_min']}-{params['elo_max']}.")
        for i in g.get("items", []):
            if 0 <= i < len(items):
                it = dict(items[i])
                if notes and notes.get(i):
                    it["note"] = notes[i]
                add(root, it, color, i + 1)
        exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        out.append({"name": name, "pgn": game.accept(exporter)})
    return out


# ------------------------------------------------------------------ Lichess

def _call(method: str, path: str, token: str, **kw) -> requests.Response:
    headers = {**HEADERS}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    fut = _HTTP_POOL.submit(requests.request, method, API + path, headers=headers, timeout=(5, 30), **kw)
    try:
        return fut.result(timeout=45)
    except FutureTimeout:
        raise StudyError("Lichess nie odpowiada.")
    except requests.RequestException as exc:
        raise StudyError(f"Błąd sieci: {exc}")


def _err(r: requests.Response) -> str:
    try:
        return r.json().get("error") or r.text[:200]
    except ValueError:
        return r.text[:200]


def token_info(token: str) -> dict:
    """{"valid", "user", "scopes": [...], "can_write"}."""
    if not token:
        return {"valid": False, "user": None, "scopes": [], "can_write": False}
    r = _call("POST", "/api/token/test", "", data=token.encode())
    if r.status_code != 200:
        raise StudyError(f"Lichess HTTP {r.status_code}: {_err(r)}")
    info = r.json().get(token)
    if not info:
        return {"valid": False, "user": None, "scopes": [], "can_write": False}
    scopes = [s for s in (info.get("scopes") or "").split(",") if s]
    return {"valid": True, "user": info.get("userId"), "scopes": scopes, "can_write": "study:write" in scopes}


def parse_study_id(text: str) -> str | None:
    text = (text or "").strip()
    m = re.search(r"study/([A-Za-z0-9]{8})", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9]{8}", text) else None


def create_study(token: str, name: str, visibility: str = "private") -> str:
    r = _call("POST", "/api/study", token, data={
        "name": name[:100] if len(name) >= 2 else "ChessPrep", "visibility": visibility,
        "computer": "everyone", "explorer": "everyone", "cloneable": "owner",
        "shareable": "owner", "chat": "member", "sticky": "false"})
    if r.status_code != 200:
        raise StudyError(f"Nie udało się utworzyć studium ({r.status_code}): {_err(r)}")
    return r.json()["id"]


def _chapter_ids(token: str, study: str) -> list[str]:
    r = _call("GET", f"/api/study/{study}.pgn", token, params={"comments": "false", "variations": "false"})
    if r.status_code != 200:
        return []
    return re.findall(r'\[ChapterURL "[^"]*/study/[A-Za-z0-9]{8}/([A-Za-z0-9]{8})"\]', r.text)


def export_to_lichess(token: str, chapters: list[dict], orientation: str,
                      study: str | None = None, new_name: str | None = None,
                      visibility: str = "private") -> dict:
    if not token:
        raise StudyError("Brak tokenu Lichess.")
    if not chapters:
        raise StudyError("Nic do eksportu.")
    created = False
    empty: list[str] = []
    if study:
        study_id = parse_study_id(study)
        if not study_id:
            raise StudyError("Nie rozpoznano adresu studium (np. https://lichess.org/study/AbCd1234).")
    else:
        study_id = create_study(token, new_name or chapters[0]["name"], visibility)
        created = True
        empty = _chapter_ids(token, study_id)  # pusty rozdział tworzony razem ze studium
    done, errors = 0, []
    for ch in chapters:
        r = _call("POST", f"/api/study/{study_id}/import-pgn", token, data={
            "pgn": ch["pgn"], "name": ch["name"][:100], "orientation": orientation, "variant": "standard"})
        if r.status_code == 200:
            done += 1
            if r.json().get("error"):
                errors.append(r.json()["error"])
        elif r.status_code == 429:
            errors.append("Limit zapytań Lichess - spróbuj za minutę.")
            break
        else:
            errors.append(f"{ch['name']}: {_err(r)}")
            if r.status_code in (401, 403, 404):
                break
    if created and done and len(empty) == 1:
        _call("DELETE", f"/api/study/{study_id}/{empty[0]}", token)
    return {"study": study_id, "url": f"{API}/study/{study_id}", "chapters": done,
            "errors": errors, "created": created}
