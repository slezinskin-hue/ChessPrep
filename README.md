# ChessPrep

**Przygotowanie debiutowe nastawione na praktykę.** Ustawiasz pozycję na szachownicy, wybierasz
kolor i zakres rankingu przeciwników, a ChessPrep szuka wariantów, które są **poprawne według
silnika**, a jednocześnie **niewygodne dla człowieka**: pułapek, w które ludzie z tego rankingu
naprawdę wpadają, linii z kilkoma trudnymi ruchami do znalezienia oraz rzadko granych nowinek.

> *In English:* ChessPrep is a Windows desktop app for practical opening preparation. From any
> position it searches for lines that stay objectively sound (Stockfish, with your own evaluation
> limit) but set problems for human opponents of a chosen rating: traps people really fall into
> (Maia-2 human-move model + your PGN database + Lichess explorer), lines with several hard,
> non-obvious only-moves, and rarely played novelties. Results can be exported to a Lichess study.
> The UI is in Polish.

---

## Spis treści
- [Co potrafi](#co-potrafi)
- [Jak to działa](#jak-to-działa)
- [Instalacja (Windows)](#instalacja-windows)
- [Pierwsze uruchomienie](#pierwsze-uruchomienie)
- [Praca z programem](#praca-z-programem)
- [Ustawienia wyszukiwania](#ustawienia-wyszukiwania)
- [Eksport do studium Lichess](#eksport-do-studium-lichess)
- [Pliki i dane](#pliki-i-dane)
- [Dokumentacja techniczna](#dokumentacja-techniczna)
- [Licencja i podziękowania](#licencja-i-podziękowania)

## Co potrafi

| | |
|---|---|
| **Pułapki** | Pozycje, w których przeciwnik często gra naturalny, ale zły ruch. Przy każdym błędzie widać, ilu graczy go zagra, ocenę po nim i **karę**, czyli linię silnika, która go wykorzystuje. Ranking: oczekiwana strata przeciwnika na partię (dojście × siła), z ograniczeniem liczby wyników z jednej rodziny wariantów. |
| **Miary praktyczne** | **Zysk** (o ile rośnie Twój oczekiwany wynik dzięki ludzkim błędom), **na 100 partii** (zysk × szansa dojścia), **koszt** (ile oddajesz swoimi ruchami względem najlepszych), **ukrycie** (od jakiej głębokości silnik widzi błąd). |
| **Trudne linie** | Warianty, w których przeciwnik musi kilka razy z rzędu znaleźć trudny, nieoczywisty ruch. Odbicia, wymuszone ucieczki z szacha i ruchy „same się grające” nie liczą się jako trudne. |
| **Nowinki** | Twoje ruchy rzadko grane (lub nigdy, **TN**) w pozycjach będących teorią, najwyżej 0,5 piona gorsze od najlepszego ruchu silnika. |
| **Prawdziwe partie** | Dla każdej pułapki: ilu graczy z Twojego rankingu naprawdę w nią wpadło (baza lokalna, Lichess, Masters) i jaki był potem Twój wynik, z 95% przedziałem ufności. |
| **Przeciwnik** | Wczytujesz partie konkretnego gracza (Lichess, chess.com albo z lokalnej bazy po nazwisku). W jego pozycjach liczą się jego własne ruchy, a przy pułapkach widać, ile razy już do nich dochodził i czy wpadał. |
| **Długie szukania** | Tryb „do godziny” (np. całą noc do 15:00), zapis wyników co 15 minut, podgląd wyników częściowych, „Zakończ i zweryfikuj”, kolejka kolejnych szukań. |
| **Rodziny wariantów** | Wyniki o wspólnym początku są zgrupowane i można je zwijać. „Szukaj głębiej” uruchamia nowe wyszukiwanie od wybranej rodziny. |
| **Repertuar** | Gwiazdki ★ i notatki przy wynikach, filtry (★ moje, zweryfikowane, ukryte, TN). |
| **Eksport** | Do studium Lichess, z osobnym rozdziałem dla każdej rodziny, komentarzami, `?`/`!` i karą za błędy. Także PGN do schowka (ChessBase itp.). |
| **Wyniki ↔ szachownica** | Gdy przesuniesz figury do pozycji, która jest w wynikach, lista sama przewija się do tej karty i ją zaznacza. |

## Jak to działa

| Składnik | Rola |
|---|---|
| **Stockfish** (lub inny silnik UCI) | Obiektywna ocena. Twoje ruchy w wariancie nie schodzą poniżej ustawionego progu (np. białymi ≥ −0,5, czarnymi ≤ +1,0). Najlepsze wyniki są na końcu sprawdzane na większej głębokości. |
| **Maia-2 / Maia-3** | Sieci neuronowe (CSSLab, Uniwersytet w Toronto), które przewidują, jaki ruch zagra **człowiek** o danym rankingu. To z nich bierze się „naturalność” ruchu. Maia-3 zna rankingi 600–2600 i jest dokładniejsza; instaluje się ją osobno (niżej). W pozycjach bez partii przewidywany odsetek błędów jest kalibrowany na prawdziwych partiach, bo model go przeszacowuje. |
| **Twoja baza PGN** | Zindeksowana według pozycji i przedziałów rankingu: ile partii, jakie ruchy, jakie wyniki. Indeksowanie 17 mln partii trwa ok. 2 godzin i robi się w tle. |
| **Explorer Lichess** | Statystyki z partii graczy Lichess (z filtrem rankingu i tempa) i/lub z bazy Masters. |

Program buduje drzewo wariantów:
* **na Twoim ruchu** bierze ruchy silnika mieszczące się w progu oceny. W pozycjach teoretycznych
  bierze ich więcej, żeby znaleźć rzadkie, ale dobre boczne warianty (nowinki),
* **na ruchu przeciwnika** bierze ruchy, które ludzie z danego rankingu faktycznie grają (Maia-2
  zmieszana ze statystykami partii), plus najlepsze ruchy silnika. Wszystkie ocenia jednym
  przeszukaniem MultiPV, więc oceny są porównywalne.

Za **błąd** uznaje ruch, po którym szansa wygranej spada o co najmniej 10%, jak „mistake” na Lichess.
Najpierw rozwija pozycje, do których przeciwnik dochodzi najczęściej, *zakładając, że broni się
dobrze*. Dzięki temu po trudnym momencie bada linię dalej i znajduje serie trudnych ruchów.
Pułapka jest **ukryta**, gdy nawet silnik na głębokości 6 wybrałby zły ruch; człowiek takie
odparcie łatwiej przeoczy (por. jk_182,
[„Finding Traps with Stockfish”](https://lichess.org/@/jk_182/blog/finding-traps-with-stockfish/mCX3kCmf)).

## Instalacja (Windows)

Potrzebny jest **Python 3.11+**. Program pobierzesz ze strony
[Releases](https://github.com/slezinskin-hue/ChessPrep/releases) (Source code .zip) albo przez `git clone`.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Instalacja pobiera m.in. PyTorch (CPU, ok. 200 MB), z którego korzysta Maia-2.

### Maia-3 (opcjonalnie, zalecane)

Maia-3 (2026) zna rankingi 600–2600 (Maia-2 tylko do 2000), uwzględnia ranking obu graczy i kilka
poprzednich pozycji, i trafniej przewiduje ludzkie ruchy. Nie ma jej na PyPI – instaluje się ją z
repozytorium autorów:

```powershell
.venv\Scripts\python -m pip install git+https://github.com/CSSLab/maia3.git
```

Po instalacji (i restarcie programu) ustawienie „Model ludzkich ruchów: automatycznie” samo jej użyje
(gdyby nie dało się jej załadować, program sam wróci do Mai-2 i pokaże błąd w pasku stanu);
wagi (~20 MB dla modelu 5M) pobiorą się przy pierwszym użyciu do `models/maia3/`. Kod Mai-3 jest na licencji
AGPL-3.0. Maia-2 słabo przewiduje pierwsze ~10 ruchów partii – tam program i tak opiera się głównie na
statystykach partii.

## Pierwsze uruchomienie

```powershell
.venv\Scripts\python run.py
```

Możesz też dwukrotnie kliknąć `ChessPrep.bat`. Jeśli program nie znajdzie silnika, sam otworzy
**⚙ Ustawienia**:

1. **Silnik**: „Wybierz silnik…” i wskaż plik `.exe`. Pasuje dowolny silnik UCI, np. Stockfish ze
   <https://stockfishchess.org/download/>. Bez wyboru program użyje pierwszego `stockfish*.exe`
   z folderu `stockfish/`.
2. **Baza partii** (opcjonalnie): „Wybierz bazę (PGN)…”. Indeks buduje się sam w tle, z paskiem
   postępu; można to przerwać i później wznowić. Indeksy wcześniej używanych baz zostają zachowane.
3. **Token Lichess** (opcjonalny, darmowy): <https://lichess.org/account/oauth/token>. Do samych
   statystyk nie trzeba żadnych uprawnień. Do eksportu studiów potrzebne są uprawnienia
   `study:read` i `study:write`; okno eksportu podaje gotowy link do takiego tokenu.
4. **Model Maia-2** pobiera się sam przy pierwszym użyciu.

`run.py --server` uruchamia sam serwer, który otworzysz w przeglądarce pod <http://127.0.0.1:8777>.

## Praca z programem

1. **Ustaw pozycję**: przeciągaj figury, wklej PGN/FEN albo kliknij ruch w tabeli „Ruchy ludzi”.
   „Nowa” wraca do pozycji wyjściowej.
2. **Ustaw szukanie**: kolor, najgorszą akceptowaną ocenę, ranking przeciwników, głębokość
   wariantu i to, jak długo szukać: **określoną liczbę pozycji** albo **do godziny** (program sam
   zostawi czas na weryfikację). Potem kliknij **Szukaj**. W trakcie:
   * **Pokaż wyniki częściowe** – wyniki zapisywane co 15 minut (bez weryfikacji),
   * **Zakończ i zweryfikuj** – kończy szukanie teraz, ale sprawdza najlepsze wyniki,
   * **Przerwij** – od razu, bez weryfikacji,
   * **Dodaj do kolejki** – kolejne szukanie ruszy, gdy skończy się bieżące (w kolejności dodania;
     ✕ przy pozycji w kolejce ją usuwa).
   Opcjonalnie w sekcji **Przeciwnik** wczytaj partie gracza i wybierz go w „Szukaj pod przeciwnika”.
3. **Przeglądaj wyniki** w zakładkach *Pułapki*, *Trudne linie*, *Nowinki* i *Drzewo*:
   * kliknięcie karty ustawia pozycję na szachownicy ze strzałkami (czerwone = błędy przeciwnika,
     zielone = dobre odpowiedzi),
   * **kara** (albo „jak ukarać?”, jeśli silnik ma ją dopiero policzyć) ustawia pozycję po błędzie;
     dalsze ruchy przechodzisz strzałką →,
   * **Kolejność**: *rodzinami* (domyślnie) albo *od najlepszych*; rodziny można zwijać,
   * **szukaj głębiej** przy rodzinie uruchamia nowe wyszukiwanie od jej pozycji, z tymi samymi
     ustawieniami,
   * **☆ / ✎** to gwiazdka do repertuaru i notatka. **Pokaż** filtruje listę.
4. Wyszukiwania zapisują się same. Wracasz do nich z listy w nagłówku „Wyniki”, a zamykasz je
   przyciskiem **✕ Zamknij**.

## Ustawienia wyszukiwania

| Ustawienie | Domyślnie | Znaczenie |
|---|---|---|
| Najgorsza akceptowana ocena | −0,5 (białe) / +1,0 (czarne) | Twoje ruchy w wariancie nie mogą zejść poniżej (dla czarnych: powyżej) tej oceny. |
| Ranking przeciwników | 1600–2000 | Dla kogo liczymy „naturalność” (Maia-2 + statystyki). |
| Głębokość wariantu | 12 półruchów | Jak daleko od pozycji startowej sięga drzewo. |
| Liczba analizowanych pozycji | 120 | Budżet wyszukiwania; więcej = dłużej i dokładniej. |
| Szukaj | liczbę pozycji | **Określoną liczbę pozycji** albo **do godziny** (np. 15:00). |
| Głębokość silnika / weryfikacji | 16 / 21 | Przy szukaniu / przy sprawdzaniu najlepszych wyników. |
| Głębokość dla mało prawdopodobnych gałęzi | 14 | Gałęzie z szansą dojścia poniżej 1% liczone płycej (do tej głębokości przy 0,1%). Równa głębokości silnika = wyłączone. |
| Min. szansa dojścia pułapki | 0,2% | Pułapki z mniejszą szansą dojścia idą na koniec listy („rzadkie dojście”). |
| Wyników z jednej rodziny / maks. udział rodziny w budżecie | 4 / 50% | Różnorodność: jedna linia nie zajmie całej listy ani całego czasu szukania. |
| Maks. strata vs najlepszy ruch | 1,2 piona | Jak bardzo Twój ruch może ustępować najlepszemu. |
| Moje kandydaty / odpowiedzi przeciwnika | 4 / 4 | Szerokość drzewa. |
| Nowinki: strata / „nowinka” / „rzadki wariant” / „teoria” | 0,5 piona / ≤ 10 partii / ≤ 2% partii / ≥ 50 partii | Nowinka (N, TN = 0 partii) ma mało partii; „rzadki wariant” ma mały udział w partiach z tej pozycji, nawet przy dużej bazie. Ranking nowinek bierze pod uwagę, czy przeciwnik zaraz potem się myli i czy wychodzi poza teorię. |
| Trudne momenty: min. % błędów / oczywisty ruch | 25% / Maia ≥ 50% | Kiedy moment jest trudny, a kiedy dobry ruch jest oczywisty. |
| Baza Lichess | gracze | *gracze* (z filtrem rankingu i tempa), *Masters* albo *obie*. |

Wskazówka: przy statystykach graczy Lichess prawie każdy ruch był już gdzieś grany. Nowinek lepiej
szukać z bazą *Masters* albo z własną bazą.

## Eksport do studium Lichess

Przycisk **Lichess** w nagłówku „Wyniki” otwiera okno, w którym wybierasz:
* **co** wysłać: pułapki, trudne linie lub nowinki, wszystkie albo tylko ★, zweryfikowane itd.,
* **rozdziały**: osobny dla każdej rodziny wariantów (Lichess przyjmie najwyżej 64) albo jeden,
* **studium**: nowe (prywatne, przez link lub publiczne) albo istniejące (wklejasz adres).

Każdy rozdział zaczyna się od ruchów, które prowadziły do pozycji wyszukiwania. Pułapki mają
komentarz z odsetkiem błędów, a błędy przeciwnika są oznaczone `?` razem z karą. Dobra obrona ma
znak `!` i idzie główną linią, nowinki mają `N` i `!?`, a Twoje notatki trafiają do komentarzy.
„Kopiuj PGN” daje te same rozdziały jako PGN do schowka.

## Pliki i dane

Wszystko, co lokalne, jest w `.gitignore`:

| Ścieżka | Zawartość |
|---|---|
| `config.local.json` | Twoje ustawienia: silnik, baza, token Lichess (wzór: `config.example.json`). |
| `data/indexes/` | Indeksy baz PGN (osobny dla każdej bazy). |
| `data/evals/` | Pamięć ocen silnika (osobna dla każdego silnika); ponowne szukanie w tej samej okolicy jest dużo szybsze. |
| `data/searches/` | Zapisane wyszukiwania (JSON). |
| `data/marks.json` | Gwiazdki i notatki. |
| `data/calibration.json` | Krzywa kalibracji przewidywanego odsetka błędów (uczy się z każdego szukania). |
| `data/opponents/` | Wczytane partie przeciwników (ich „książki”). |
| `models/maia3/` | Wagi Mai-3 (jeśli zainstalowana). |
| `data/lichess.sqlite` | Pamięć odpowiedzi explorera Lichess (30 dni). |
| `models/maia2/` | Wagi Maia-2. |

Indeks z wiersza poleceń (opcjonalnie):

```powershell
.venv\Scripts\python -m chessprep.dbindex build --pgn games\MojaBaza.pgn
.venv\Scripts\python -m chessprep.dbindex query e4 c5 Nf3 --elo-min 1600 --elo-max 2000
```

## Dokumentacja techniczna

Budowa programu, algorytm, formaty danych i API są opisane w
[docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md), a historia zmian w [CHANGELOG.md](CHANGELOG.md).

## Licencja i podziękowania

Kod na licencji **GPL-3.0** (zob. [LICENSE](LICENSE)).

* [Stockfish](https://stockfishchess.org/), GPL-3.0, pobierany osobno.
* [Maia-2](https://github.com/CSSLab/maia2) i [Maia-3](https://github.com/CSSLab/maia3) (CSSLab, Uniwersytet w Toronto): modele ruchów ludzkich.
* [Lichess](https://lichess.org/) za opening explorer i API studiów.
* Figury: zestaw „cburnett” (Colin M.L. Burnett, GPLv2+), ten sam co domyślny na Lichess.
* Pomysł ukrytych pułapek: jk_182, „Finding Traps with Stockfish”.
