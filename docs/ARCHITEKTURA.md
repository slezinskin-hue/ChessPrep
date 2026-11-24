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
* Jedno wyszukiwanie naraz (jeden silnik), kolejne czekają w kolejce. Wyszukiwanie działa w wątku, a interfejs co 1,5 s
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
| `engine.py` | `Engine`: UCI przez python-chess, `analyse(board, depth, multipv, moves=None)` zwraca listę `Line` (cp z perspektywy **białych**, WDL, pv). Pamięć ocen w `data/evals/<hash silnika>.sqlite`, klucz: EPD + ograniczenie ruchów (`root`); zapis z większą głębokością lub MultiPV obsłuży też płytsze zapytania. `exact=True`: wynik dokładnie z tej głębokości (czysta pamięć silnika, osobny zapis). `shallow()`: drugi, mały proces silnika do płytkich analiz. `resolve_engine_path()`, `probe()`, `set_engine()`. Bez okna konsoli (`CREATE_NO_WINDOW`). |
| `human.py` | Model ludzkich ruchów: `Maia` (Maia-2, rankingi 1100–2000) i `Maia3` (pakiet `maia3`, 600–2600, historia 7 pozycji, głowica W/D/L). `shared()` wybiera wg `human_model` („auto” = Maia-3, jeśli zainstalowana). Gdy zakres rankingu sięga ponad 2600 (dane treningowe Mai-3), Maia-3 dostaje punkty z górnych 200 (np. 2000–3300 → 2400/2500/2600) – lepiej przewiduje ruchy silnej populacji. Gdy Maia-3 nie da się załadować, `Maia3` przekazuje zapytania do Mai-2 (`fallback`), a błąd pokazuje w statusie. `probs(board, elo_min, elo_max, history)` – średnia z ≤ 4 punktów Elo w zakresie, cache LRU. |
| `calibration.py` | Krzywa „przewidywany odsetek błędów → faktyczny” (`Calibration.apply(pred, klasa)`): pomiar na bazie, dane z wyszukiwań albo krzywa startowa (pkt 3, „Krzywa kalibracji”); `data/calibration.json`, klucz `model|źródło|ranking`. |
| `calibrate_db.py` | Zadanie „Kalibruj model”: pomiar na losowych pozycjach z partii z lokalnej bazy (pkt 3). |
| `updates.py` | Sprawdzanie i instalowanie aktualizacji Mai-2 / Mai-3 (pkt 3). |
| `opponent.py` | Przeciwnik: pobieranie partii (Lichess `/api/games/user`, chess.com archiwa miesięczne, lokalny PGN po tagu White/Black), „książka” = pozycje, w których był na ruchu (`epd → [n, {uci: [ile, suma wyników]}]`), `Loader` w tle, `Book.dist/info`. |
| `dbindex.py` | Indeks bazy PGN (pkt 7): budowa (`build`, CLI) i odczyt (`GameIndex.query`). |
| `databases.py` | Wybór aktywnej bazy, katalog indeksu dla pliku, `IndexManager`: budowa w podprocesie z logiem, postęp z parsowania logu, przerwanie (`taskkill /T`), wznawianie. |
| `explorer.py` | `LichessExplorer` (players/masters, token Bearer, cache SQLite 30 dni, twardy timeout 15 s, pauza 60 s po 429 lub zawieszeniu) i `Stats` (lokalny indeks + Lichess, scalanie źródeł). |
| `search.py` | Algorytm (pkt 3), `SearchParams`, `Node`, `HumanPolicy`, `Searcher`, `JobManager` (kolejka, zapis częściowy, `data/searches/<id>.json`), `refutation()`, `validate_history()`. |
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
  error jest **kalibrowany** (`calibration.py`), ale tylko jego część z modelu:
  `error' = error − w·(err_model − apply(err_model, klasa))`, `w = 25/(N+25)` (waga modelu
  w `HumanPolicy`, więc przy wielu partiach poprawka znika sama), `err_model` = masa złych ruchów
  wg samego modelu (z resztą nieocenionych ruchów, gdy są błędami – ta sama definicja co przy
  zbieraniu danych). `klasa` = `shallow` (silnik widzi błąd do głębokości 6) / `deep` / brak.
  p złych ruchów skalujemy do `error'`, p dobrych odpowiednio w górę. `err_raw` = przed kalibracją.
