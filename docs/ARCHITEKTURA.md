# ChessPrep – dokumentacja techniczna

Opis budowy programu dla osoby, która będzie go rozwijać: moduły, przepływ danych, algorytm,
formaty plików, API i interfejs. Opis działania dla użytkownika jest w [README](../README.md).

## Spis treści
1. [Przegląd](#1-przegląd)
2. [Moduły Pythona](#2-moduły-pythona)
3. [Algorytm wyszukiwania](#3-algorytm-wyszukiwania)
4. [Format wyników](#4-format-wyników)
5. [API HTTP](#5-api-http)
6. [Interfejs (web/)](#6-interfejs-web)
7. [Indeks bazy PGN](#7-indeks-bazy-pgn)
8. [Lichess](#8-lichess)
9. [Dane lokalne](#9-dane-lokalne)
10. [Testowanie i wydania](#10-testowanie-i-wydania)
11. [Znane ograniczenia i pomysły](#11-znane-ograniczenia-i-pomysły)

---

## 1. Przegląd

```
run.py ──► okno pywebview ──► http://127.0.0.1:8777 (uvicorn + FastAPI, chessprep/server.py)
                                   │
         web/ (HTML + czysty JS) ◄─┤  pliki statyczne (no-cache)
                                   │
                                   ├─ /api/search  ──► search.JobManager ──► Searcher (wątek w tle)
                                   │                        │
                                   │      engine.Engine (Stockfish UCI + SQLite cache)
                                   │      human.Maia (Maia-2, torch CPU)
                                   │      explorer.Stats ──► dbindex.GameIndex (memmap)
                                   │                    └──► LichessExplorer (HTTP + SQLite cache)
                                   │
                                   ├─ /api/search/{id}/lichess_study ──► study.py ──► lichess.org API
                                   └─ /api/database ──► databases.IndexManager ──► subprocess dbindex build
```

* Jeden proces Pythona z serwerem FastAPI. Okno to pywebview (Edge WebView2), interfejs to
  zwykła strona w `web/` bez frameworków i bez budowania.
* Jedno wyszukiwanie naraz (jeden silnik). Wyszukiwanie działa w wątku, a interfejs co 1,5 s
  pyta o postęp.
* Wszystkie ciężkie zasoby (silnik, Maia, indeks, explorer) są współdzielone: `shared()` w
  każdym module.
* Ścieżki i ustawienia: `config.py`. Wartości domyślne są nadpisywane przez `config.local.json`
  (gitignore). Zapis: `config.save_local({...})`.

## 2. Moduły Pythona

| Plik | Odpowiada za |
|---|---|
| `__init__.py` | `__version__`, wstrzyknięcie `truststore` (systemowe certyfikaty, np. gdy antywirus skanuje HTTPS). |
| `config.py` | `CONFIG`, `ROOT`, `DATA_DIR`, `WEB_DIR`, `load()`, `save_local()`. |
| `engine.py` | `Engine`: UCI przez python-chess, `analyse(board, depth, multipv, moves=None)` zwraca listę `Line` (cp z perspektywy **białych**, WDL, pv). Pamięć ocen w `data/evals/<hash silnika>.sqlite`, klucz: EPD + ograniczenie ruchów (`root`); zapis z większą głębokością lub MultiPV obsłuży też płytsze zapytania. `resolve_engine_path()`, `probe()`, `set_engine()`. Bez okna konsoli (`CREATE_NO_WINDOW`). |
| `human.py` | `Maia.probs(board, elo_min, elo_max)` daje rozkład ruchów człowieka. Średnia z ≤ 4 punktów Elo w zakresie (Maia-2 zna 1100–2000), wsadowo, z cache LRU. Wagi pobierają się do `models/maia2`. |
| `dbindex.py` | Indeks bazy PGN (pkt 7): budowa (`build`, CLI) i odczyt (`GameIndex.query`). |
| `databases.py` | Wybór aktywnej bazy, katalog indeksu dla pliku, `IndexManager`: budowa w podprocesie z logiem, postęp z parsowania logu, przerwanie (`taskkill /T`), wznawianie. |
| `explorer.py` | `LichessExplorer` (players/masters, token Bearer, cache SQLite 30 dni, twardy timeout 15 s, pauza 60 s po 429 lub zawieszeniu) i `Stats` (lokalny indeks + Lichess, scalanie źródeł). |
| `search.py` | Algorytm (pkt 3), `SearchParams`, `Node`, `HumanPolicy`, `Searcher`, `JobManager` (wątki, zapis do `data/searches/<id>.json`), `refutation()`. |
| `study.py` | Budowa rozdziałów PGN z wyników (`build_chapters`) i eksport do studium Lichess (`export_to_lichess`, `token_info`). |
| `server.py` | Endpointy (pkt 5), eksport całego drzewa do PGN (`_tree_to_pgn`), gwiazdki/notatki, statystyki „w partiach”. |

## 3. Algorytm wyszukiwania

Plik `search.py`; opis jest też w docstringu modułu.

### Miary
* **Szansa wygranej** z centypionów, krzywa Lichess: `1 / (1 + exp(-0.00368208·cp))`.
* **loss(m)**: ile szansy wygranej przeciwnik oddaje ruchem *m* względem najlepszego ruchu.
  `loss ≥ MISTAKE (0.10)` oznacza błąd.
* **error**: masa prawdopodobieństwa (wg `HumanPolicy`) ruchów-błędów. Gdy drugi najlepszy ruch
  silnika jest już błędem, wszystkie nieocenione ruchy też są błędami i ich masa się dolicza.
* **trick**: oczekiwana strata przeciwnika, Σ p(m)·loss(m) po błędach.
* **reach**: iloczyn prawdopodobieństw ruchów przeciwnika od korzenia; nasze ruchy mają p = 1.

### HumanPolicy
`p(m) = (games(m) + 25·maia(m)) / (N + 25)`, gdzie `games` to suma partii z lokalnej bazy
i z Lichess w zakresie rankingu. Maia ma wagę 25 partii (`PRIOR_GAMES`): przy małej liczbie
partii decyduje Maia, przy dużej statystyki.

### Rozwijanie węzłów
* **Nasz ruch** (`_expand_mine`): MultiPV = `my_candidates`. W pozycjach teoretycznych
  (≥ `theory_min_games` partii) MultiPV = `novelty_multipv` (8), żeby wyłapać nowinki. Przechodzą
  ruchy z oceną ≥ `min_eval` i stratą ≤ `max_drop`. Nowinka: ≤ `novelty_max_games` partii,
  strata ≤ `novelty_max_loss`; TN, gdy 0 partii.
* **Ruch przeciwnika** (`_expand_opponent`): płytki przegląd (MultiPV 3, głębokość −4), żeby znać
  najlepsze ruchy silnika. Potem kandydaci = ruchy ludzi do pokrycia `opp_coverage` (+ top
  silnika) oceniani **jednym** przeszukaniem MultiPV z ograniczeniem `searchmoves`, dzięki czemu
  oceny są porównywalne. Dla błędów zapisujemy `pv` (karę, ≤ 9 półruchów, `refutation()`).
  Do drzewa idą dobre ruchy (najwyżej `opp_max_replies`) oraz najlepszy ruch.
* **Trudny moment** (`_classify_moment`): `error ≥ hard_error` i (jedyny dobry ruch **albo**
  `error ≥ max(0.5, hard_error)` przy kilku dobrych ruchach). Nie liczy się: jedyny legalny
  ruch, ucieczka z szacha przy ≤ 3 ruchach, odbicie na polu ostatniego ruchu, dobry ruch z
  Maia ≥ `obvious_p`. **Ukryta**: najlepszy ruch silnika na głębokości 6 jest błędem.

### Kolejność (best-first)
Kolejka priorytetowa po `prio`. Po ruchu przeciwnika `prio` mnożymy przez
**warunkowe** p (= p(m)/p(dobre)), czyli zakładamy, że przeciwnik broni się dobrze. Dzięki temu
po trudnym momencie linia jest rozwijana dalej i powstają linie z wieloma trudnymi ruchami. Koniec,
gdy budżet `budget` jest wyczerpany, gdy pozycja jest wygrana (≥ 300 cp) lub gdy dojdziemy do `max_plies`.

### Wyniki
* **Pułapki**: węzły przeciwnika z `error ≥ 0.08` i `trick ≥ 0.01`, sortowane po
  `reach·trick + 0.15·trick`, bez duplikatów pozycji (transpozycje); eksport 40 najlepszych.
* **Trudne linie**: ścieżki do węzłów z momentem; momenty = wszystkie momenty na ścieżce;
  wynik = `(0.3 + reach pierwszego) · (1 − Π(1 − error)) · premie`. Linie będące prefiksem
  bogatszej linii są usuwane.
* **Nowinki**: sortowane po popularności pozycji, bliskości do najlepszego ruchu i błędach dalej.
* **Weryfikacja**: najlepsze pułapki, linie i nowinki są sprawdzane na `verify_depth`. Gdy
  nasz ruch na ścieżce wypada poniżej progu, węzeł dostaje `verified = False` i znika.

## 4. Format wyników

`data/searches/<id>.json` (odpowiedź `GET /api/search/{id}` → `result`):

```jsonc
{
  "id": "d4d253b10b", "created": "2026-11-22 20:45",
  "params": { /* SearchParams */ }, "analysed": 400, "engine": "Stockfish",
  "traps": [{
    "fen": "...", "line": ["Bb6", "O-O"], "line_uci": ["c5b6", "e1g1"],   // od pozycji params.fen
    "reach": 0.27, "trick": 0.05, "error_chance": 0.42, "only_move": false, "hidden": false,
    "good": [{ "san": "d6", "uci": "d7d6", "p": 0.3, "cp": 20, "loss": 0.0, "maia": 0.2, "games": 812 }],
    "bad":  [{ "san": "Nxe4", "uci": "f6e4", "p": 0.4, "cp": 180, "loss": 0.2,
               "pv": { "uci": ["f6e4", "f1e1", "..."], "san": ["Nxe4", "Re1", "..."] } }],  // pv od 0.4
    "eval": 30, "verified": true, "local_games": 5, "lichess_games": 4391
  }],
  "hard_lines": [{ "line": [], "line_uci": [], "moments": [{ "type": "only|hard", "error": 0.6,
                   "good": [], "bad": [], "hidden": false, "fen": "...", "line_uci": [] }],
                   "survive": 0.2, "entry_reach": 0.3, "verified": true }],
  "novelties": [{ "line": [], "line_uci": [], "san": "a4", "parent_fen": "...", "games": 0, "tn": true,
                  "parent_games": 812, "loss": 20, "best_san": "...", "replies": [], "follow_trap": null }],
  "tree": { "children": [ /* węzły z moves_info, moment, novelty, eval, prob, ... */ ] }
}
```

* Wszystkie `cp` w wynikach są z perspektywy **naszego koloru** (`params.color`). Interfejs
  zamienia je na perspektywę białych (`myToWhite`).
* `line`/`line_uci` liczone są od pozycji wyszukiwania. Ruchy, które do niej prowadziły, interfejs
  trzyma w `localStorage["hist-<id>"]` (`startFen` + `moves`). Służą do odtwarzania linii na
  szachownicy i jako początek rozdziałów eksportu.
* Starsze wyszukiwania (sprzed 0.4) nie mają `pv`; karę liczy wtedy `POST /api/refute`.

## 5. API HTTP

| Metoda i ścieżka | Opis |
|---|---|
| `GET /api/position?fen=` | Legalne ruchy (`dests`), szach, koniec partii. |
| `POST /api/move` `{fen, uci|san}` | Wykonanie ruchu. |
| `POST /api/line` `{fen, moves[]}` | UCI → SAN + FEN po każdym ruchu. |
| `POST /api/parse_pgn` `{pgn}` | PGN albo FEN → pozycja startowa + ruchy. |
| `GET /api/analyse?fen=&depth=&multipv=` | Analiza silnika (karta „Silnik”). |
| `GET /api/stats?fen=&elo_min=&elo_max=&speeds=&source=` | „Ruchy ludzi”: baza lokalna, Lichess, Maia. |
| `POST /api/search` `{params}` | Start wyszukiwania (zwraca `id`). |
| `GET /api/search/{id}` | Postęp albo wynik (także zapisane). |
| `POST /api/search/{id}/cancel`, `DELETE /api/search/{id}` | Przerwanie, usunięcie (z gwiazdkami). |
| `GET /api/searches` | Zapisane i trwające wyszukiwania. |
| `GET /api/search/{id}/pgn` | Całe drzewo jako jeden PGN. |
| `POST /api/refute` `{fen, uci, depth}` | Kara za ruch (linia silnika od tego ruchu). |
| `GET /api/search/{id}/real` | Pułapki w prawdziwych partiach: dla każdej pułapki `{local, lichess}` z `total`, `fell`, `fell_pct`, `score_after_bad`, `score_overall` (wynik z naszej perspektywy). |
| `GET/POST /api/search/{id}/marks` | Gwiazdki i notatki: `{key: {star, note}}`, klucz `rodzaj:uci,uci,…`. |
| `POST /api/search/{id}/chapters_pgn` | Rozdziały eksportu jako PGN (tekst). |
| `POST /api/search/{id}/lichess_study` | Eksport do studium (nowego lub istniejącego). |
| `GET /api/lichess/token` | Ważność i uprawnienia tokenu, link do utworzenia tokenu ze `study:write`. |
| `GET /api/status`, `POST /api/settings` | Stan silnika, bazy, Maii, Lichess; zapis tokenu. |
| `POST /api/pick_file`, `/api/engine`, `/api/database[/build|/cancel|/clear]` | Ustawienia silnika i bazy. |

Ciało eksportu (`chapters_pgn`, `lichess_study`):
`{kind: "traps|hard|nov", groups: [{name, items: [indeksy]}], prefix: {start_fen, moves}, notes: {indeks: tekst}, study, new_name, visibility}`.
Podział na rozdziały robi interfejs (te same rodziny co na liście), a serwer buduje PGN.

## 6. Interfejs (web/)

* `index.html`: trzy kolumny (szachownica, analiza, wyszukiwanie z wynikami) i okna dialogowe:
  PGN/FEN, ustawienia, eksport, promocja.
* `board.js`: własna szachownica (przeciąganie, kliknięcia, strzałki, figury SVG z `pieces/`).
* `app.js`: cała logika. Najważniejsze miejsca:
  * `state`: pozycja (`startFen`, `moves`, `cursor`), `result`, `marks`, `real`, `job`, `arrows`.
  * `refresh()`: po każdej zmianie pozycji wywołuje `syncResults()`, które zaznacza i przewija
    karty pasujące do pozycji, rozwijając zwinięte rodziny.
  * `showResult(res)` → `renderResults()` → `renderTraps/Hard/Novelties/Tree`. Przy nowym
    wyniku dociąga gwiazdki (`loadMarks`) i statystyki „w partiach” (`loadReal`).
  * Rodziny: `familyTree(items, keep)` buduje drzewo prefiksów `line_uci` i zwija łańcuchy bez
    rozgałęzień. `renderFamilies()` rysuje nagłówki `<details class="fam">` (do 3 poziomów, tylko
    przy rzeczywistym rozgałęzieniu); kolejność wyznacza najlepszy wynik w rodzinie. Karta
    zachowuje `data-i` = indeks w wynikach serwera; numer `#n` to pozycja na liście.
  * Akcje na kartach: `bindActions(el, kind)` obsługuje elementy z `data-act`: `star`, `note`,
    `note-save`, `ref` (kara), `deeper` (szukaj głębiej). `bindCards()` ignoruje kliknięcia w
    `[data-act]` i `[data-sub]`.
  * `loadLine(res, ucis, arrows, upTo)` ustawia na szachownicy historię wyszukiwania + linię;
    `upTo` zatrzymuje kursor wcześniej (kara: pozycja po błędzie).
  * `applyParams(params)` to odwrotność `searchParams()`, używana przez „szukaj głębiej”.
* Preferencje interfejsu w `localStorage`: `res-order`, `res-filter-<rodzaj>`, `hist-<id>`.
* Serwer wysyła pliki interfejsu z `Cache-Control: no-cache`, więc po aktualizacji okno od razu
  używa nowych plików.

## 7. Indeks bazy PGN

`dbindex.py`, katalog `data/indexes/<nazwa>-<rozmiar>/` (pierwszy indeks: `data/index/`).

* Indeksowane jest pierwsze 30 półruchów każdej partii. Klucz pozycji to 64-bitowy hash
  (`KEY_METHOD = "tuplehash-v1"`, z bitboardów, strony na ruchu, roszad i bicia w przelocie).
* Wpis `info` (uint32): ruch (15 bitów), przedział Elo (6 bitów, co 100 punktów, średnia graczy)
  i wynik (2 bity); do tego `count`.
* Budowa: faza 1 to równoległe czytanie partii w kawałkach (`_fast_move`, ok. 10× szybsze niż
  pełne python-chess, sprawdzone na 2,3 mln ruchów) do `tmp/*.npy` (umożliwia wznowienie).
  Faza 2 to scalanie w 256 shardach do `keys.u64`, `info.u32`, `count.u32` (memmap).
  `meta.json` powstaje na końcu i oznacza gotowy indeks.
* Zapytanie: `searchsorted` po kluczu i filtr przedziału Elo; wynik to `MoveStat(uci, games, white, draws, black)`.
* Przykład: TeraBaza, 17,1 mln partii, 255 mln wpisów, ok. 2 h budowy (3 procesy).

## 8. Lichess

* **Explorer** (`explorer.lichess.org/lichess` i `/masters`): token Bearer (bez uprawnień),
  odstęp ≥ 0,25 s, cache 30 dni, twardy timeout 15 s w osobnym wątku (zawieszone połączenia
  przy skanowaniu HTTPS przez antywirus), pauza 60 s po 429 lub timeoucie.
* **Studia** (`study.py`, `lichess.org/api`): wymagają `study:write`.
  * `POST /api/study`: nowe studium (tworzy też pusty rozdział, który usuwamy po imporcie przez
    `DELETE /api/study/{id}/{chapterId}`; jego ID bierzemy z `GET /api/study/{id}.pgn`).
  * `POST /api/study/{id}/import-pgn`: po jednym żądaniu na rozdział (pole `name` działa
    tylko przy jednej partii w PGN); `orientation` = nasz kolor.
  * Limit: 64 rozdziały na studium, 30 nowych studiów dziennie.
  * `POST /api/token/test`: uprawnienia tokenu.
* Rozdział PGN: `_trap` / `_hard` / `_novelty` dopisują warianty od wspólnego korzenia
  (`_follow` używa istniejących gałęzi). Dobra obrona idzie główną linią (`!`), błędy są
  wariantami (`?`) z karą, nowinki mają NAG 146 (N) i `!?`.

## 9. Dane lokalne

| Ścieżka | Format |
|---|---|
| `config.local.json` | Ścieżka silnika, ścieżka bazy, token (nie trafia do gita). |
| `data/evals/<sha1(ścieżka\|rozmiar\|mtime)[:12]>.sqlite` | Pamięć ocen silnika. |
| `data/indexes/…`, `data/index/` | Indeksy baz (pkt 7) + `source.json`, `build.log`. |
| `data/lichess.sqlite` | Cache explorera (`url → body`). |
| `data/searches/<id>.json` | Wyniki (pkt 4). |
| `data/marks.json` | `{id wyszukiwania: {klucz: {star, note}}}`. |
| `models/maia2/` | Wagi Maia-2. |

## 10. Testowanie i wydania

* Interfejs najwygodniej sprawdzać przez `run.py --server` i przeglądarkę (port 8777). Panel
  podglądu w trakcie pracy używa portu 8778 (`.claude/launch.json` w folderze nadrzędnym).
* Szybkie testy: otwórz zapisane wyszukiwanie i sprawdź zakładki, rodziny, filtry, karę
  („jak ukarać?”), statystyki „w partiach” i eksport („Kopiuj PGN” działa bez Lichess).
* PGN eksportu można sprawdzić bez sieci: `study.build_chapters(result, "traps", groups, prefix)`
  i wczytanie przez `chess.pgn.read_game` (bez `errors`).
* Wydanie: podnieś `__version__` w `chessprep/__init__.py`, uzupełnij `CHANGELOG.md`, commit,
  tag `vX.Y.Z`, push, `gh release create`. Repozytorium: `slezinskin-hue/ChessPrep`, tożsamość
  gita lokalna dla repo (adres noreply GitHuba).

## 11. Znane ograniczenia i pomysły

* Przy statystykach graczy Lichess prawie każdy ruch był już grany, więc nowinek lepiej szukać
  z Masters lub z lokalną bazą.
* Maia-2 zna rankingi 1100–2000; poza tym zakresem więcej zależy od statystyk.
* Indeks lokalny obejmuje 30 półruchów; dalej statystyki są tylko z Lichess.
* Jedno wyszukiwanie naraz; analiza na żywo czeka na silnik w trakcie wyszukiwania.
* Pomysły na dalszy rozwój: tryb treningu (Maia gra za przeciwnika, powtórki błędów),
  odwrotny tryb „co mi grozi” (pułapki przeciwnika w moim repertuarze), łączenie wielu
  wyszukiwań w jeden repertuar z transpozycjami.
