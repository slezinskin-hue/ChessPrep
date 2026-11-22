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
| **Pułapki** | Pozycje, w których przeciwnik często gra naturalny, ale zły ruch. Przy każdym błędzie widać, ilu graczy go zagra, ocenę po nim i **karę**, czyli linię silnika, która go wykorzystuje. |
| **Trudne linie** | Warianty, w których przeciwnik musi kilka razy z rzędu znaleźć trudny, nieoczywisty ruch. Odbicia, wymuszone ucieczki z szacha i ruchy „same się grające” nie liczą się jako trudne. |
| **Nowinki** | Twoje ruchy rzadko grane (lub nigdy, **TN**) w pozycjach będących teorią, najwyżej 0,5 piona gorsze od najlepszego ruchu silnika. |
| **Prawdziwe partie** | Dla każdej pułapki: ilu graczy z Twojego rankingu naprawdę w nią wpadło (baza lokalna, Lichess, Masters) i jaki był potem Twój wynik. |
| **Rodziny wariantów** | Wyniki o wspólnym początku są zgrupowane i można je zwijać. „Szukaj głębiej” uruchamia nowe wyszukiwanie od wybranej rodziny. |
| **Repertuar** | Gwiazdki ★ i notatki przy wynikach, filtry (★ moje, zweryfikowane, ukryte, TN). |
| **Eksport** | Do studium Lichess, z osobnym rozdziałem dla każdej rodziny, komentarzami, `?`/`!` i karą za błędy. Także PGN do schowka (ChessBase itp.). |
| **Wyniki ↔ szachownica** | Gdy przesuniesz figury do pozycji, która jest w wynikach, lista sama przewija się do tej karty i ją zaznacza. |

## Jak to działa

| Składnik | Rola |
|---|---|
| **Stockfish** (lub inny silnik UCI) | Obiektywna ocena. Twoje ruchy w wariancie nie schodzą poniżej ustawionego progu (np. białymi ≥ −0,5, czarnymi ≤ +1,0). Najlepsze wyniki są na końcu sprawdzane na większej głębokości. |
| **Maia-2** | Sieć neuronowa (CSSLab, Uniwersytet w Toronto), która przewiduje, jaki ruch zagra **człowiek** o danym rankingu. To z niej bierze się „naturalność” ruchu. |
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
2. **Ustaw szukanie**: kolor, najgorszą akceptowaną ocenę, ranking przeciwników i głębokość
   wariantu, potem kliknij **Szukaj**. Pasek pokazuje postęp i przewidywany czas; wyszukiwanie
   można przerwać i zachować wyniki częściowe.
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
| Głębokość silnika / weryfikacji | 16 / 21 | Przy szukaniu / przy sprawdzaniu najlepszych wyników. |
| Maks. strata vs najlepszy ruch | 1,2 piona | Jak bardzo Twój ruch może ustępować najlepszemu. |
| Moje kandydaty / odpowiedzi przeciwnika | 4 / 4 | Szerokość drzewa. |
| Nowinki: strata / „rzadko” / „teoria” | 0,5 piona / ≤ 10 partii / ≥ 50 partii | Kiedy ruch jest nowinką. |
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
* [Maia-2](https://github.com/CSSLab/maia2) (CSSLab, Uniwersytet w Toronto): model ruchów ludzkich.
* [Lichess](https://lichess.org/) za opening explorer i API studiów.
* Figury: zestaw „cburnett” (Colin M.L. Burnett, GPLv2+), ten sam co domyślny na Lichess.
* Pomysł ukrytych pułapek: jk_182, „Finding Traps with Stockfish”.