* **Krzywa kalibracji** (`Calibration`, klucz `model|źródło|ranking`, np. `maia3-5m|local+masters|2000-3200`):
  1. pomiar na bazie (zadanie „Kalibruj model”, ≥ 200 pozycji, osobne krzywe dla klas) – tylko gdy
     szukanie porównuje z partiami klasycznymi (`local` sama albo z `masters`); ten sam przedział
     rankingu, a bez niego pomiar tego samego modelu o najbardziej pokrywającym się zakresie (≥ połowa);
  2. dane z wyszukiwań (pozycje z ≥ 30 partiami, każda pozycja raz – `seen` w pliku), ściągane do
     krzywej startowej z siłą 10 pozycji na przedział (`fit`); przedziały bez danych skalowane średnią
     z przedziałów ≥ 10% (gdy mają ≥ 20 pozycji), bo 0–10% to teoria;
  3. krzywa startowa: od `PRIOR` (Maia-2 / gracze Lichess) do `PRIOR_STRONG` (~1/3 przewidywania)
     płynnie wg rankingu 1800–2200; populacja online (gracze Lichess) liczy się w połowie.
  Bez pomiaru klasa `shallow` dostaje 0,6 × krzywa ogólna (`SHALLOW_FACTOR`). Krzywa jest
  niemalejąca i nigdy nie podnosi przewidywania. W trakcie szukania krzywa się nie zmienia; w
  `results()` (także zapis częściowy) `_recalibrate` dolicza dane z tego szukania i przelicza
  wszystkie pozycje przeciwnika ostateczną krzywą – bez silnika (oceny i p modelu są w węzłach,
  `cal_ver`). Klucze sprzed 0.6 (`model|ranking`) są przenoszone przy pierwszym odczycie.
* **trick** (na kartach „siła”): oczekiwana strata przeciwnika, Σ p(m)·loss(m) po błędach.
* **reach** (dojście): szansa dotarcia do pozycji przy naszej najlepszej strategii. Bez
  transpozycji to iloczyn p ruchów przeciwnika. Przy transpozycjach drogi różniące się kolejnością
  NASZYCH ruchów są alternatywami (zagramy jedną), a sumują się tylko drogi różniące się ruchami
  przeciwnika – w tej samej pozycji mogą się mieszać oba rodzaje, więc dla węzłów, które trafiają
  do wyników, `compute_reach` liczy dojście dokładnie (`_exact_reach`: programowanie dynamiczne po
  przodkach, na naszym ruchu max, na ruchu przeciwnika Σ p·wartość). W trakcie szukania (priorytet,
  głębokość silnika) przy transpozycji bierzemy maksimum po drogach – nigdy zawyżone.
  Do 0.5.0 drogi były sumowane, co zawyżało dojście średnio ~3× (przegląd 24.11.2026).
* **ev**: oczekiwany wynik (szansa wygranej z naszej perspektywy) – expectimax: na ruchu
  przeciwnika Σ p(m)·ev(dziecko) (dla nierozwiniętych – szansa z oceny), na naszym ruchu max.
  **gain** = ev − szansa przy najlepszej obronie (karta: „zysk”), **per100** = reach·gain·100,
  **line_net** (karta: „netto”) – dla PIERWSZEGO odejścia od najlepszego ruchu silnika na drodze:
  100·dojście(miejsca odejścia)·(ev po naszym ruchu − max(szansa przy najlepszym ruchu, ev po nim)).
  Jedno odejście „kupuje” wszystkie pułapki pod nim, więc to ocena linii, nie pojedynczej pułapki.
* **cost**: Σ po naszych ruchach na ścieżce (najlepszy ruch silnika − wybrany), w cp; obie oceny
  z tej samej analizy MultiPV (`edge_cp`), więc weryfikacja na większej głębokości ich nie miesza.
