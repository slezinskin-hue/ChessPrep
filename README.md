# ChessPrep

Aplikacja do przygotowania debiutowego: od wybranej pozycji szuka **poprawnych**
kontynuacji dla Twojego koloru, które w przyszłości stawiają przeciwnika przed
**praktycznymi problemami**: jedynymi ruchami, ruchami nienaturalnymi i
pułapkami, w które ludzie z danego przedziału rankingowego często wpadają.
Najlepiej w wariantach, które rzadko pojawiają się w partiach.

## Jak to działa

| Składnik | Rola |
|---|---|
| **Stockfish** | Obiektywna ocena. Żaden Twój ruch w wariancie nie schodzi poniżej ustawionego progu (np. białymi ≥ −0,5, czarnymi ≤ +1,0). Najlepsze warianty są na końcu weryfikowane na większej głębokości. |
| **Maia-2** | Sieć neuronowa (CSSLab, Uniwersytet w Toronto) przewidująca, jaki ruch zagra **człowiek** o danym rankingu. Na niej opiera się „naturalność” ruchu. |
| **Lokalna baza PGN** | Twoja baza partii, zindeksowana według pozycji i przedziałów rankingu: ile partii, jakie ruchy, jakie wyniki. |
| **Explorer Lichess** | Statystyki partii online dla wybranego zakresu rankingu i tempa. |

Szukanie buduje drzewo wariantów:

* **na Twoim ruchu**: ruchy silnika mieszczące się w progu oceny,
* **na ruchu przeciwnika**: ruchy, które ludzie z zakresu rankingu faktycznie grają
  (Maia-2 zmieszana ze statystykami), plus zawsze najlepszy ruch silnika.

Najpierw rozwijane są pozycje, do których przeciwnik najczęściej dochodzi. Dla
każdej pozycji przeciwnika liczona jest **oczekiwana strata**: suma po ruchach
`p(ruch) × spadek szansy wygranej`, czyli ile przeciwnik średnio „oddaje”,
grając jak typowy człowiek. Wynikiem jest lista pułapek z:

* szansą dojścia do pozycji (ruchy przeciwnika po drodze są prawdopodobne),
* szansą błędu (jak często zagra ruch, który według silnika psuje mu pozycję),
* oznaczeniem *jedyny ruch*, gdy tylko jeden ruch trzyma pozycję,
* liczbą partii w bazie i na Lichess (rzadkość),
* pełnym drzewem z eksportem do PGN (np. do studium na Lichess albo do ChessBase).

## Instalacja (Windows)

Wymagany Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Zasoby, które **nie są w repozytorium** (są w `.gitignore`):

1. **Stockfish**: pobierz z <https://stockfishchess.org/download/> i rozpakuj do
   `stockfish/` (domyślna ścieżka to `stockfish/stockfish-windows-x86-64-universal.exe`;
   inną możesz ustawić w `config.local.json`).
2. **Baza partii PGN** (opcjonalna): domyślnie `games/TeraBaza.pgn`.
3. **Model Maia-2**: pobiera się sam przy pierwszym użyciu do `models/maia2/`.
4. **Token Lichess** (opcjonalny, darmowy): <https://lichess.org/account/oauth/token>,
   bez zaznaczania uprawnień.

Ustawienia lokalne trzymaj w `config.local.json` (wzór: `config.example.json`).
Token można też wpisać w aplikacji (⚙).

### Indeks bazy partii

Jednorazowo, przy ~17 mln partii trwa to około 2 godzin. Można przerwać i
wznowić tą samą komendą.

```powershell
.venv\Scripts\python -m chessprep.dbindex build --workers 3
```

Sprawdzenie indeksu:

```powershell
.venv\Scripts\python -m chessprep.dbindex query e4 c5 Nf3 --elo-min 1600 --elo-max 2000
```

## Uruchomienie

```powershell
.venv\Scripts\python run.py
```

Albo dwuklik w `ChessPrep.bat`. `run.py --server` uruchamia sam serwer, który
otworzysz w przeglądarce pod <http://127.0.0.1:8777>.

## Struktura

```
chessprep/
  config.py    konfiguracja (domyślne + config.local.json)
  dbindex.py   indeks bazy PGN (numpy, pliki mapowane w pamięci)
  engine.py    Stockfish przez UCI + pamięć ocen w SQLite
  human.py     Maia-2
  explorer.py  explorer Lichess + dostęp do lokalnego indeksu
  search.py    algorytm szukania pułapek, zadania w tle
  server.py    API (FastAPI)
web/           interfejs (szachownica, analiza, wyniki), bez zewnętrznych bibliotek JS
run.py         okno aplikacji (pywebview) lub sam serwer
data/          (lokalne) indeks, pamięć ocen, zapisane wyszukiwania
```

## Uwagi

* Maia-2 rozróżnia rankingi od <1100 do ≥2000. Powyżej 2000 „naturalność” coraz
  bardziej opiera się na statystykach partii.
* Oceny pokazywane są zawsze z perspektywy białych.
* Wyniki silnika są zapamiętywane (`data/evals.sqlite`), więc ponowne szukanie
  w tej samej okolicy jest dużo szybsze.
* Figury szachowe: zestaw „cburnett” (Colin M.L. Burnett, GPLv2+), ten sam co
  domyślny na Lichess (`web/pieces/`).
