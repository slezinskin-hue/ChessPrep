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

* **na Twoim ruchu**: ruchy silnika mieszczące się w progu oceny; w pozycjach
  „teoretycznych” (dużo partii) także rzadkie boczne warianty i nowinki,
* **na ruchu przeciwnika**: ruchy, które ludzie z zakresu rankingu faktycznie grają
  (Maia-2 zmieszana ze statystykami), plus najlepsze ruchy silnika. Wszystkie są
  oceniane jednym przeszukaniem MultiPV, więc oceny dają się porównać.

Najpierw rozwijane są pozycje, do których przeciwnik najczęściej dochodzi, przy
założeniu, że broni się dobrze. Dzięki temu po trudnym momencie linia jest
badana dalej. Wyniki są w trzech zakładkach:

### Pułapki
Pozycje, w których co najmniej 8% ludzi z zakresu gra błąd, czyli ruch
obniżający ich szansę wygranej o 10% lub więcej (jak „mistake” na Lichess).
Każda pułapka ma:
* **dojście**: szansę, że przeciwnik sam doprowadzi do tej pozycji,
* **błąd**: ilu ludzi się tu myli,
* **siłę**: ile średnio szansy wygranej przeciwnik oddaje,
* oznaczenie *jedyny ruch*,
* oznaczenie *ukryta*, gdy nawet silnik na głębokości 6 wybrałby zły ruch.
  Ruchy, które silnik odrzuca dopiero głębiej, człowiek łatwiej przeoczy (por. jk_182,
  [„Finding Traps with Stockfish”](https://lichess.org/@/jk_182/blog/finding-traps-with-stockfish/mCX3kCmf)).

### Trudne linie
Warianty, w których przeciwnik musi kilka razy znaleźć trudny ruch. Trudny moment to:
* **jedyny ruch**: tylko jeden ruch nie jest błędem, a myli się ≥ 25% ludzi,
* **trudny wybór**: jest kilka dobrych ruchów, ale myli się większość ludzi.

Dobry ruch nie może być oczywisty: odbiciem bierki, wymuszoną ucieczką z szacha
ani ruchem, który Maia uważa za naturalny (≥ 50%). Linia pokazuje przebieg przy
dobrej obronie przeciwnika oraz szansę, że typowy gracz przejdzie ją bez błędu.

### Nowinki
Twoje ruchy grane rzadko (domyślnie ≤ 10 partii) w pozycji, która jest teorią
(≥ 50 partii). Według silnika nowinka może być gorsza od najlepszego ruchu
najwyżej o 0,5 piona i musi mieścić się w progu oceny. Ruch bez żadnej partii
ma oznaczenie **TN**. Program pokazuje też, co przeciwnik zwykle odpowiada i
jaka pułapka czeka dalej.

Do tego pełne drzewo z eksportem do PGN: nowinki dostają znaczki `!?` i N,
trudne momenty są skomentowane, a dobre ruchy przeciwnika oznaczone `!`. Plik
można wczytać do studium na Lichess albo do ChessBase. Wszystkie progi da się
zmienić w „Zaawansowane”.

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