* **hidden_depth**: najmniejsza głębokość z (2, 4, …, 16), na której silnik widzi, że najczęstszy
  zły ruch jest błędem (porównanie z najlepszym dobrym ruchem, MultiPV 2). `hidden` = > 6.
  Liczona osobnym małym procesem silnika (`engine.shallow()`: 1 wątek, 16 MB) z `exact=True`:
  przed każdą analizą `ucinewgame` (czysta tablica transpozycji) i osobny zapis w pamięci ocen
  (`root = "@głębokość|…"`) – inaczej płytka analiza dostawałaby wyniki wcześniejszych głębokich.
  W trakcie szukania sprawdzana do głębokości 10 (`QUICK_HIDDEN`) dla każdej pozycji z `err_raw ≥ 8%`
  (`hidden_checked`; `hidden_depth = None` = nie widać do `hidden_max`), przy weryfikacji – do 16.
  **confidence** (karta): `games` (≥ 30 partii), `hidden`, `shallow` (sam model, a błąd widać
  od razu – silni gracze rzadko go popełniają), `model` (nie sprawdzano).

### HumanPolicy
`p(m) = (games(m) + 25·model(m)) / (N + 25)`, gdzie `games` to suma partii z lokalnej bazy
i z Lichess w zakresie rankingu. Model ma wagę 25 partii (`PRIOR_GAMES`). Gdy wczytano
przeciwnika, w jego pozycjach (na ruchu jest przeciwnik): `p = (1−λ)·p + λ·p_przeciwnika`,
`λ = n/(n+3)`, n = jego partie z tą pozycją. Uwaga: Maia-2 słabo przewiduje pierwsze ~10 ruchów
(tam decydują statystyki – te pozycje mają dużo partii).

### Rozwijanie węzłów
* **Nasz ruch** (`_expand_mine`): MultiPV = `my_candidates`. W pozycjach teoretycznych
  (≥ `theory_min_games` partii) MultiPV = `novelty_multipv` (8). Przechodzą ruchy z oceną
  ≥ `min_eval` i stratą ≤ `max_drop`. Nowinka: strata ≤ `novelty_max_loss` oraz ≤
  `novelty_max_games` partii (N; TN = 0) albo udział ≤ `novelty_max_share` („rzadki wariant”).
* **Ruch przeciwnika** (`_expand_opponent` → `_score_opponent`): płytki przegląd (MultiPV 3,
  głębokość −4), potem kandydaci = ruchy ludzi do pokrycia `opp_coverage` (+ top silnika)
  oceniani **jednym** MultiPV z `searchmoves`. Dla błędów zapisujemy `pv` (kara, ≤ 9 półruchów).
  Do drzewa idą dobre ruchy (najwyżej `opp_max_replies`) oraz najlepszy ruch. Pozycje z ≥ 30
  partiami zasilają kalibrację (przewidziany vs faktyczny odsetek złych ruchów).
* **Mniej zapytań o partie** (`_skip_sources`): gdy każda poprzednia pozycja w drzewie ma 0 partii na
  Lichess, tej nie pytamy (`lichess_skipped`; wyjątek – transpozycja spoza drzewa – dla wyników
  dopytuje `_fill_rarity`). Baza lokalna jest pytana zawsze (memmap, ułamek ms).
* **Głębokość silnika** (`node_depth`): `depth` przy dojściu ≥ 1%, `depth_min` przy ≤ 0,1%,
  pomiędzy – interpolacja logarytmiczna.
* **Transpozycje** (`_add_child`): tablica `(epd, ply) → węzeł`; ta sama pozycja po tej samej
  liczbie półruchów to jeden węzeł z wieloma rodzicami (`parents`, `edge_p`). Linia (`path`)
  biegnie przez pierwszego rodzica; w eksporcie drzewa pozostałe krawędzie to „transpozycja”.
  Weryfikacja usuwa złą pozycję ze wszystkich dróg (`_cut`); `_mark_alive` wyznacza węzły nadal
  osiągalne i – gdy pierwsza droga została odcięta – przepina linię węzła na inną żywą drogę
  (cel sprawdzany jest wtedy jeszcze raz, już nową drogą).
* **Trudny moment** (`_classify_moment`): `error ≥ hard_error` i (jedyny dobry ruch **albo**
  `error ≥ max(0.5, hard_error)` przy kilku dobrych ruchach). Nie liczy się: jedyny legalny
  ruch, ucieczka z szacha przy ≤ 3 ruchach, odbicie, dobry ruch z modelem ≥ `obvious_p`.

### Kolejność (best-first)
Priorytet = √(dojście warunkowe · dojście). Co 5 min (`QUEUE_REACH_EVERY`) nierozwinięte pozycje
z transpozycjami dostają dokładne dojście (`_refresh_queue`) – maksimum po drogach zaniża pozycje
osiągalne różnymi ruchami przeciwnika. Warunkowe zakłada dobrą obronę (p(m)/p(dobre)) –
dzięki temu po trudnym momencie linia jest rozwijana dalej; bezwarunkowe nie pozwala zejść
głęboko w linie, do których nikt nie dojdzie. Do tego limit `family_share` (domyślnie 50%)
budżetu na jedną rodzinę (pierwszy ruch + odpowiedź): po przekroczeniu węzeł wraca do kolejki
z połową priorytetu. Koniec: budżet `budget` (0 = bez limitu), albo w trybie **do godziny**
(`deadline`) chwila `deadline − rezerwa` (12% czasu, min. 10 min; przy krótkich 20%), albo
„Zakończ i zweryfikuj” (`job.finish`) / „Przerwij” (`job.cancel`, bez weryfikacji). Co 15 min
zapis wyników częściowych (`results(verify=False, rarity=False)` – bez zapytań do Lichess,
`partial: true`).

### Kolejka
`JobManager` ma jeden wątek roboczy i kolejkę FIFO: wyszukiwania ruszają w kolejności dodania.
Anulowanie czekającego wyszukiwania (`POST /api/search/{id}/cancel`, w oknie ✕ przy kolejce) od razu
wyjmuje je z kolejki. Wyszukiwanie „do godziny”, które wychodzi z kolejki, gdy do końca zostało
< 5 minut (`MIN_TIME_LEFT`), kończy się błędem z opisem zamiast pustym wynikiem.

### Wyniki
* **Pułapki**: węzły przeciwnika z `error ≥ 0.08`, `trick ≥ 0.01`, nieodrzucone przy weryfikacji;
  sortowane po `reach · trick · ukrycie` (ukrycie: 0,8–1,0 wg `hidden_depth`, 0,9 gdy nieznane).
  Pułapki z `reach < min_reach` na końcu (`rare`). Różnorodność (`_diversify`): najpierw najwyżej
  `family_quota` z jednej rodziny (wspólne 4 półruchy), potem reszta.
* **Trudne linie**: ścieżki do węzłów z momentem; wynik = Σ reach_k·error_k (×1,15 za jedyny ruch)
  · (1 + 0,1·(liczba momentów − 1)) = oczekiwana liczba błędów; bez linii będących prefiksem
  bogatszej i najwyżej 2 linie o tych samych dwóch pierwszych momentach na początku.
* **Nowinki**: `teoria · jakość · (0,15 + 5·follow) · rodzaj · poza_teorią`, gdzie follow =
  oczekiwana strata przeciwnika w 5 półruchach po nowince, rodzaj = 1,2 TN / 1,0 N / 0,6 rzadki
  wariant, poza_teorią = 1,15 gdy po nowince nie ma partii.
* **Limity**: pułapki `max(40, min(300, analysed/8))`, linie i nowinki `max(30, min(150, analysed/16))`.
* **Weryfikacja** (`results(verify=True)`): ok. 20% najlepszych (pułapki 10–60, linie 8–25,
  nowinki 10–25), **na przemian** pułapka → linia → nowinka według rankingu (gdy skończy się czas,
  każda grupa ma sprawdzone swoje najlepsze): nasze ruchy na ścieżce na `verify_depth` (spadek
  poniżej progu → gałąź odcięta), a w pułapkach i momentach `reverify_opponent`: dobre i złe ruchy
  jeszcze raz jednym MultiPV na `verify_depth` – gdy błąd przestał być błędem, węzeł jest
  `refuted`; potem głębokość ukrycia. W trybie do godziny weryfikacja kończy się najpóźniej 5 min
  po `deadline` (czas sprawdzany też w środku wyniku; przerwany wynik nie liczy się jako sprawdzony).
  `stats.verified`: liczby sprawdzonych, `planned`, `interrupted`, `cut` (pozycje odcięte po naszych
  ruchach). Przed weryfikacją `compute_reach(reparent=True)` ustawia linię każdego wyniku na
  kolejność naszych ruchów, której dotyczy dokładne dojście (`_follow_best`; bez węzłów z nowinką). `stats.budget_split`: pierwsze ruchy przeciwnika – udział w partiach
  i w przeanalizowanych pozycjach.

### Kalibracja na bazie (`calibrate_db.py`)
Zadanie w tej samej kolejce co wyszukiwania (`CalibParams`, `kind = "calibration"`). Losuje
partie z pliku PGN aktywnej bazy (losowe miejsce w pliku → następna partia), w nich półruch
16–60; gracz na ruchu musi mieć ranking z zakresu, pozycja < 30 partii w indeksie (poza teorią).
Wymaga gotowego indeksu. Dla pozycji: Maia (z 7 poprzednimi pozycjami), kandydaci jak w szukaniu
(ruchy modelu do `opp_coverage`, ≤ `opp_max_replies + 2`, + 3 najlepsze z płytkiego przeglądu na
`depth − 4`), MultiPV z kandydatami i zagranym ruchem na `depth` (14). Przewidywany błąd liczony
jak `err_model` w szukaniu (tylko kandydaci); faktyczny = czy zagrany ruch to błąd. Klasa ukrycia
jak w szukaniu (do głębokości 10).
Wynik dopisywany (sumowany z poprzednimi) do `calibration.json` → `measured` → `model|ranking`.

### Aktualizacje modeli (`updates.py`)
Okno przy starcie pyta `GET /api/updates`: commit zainstalowanej Mai-3 (`direct_url.json`) vs
GitHub (z datą i opisem commita), SHA-256 pobranych wag `.pt` vs Hugging Face (`?blobs=true`),
Maia-2 vs PyPI. Nic nie instaluje się samo: Maia-3 ładuje wagi z dysku (`local_files_only`).
`POST /api/updates` przyjmuje tylko identyfikatory z ostatniego sprawdzenia: wagi pobiera od razu
(`human.reset_shared()`), a dla paczek robi suchy przebieg pip (`--dry-run --report`, widać np.
zmianę torcha) i zapisuje `models/pending-update.json`. Instalacja (`run_pending`) odbywa się przy
następnym uruchomieniu w `run.py`, zanim załaduje się torch; Maia-3 z przypiętego commita. Wynik
trafia do `models/update-result.json` i okno pokazuje go raz. `DELETE /api/updates/pending` anuluje.

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
    "eval": 30, "verified": true, "local_games": 5, "lichess_games": 4391,
    // od 0.5:
    "error_raw": 0.55, "calibrated": true, "hidden_depth": 8, "rare": false, "reverified": true,
    "cost": 22, "ev": 0.61, "gain": 0.07, "per100": 1.9,
    "opponent": { "reached": 12, "fell": 3, "moves": [{ "san": "Nxe4", "n": 3, "score": 0.33, "bad": true }] }
  }],
  "hard_lines": [{ "line": [], "line_uci": [], "moments": [{ "type": "only|hard", "error": 0.6,
                   "good": [], "bad": [], "hidden": false, "fen": "...", "line_uci": [] }],
                   "survive": 0.2, "entry_reach": 0.3, "exp_errors": 0.12, "cost": 0, "verified": true }],
  "novelties": [{ "line": [], "line_uci": [], "san": "a4", "parent_fen": "...", "games": 0, "tn": true,
                  "parent_games": 812, "loss": 20, "best_san": "...", "replies": [], "follow_trap": null,
                  "share": 0.004, "rare_only": false, "follow": 0.03, "out_of_book": true }],
  "stats": { "nodes": 8106, "transpositions": 750, "verified": { "traps": 60, "lines": 25, "novelties": 25 },
             "calibration": [...], "elapsed": 29000 },
  "human_model": "Maia-3 (5M)", "opponent": { /* meta książki przeciwnika */ }, "partial": false,
  "tree": { "children": [ /* węzły z moves_info, moment, novelty, eval, prob, ... */ ] }
}
```

* Wszystkie `cp` w wynikach są z perspektywy **naszego koloru** (`params.color`). Interfejs
  zamienia je na perspektywę białych (`myToWhite`).
* `line`/`line_uci` liczone są od pozycji wyszukiwania. Ruchy, które do niej prowadziły, są w
  `params.pre_fen` + `params.pre_moves` (od 0.5; serwer sprawdza je przy starcie –
  `validate_history`; interfejs sprawdza jeszcze raz przy kliknięciu). Starsze wyszukiwania:
  `localStorage["hist-<id>"]`. Służą do odtwarzania linii na szachownicy, jako historia dla
  Mai-3 i jako początek rozdziałów eksportu.
* Starsze wyszukiwania (sprzed 0.4) nie mają `pv`; karę liczy wtedy `POST /api/refute`.

## 5. API HTTP

| Metoda i ścieżka | Opis |
|---|---|
| `GET /api/position?fen=` | Legalne ruchy (`dests`), szach, koniec partii. |
| `POST /api/move` `{fen, uci|san}` | Wykonanie ruchu. |
| `POST /api/line` `{fen, moves[]}` | UCI → SAN + FEN po każdym ruchu. |
| `POST /api/parse_pgn` `{pgn}` | PGN albo FEN → pozycja startowa + ruchy. |
| `GET /api/analyse?fen=&depth=&multipv=` | Analiza silnika (karta „Silnik”). |
| `GET /api/stats?fen=&elo_min=&elo_max=&speeds=&source=&hist_fen=&hist_moves=&opponent=` | „Ruchy ludzi”: baza lokalna, Lichess, model (z historią), ruchy przeciwnika. |
| `POST /api/search` `{params}` | Start wyszukiwania (zwraca `id`). |
| `GET /api/search/{id}[?partial=1]` | Postęp albo wynik (także zapisane); `partial=1` – wyniki częściowe trwającego szukania. |
| `POST /api/search/{id}/finish` | Zakończ szukanie, ale zweryfikuj wyniki. |
| `POST /api/search/{id}/cancel`, `DELETE /api/search/{id}` | Przerwanie bez weryfikacji, usunięcie (z gwiazdkami). |
| `GET /api/searches` | Zapisane i trwające wyszukiwania. |
| `GET /api/search/{id}/pgn` | Całe drzewo jako jeden PGN. |
| `POST /api/refute` `{fen, uci, depth}` | Kara za ruch (linia silnika od tego ruchu). |
| `GET /api/search/{id}/real` | Pułapki w prawdziwych partiach: dla każdej pułapki `{local, lichess}` z `total`, `fell`, `fell_pct`, `fell_ci`, `score_after_bad`, `score_ci`, `score_overall`, `small` (95% przedziały Wilsona; wynik z naszej perspektywy). |
| `POST /api/opponent/load` `{source, name, max_games}`, `GET /api/opponent/status`, `GET /api/opponents`, `DELETE /api/opponent/{key}` | Wczytywanie partii przeciwnika w tle i lista książek. |
| `GET/POST /api/search/{id}/marks` | Gwiazdki i notatki: `{key: {star, note}}`, klucz `rodzaj:uci,uci,…`. |
| `POST /api/search/{id}/chapters_pgn` | Rozdziały eksportu jako PGN (tekst). |
| `POST /api/search/{id}/lichess_study` | Eksport do studium (nowego lub istniejącego). |
| `GET /api/lichess/token` | Ważność i uprawnienia tokenu, link do utworzenia tokenu ze `study:write`. |
| `GET /api/status`, `POST /api/settings` | Stan silnika, bazy, modelu (`human_model`, `maia3_installed`), Lichess, kolejki; zapis tokenu i wyboru modelu. |
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
| `data/calibration.json` | `{"model|elo_min-elo_max": [[Σ przewidziany, Σ faktyczny, n] × 10 przedziałów]}`. |
| `data/opponents/<źródło>-<nick>.json` | `{meta, positions: {epd: [n, {uci: [ile, suma wyników]}]}}`. |
| `models/maia3/` | Cache Hugging Face z wagami Mai-3. |
| `models/maia2/` | Wagi Maia-2. |

## 10. Testowanie i wydania

* Interfejs najwygodniej sprawdzać przez `run.py --server` i przeglądarkę (port 8777). Panel
  podglądu w trakcie pracy używa portu 8778 (`.claude/launch.json` w folderze nadrzędnym).
* Testy regresji (bez silnika i Mai, z tymczasowym plikiem kalibracji):
  `.venv\Scripts\python tests\test_reach_calib.py` – dojście przy transpozycjach (też po odcięciu
  i w kolejce), pomijanie zapytań, `fit`, krzywa startowa, migracja, duplikaty, pomiar na bazie.
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
