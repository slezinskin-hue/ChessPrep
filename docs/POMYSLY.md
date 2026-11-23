# ChessPrep – pomysły na ulepszenia

*Wersja 2, 23.11.2026. Wersja 1 powstała w nocy; wersja 2 jest uzupełniona o wnioski z nocnego szukania
(sekcja 0).*
Nic z tego nie jest zaimplementowane. Każdy pomysł ma ocenę: **zysk** (jak bardzo poprawi wyniki) i
**praca** (S = godziny, M = dzień, L = kilka dni).

Podstawa: przegląd kodu (`search.py`), dane z wcześniejszych wyszukiwań, nocne szukanie na 2500 pozycji
oraz źródła z internetu (lista na końcu).

## Stan (23.11.2026, po wdrożeniu)

Zrobione: 1.1–1.6 (limity, weryfikacja złego ruchu, transpozycje, tryb do godziny + zapis częściowy +
kolejka, przedziały ufności), 2.1 (jako dodatkowa miara – stare wskaźniki zostały), 2.3, 2.4, 2.5 (wynik
trudnych linii), 2.6 / 0.4 (kalibracja), 0.1–0.3 (ranking, różnorodność, priorytet), 3.1, 3.2, 4.1
(konfigurowalne), 5 (Maia-3 jako opcja – wymaga instalacji), 6.1 (przeciwnik: Lichess, chess.com, baza),
6.6 (kolejka), 0.7 (błąd historii ruchów).
Nie robimy (decyzja): 2.2 (czy ja znajdę karę), 6.2 (import repertuaru), 6.3 (trening), 6.4 („co mi grozi”).
Do zrobienia: 3.3 (asymetria przygotowania), 3.4 (dwufazowe MultiPV), 6.5 (repertuar z wielu wyszukiwań).

## Najważniejsze w skrócie (po nocnym szukaniu)

1. **Ranking pułapek nagradza pułapki, do których nikt nie dojdzie.** W nocnym szukaniu połowa z 40
   pokazanych pułapek ma dojście ≈ 0% (mediana 0,09%), bo leżą 18–28 półruchów w głąb jednej ostrej linii.
   Trzeba mocniej liczyć dojście, wprowadzić próg minimalnego dojścia i ograniczyć liczbę wyników z jednej rodziny (0.1–0.3).
2. **Szukanie „nurkuje” w jedną forsowną linię.** Priorytet zakłada, że przeciwnik broni się dobrze, więc
   w liniach z wymuszonymi ruchami budżet idzie bardzo głęboko (setki pozycji na półruchach 20–28).
   27 z 40 pułapek ma wspólne pierwsze 6 półruchów (0.2).
3. **Maia przeszacowuje wysokie odsetki błędów.** Tam, gdzie da się to sprawdzić na partiach, przewidywane
   60–100% błędu to w rzeczywistości 42%, a przewidywane 30–60% to 33%. Najlepsze pułapki (Maia: 78–99%
   błędu) leżą w pozycjach bez partii, więc są najpewniej zawyżone. Potrzebna kalibracja albo Maia-3 (0.4, 2.6, 5).
4. **Limity ucięły 91% znalezisk**: w drzewie było 437 różnych pozycji-pułapek, pokazano 40. Nowinek w
   drzewie było 368, pokazano 30. Sprawdzonych na większej głębokości jest 16 z 40 pułapek (1.1).
5. **30% rozwiniętych pozycji to transpozycje** (752 z 2497), czyli prawie jedna trzecia budżetu (1.3).
6. **Weryfikować też zły ruch przeciwnika**, nie tylko moje ruchy (1.2), oraz wprowadzić **jedną miarę
   praktycznego zysku** (2.1) i ocenę, **czy ja znajdę karę** (2.2).
7. **Szybsze szukanie przez zmienną głębokość** (4.1) i **Maia-3** (5) – bez zmian względem wersji 1.

---

## 0. Wnioski z nocnego szukania (23.11, 2500 pozycji)

**Ustawienia:** Najdorf 6.Bg5 e6 7.f4, białe, ranking 1900–3300, głębokość 20 / weryfikacja 24,
głębokość wariantu **30** półruchów, maks. strata 0,5, gracze Lichess + baza lokalna.
**Czas:** 8 h 8 min (ok. 11,7 s na pozycję z weryfikacją; szybciej niż pomiar, bo część pozycji była w pamięci ocen).

**Wyniki w liczbach:**

| | |
|---|---|
| Pozycji w drzewie / rozwiniętych | 8106 / 2497 |
| Rozwinięte transpozycje (ta sama pozycja inną drogą) | 752 (30%) |
| Kandydaci na pułapki (unikalne pozycje) → pokazane | 437 → 40 |
| Pozycje z trudnym momentem (jedyny ruch) | 145 (31) |
| Nowinki w drzewie (TN) → pokazane | 368 (95) → 30 |
| Pułapki zweryfikowane / ukryte / jedyny ruch | 16 / **0** / 14 z 40 |
| Pułapki bez żadnej partii w bazach | 31 z 40 |
| Dojście pułapek: mediana / max | **0,09%** / 10,4% |
| Długość linii pułapek: mediana / max | 19 / 28 półruchów |

### 0.1 Ranking nagradza pułapki nie do osiągnięcia
* Wzór `dojście·siła + 0,15·siła`: przy dojściu ≈ 0 decyduje drugi człon, czyli sama siła. W głębokich,
  chaotycznych pozycjach (wszystko wisi, każdy ruch szachuje) siła jest zawsze wysoka, więc takie pozycje
  wygrywają. Przykład: pułapki #2–#4 i #7–#12 leżą po 18–28 półruchach linii 7…Be7 8.e5 dxe5 9.fxe5 Nd5…
  i mają dojście 0,000.
* **Pomysł:** ranking według oczekiwanego zysku (dojście × siła, zob. 2.1) bez członu „za samą siłę”,
  minimalne dojście (np. 0,5% albo ustawialne), osobna lista „ciekawe, ale mało prawdopodobne”.

### 0.2 Brak różnorodności i nurkowanie
* 27 z 40 pułapek zaczyna się tak samo (7…Be7 8.e5 dxe5 9.fxe5 Nd5 10.Bxe7), a 8 to Zatruty pionek. Inne
  pomysły (np. 8.Qf3 Qc7 9.O-O-O z dojściem 1–10%) wypadły poza limit.
* Przyczyna: priorytet rozwijania to dojście *warunkowe* (zakładamy dobrą obronę). W linii, gdzie każdy
  dobry ruch przeciwnika jest prawie wymuszony, warunkowe prawdopodobieństwo ≈ 1, więc budżet schodzi do
  półruchu 28 (prawie 700 rozwiniętych pozycji na półruchach 20–29).
* **Pomysł:** priorytet z mieszanki dojścia warunkowego i bezwarunkowego (np. średnia geometryczna) plus
  kara za głębokość; limit budżetu na rodzinę; w wynikach najwyżej N pułapek z jednej rodziny, zanim pojawią
  się kolejne z tej samej. Zalecenie na teraz: głębokość wariantu 16–20, nie 30.

### 0.3 Trudne linie się „nasycają”
* Wszystkie najlepsze trudne linie mają „przejdzie bez błędu 0%”, bo mają 4–7 momentów z dużym błędem.
  Liczy się wtedy tylko liczba momentów, więc wygrywają najdłuższe linie. Do tego linie się dublują
  (3 z 8 najlepszych różnią się ostatnim ruchem).
* **Pomysł:** wynik = Σ dojście_k · błąd_k (oczekiwana liczba błędów, ważona szansą dojścia do momentu k),
  co samo ogranicza znaczenie późnych momentów. Do tego deduplikacja po wspólnym początku.

### 0.4 Kalibracja Mai (pomiar na 219 pozycjach przeciwnika z ≥ 100 partiami)
| Maia przewiduje błąd | Pozycji | Faktycznie w partiach (średnio) | Maia (średnio) |
|---|---|---|---|
| 0–10% | 95 | 3,4% | 2,4% |
| 10–30% | 33 | 15,9% | 18,1% |
| 30–60% | 82 | 32,7% | 40,5% |
| 60–100% | 9 | 41,9% | 71,4% |

* Najczęstszy ruch według Mai zgadza się z najczęstszym ruchem w partiach w 151 z 219 pozycji (69%).
* Przeszacowanie rośnie z głębokością: +2 pp na półruchach 4–11, +7 pp na półruchach 12–19.
* Część winy to obcięcie rankingu: Maia-2 zna rankingi do 2000, a ustawiony zakres to 1900–3300.
* **Pomysł:** krzywa kalibracji (np. izotoniczna) dopasowywana automatycznie na pozycjach z partiami i
  stosowana tam, gdzie partii nie ma; ostrzeżenie „przewidywanie bez partii” przy pułapkach z Maią > 60%;
  docelowo Maia-3.

### 0.5 Żadna pułapka nie jest „ukryta”
* 0 z 40: silnik już na głębokości 6 widzi karę. To głównie pozostawione bez obrony figury i taktyka na
  1–2 ruchy. Gracze 1900+ takie rzeczy zwykle widzą, co zgadza się z przeszacowaniem Mai z 0.4.
* **Pomysł:** stopień ukrycia z 2.4 jako czynnik w rankingu (im płycej widać karę, tym bardziej obniżyć
  przewidywany błąd przy wyższych rankingach); filtr „tylko ukryte” jest już w interfejsie.

### 0.6 Nowinki
* 368 w drzewie, z tego 95 TN; pokazano 30, zweryfikowano 10. Najciekawsze: 11.Nd1 w Zatrutym pionku
  (0 partii na 6913, strata 0,26) i 10.a3 / 10.h4 po 8.Qf3 Qc7 9.O-O-O Nbd7 10.Bxf6 Bxf6 (0–3 partii na
  1130, błąd przeciwnika zaraz potem 57–61%).
* Część nowinek ma błąd przeciwnika 0% zaraz potem (np. 9.Rg1, 9.Rf1), czyli są rzadkie, ale niczego nie
  dają. To przemawia za oceną nowinki po tym, co dzieje się dalej (3.2).

### 0.7 Błąd zgłoszony rano: kliknięcie nowinki prowadzi do pozycji po 5. ruchu
* Ruchy od początku partii do pozycji wyszukiwania okno trzyma tylko w swojej pamięci (`localStorage`,
  okno pywebview działa w trybie prywatnym, więc po restarcie aplikacji znikają). Program nie sprawdza,
  czy te ruchy naprawdę prowadzą do pozycji wyszukiwania. Jeśli nie (u Ciebie kończą się prawdopodobnie na
  5…a6), każda linia urywa się w tym miejscu, bo kolejny ruch jest nielegalny.
* Sprawdzone: z poprawną historią (1.e4 … 7.f4) i bez historii nowinki i pułapki ustawiają się dobrze.
* **Poprawka (praca: S):** zapisywać historię razem z wynikami wyszukiwania (w pliku na serwerze), przy
  odtwarzaniu sprawdzać, czy kończy się na pozycji wyszukiwania, a jeśli nie, pomijać ją. Przy okazji
  eksport do studium dostanie poprawny początek także po restarcie aplikacji.

---

---

## 1. Szybkie poprawki

### 1.1 Limity wyników i weryfikacji — zysk: duży, praca: S
* Teraz: eksport 40 pułapek / 30 linii / 30 nowinek; weryfikacja na większej głębokości tylko 10 / 8 / 10;
  liczby partii („rzadkość”) liczone tylko dla 25 pułapek.
* Dane: w ostatnich szukaniach zweryfikowanych było 20/40, 24/40 i 10/25 pułapek.
* Pomysł: limity zależne od budżetu (np. pułapki = budżet/10, min. 40), weryfikacja najlepszych ~30–40%
  i dopiero potem odcięcie. Na kartach niezweryfikowane wyraźnie oznaczyć (już jest znaczek ✓).

### 1.2 Weryfikacja złego ruchu, nie tylko moich — zysk: duży, praca: S
* Teraz `verify_paths` sprawdza tylko, czy *moje* ruchy trzymają próg oceny. To, że ruch przeciwnika jest
  błędem, wynika z porównania na głębokości szukania (np. 18–22) i nikt tego później nie sprawdza.
* Pomysł: dla zweryfikowanej pułapki jeszcze raz porównać dobre i złe ruchy (MultiPV z ograniczeniem do
  nich) na głębokości weryfikacji. Jeśli „błąd” przestał być błędem, pułapka odpada albo dostaje
  oznaczenie „wątpliwa”.

### 1.3 Budżet, transpozycje i pamięć ocen — zysk: średni, praca: S
* W drzewie z 400 pozycji 200 z 1121 węzłów (18%) to ta sama pozycja osiągnięta inną kolejnością ruchów.
  Silnik ma je w pamięci, ale każda i tak zjada jednostkę budżetu oraz zapytania do Mai i Lichess.
* Pomysł: tablica transpozycji (EPD → węzeł) i liczenie do budżetu tylko faktycznej pracy silnika.
  Wyniki z różnych dróg do tej samej pozycji łączyć: dojście to suma po drogach.

### 1.4 Szukanie „do godziny” i zapis w trakcie — zysk: duży przy nocnych szukaniach, praca: S–M
* Dziś trzeba zgadywać, ile pozycji zdąży się policzyć. Wyniki zapisują się dopiero na końcu, więc
  zamknięcie okna albo awaria w nocy oznacza utratę wszystkiego. „Przerwij” pomija weryfikację.
* Pomysł: tryb „licz do 15:00”, w którym program sam zostawia czas na weryfikację. Do tego zapis
  częściowych wyników co ~15 min, możliwość wznowienia i weryfikacja także po „Przerwij” (jako opcja).

### 1.5 Wiarygodność liczb „w partiach” — zysk: mały–średni, praca: S
* „Wpadło 1 z 5 (20%)” wygląda tak samo pewnie jak „1856 z 4391 (42%)”.
* Pomysł: przedział ufności (Wilsona), np. „20% (4–62%)”, i wyszarzanie wyników z małej próby.
  WickedLines (LKama) robi to samo przez p-value < 0,05. Uwaga z bloga D2D4C2C4: surowe statystyki
  explorera zawyżają różnice wyników między ruchami (różnice rankingów, kto co gra).

### 1.6 Bezpieczniejsze mieszanie „gracze + Masters” — zysk: mały, praca: S
* Opcja „obie” sumuje partie Masters i graczy Lichess, a to zupełnie inne populacje.
* Pomysł: osobne źródła w modelu (Masters jako sygnał „czy teoria to zna”, gracze jako „co ludzie grają”).

---

## 2. Lepsze pułapki

### 2.1 Jedna miara: oczekiwany wynik przeciw człowiekowi — zysk: bardzo duży, praca: M
* Teraz ranking to `dojście·siła + 0,15·siła`, a wartość drzewa to suma „siły” po drodze. Brakuje tu
  kosztu: ile oddaję, gdy przeciwnik zagra dobrze.
* Pomysł (expectimax):
  * na ruchu przeciwnika: wartość = Σ p(ruch) · wartość(pozycji po ruchu),
  * na moim ruchu: wartość = najlepsze dziecko,
  * w liściach: oczekiwany wynik z oceny silnika (WDL) albo z głowicy wartości Mai-3 (wynik partii *ludzi*).
* Zysk praktyczny ruchu = jego wartość minus wartość najlepszego ruchu silnika. Wtedy pułapka z kosztem
  −0,3 piona, w którą wpada 40% ludzi, porządnie przegra z pułapką bez kosztu, w którą wpada 30%. Dziś te
  rzeczy są mieszane ręcznie dobranymi wagami.
* Do pokazania jako zrozumiała liczba, np. „+3,1 pkt na 100 partii” albo „≈ +25 Elo na 100 partii”
  (WickedLines: dojście × ΔEV × ~8 Elo).
* Źródła: chessenginelab (Maia/Leela z WDL dają oczekiwany wynik ludzi z błędem ~3% vs Lichess),
  Jacob i in. (wyszukiwanie z ludzkim modelem), Allie („Human-aligned chess with a bit of search”).

### 2.2 Czy ja znajdę karę? — zysk: duży, praca: S–M
* Teraz pokazujemy karę, ale nie wiemy, czy jest łatwa. Jeśli wymaga cichego ruchu, który dostrzega 5%
  graczy mojego rankingu, pułapka w praktyce nie działa (chyba że ją zapamiętam).
* Pomysł: Maia na *moim* rankingu ocenia ruchy kary. Łatwość kary = iloczyn prawdopodobieństw moich ruchów
  w linii kary, albo prościej: pierwszego ruchu. Oznaczenia: „kara oczywista” / „kara do zapamiętania”.
  To samo jest potrzebne dla „ruchów wymuszonych” w trudnych liniach: czy ja trzymam przewagę łatwo.
* Ustawienie: „mój ranking” (osobno od rankingu przeciwników).

### 2.3 Koszt pułapki widoczny na karcie — zysk: średni, praca: S
* Karta pokazuje, ile traci przeciwnik, ale nie pokazuje, ile ja ryzykuję: jaka jest ocena po dobrej obronie
  w porównaniu z oceną najlepszego ruchu silnika kilka ruchów wcześniej.
* Pomysł: „koszt: −0,25 względem najlepszej gry” obok „siły”.

### 2.4 Stopień ukrycia zamiast „ukryta tak/nie” — zysk: średni, praca: M
* Teraz pułapka jest ukryta, jeśli na głębokości 6 silnik też wybrałby zły ruch.
* Pomysł (Biswas–Regan): głębokość, na której silnik pierwszy raz widzi, że ruch jest zły, czyli 4 / 8 / 12 /
  16. Im głębiej, tym trudniej człowiekowi. Można to liczyć tanio, tylko dla zweryfikowanych pułapek.

### 2.5 Trudne linie przez „ostrość” i „łatwość wariantu” — zysk: średni, praca: M
* jk_182 (11.2026, 3000 pozycji z olimpiady): najlepszym predyktorem błędów ludzi jest „odwrócona łatwość”
  (inverted ease), Σ p(ruchu)·(wynik najlepszego − wynik ruchu). To prawie dokładnie nasza „siła”, tylko
  liczona z polityki Lc0 zamiast z Mai, czyli nasze podejście jest dobre.
* Słabość tej miary: pozycja z jednym legalnym ruchem ma łatwość 1, nawet gdy cała linia jest piekielnie
  trudna. Dlatego do trudnych linii warto dodać miarę *pozycji*: ostrość z WDL
  (s = (2 / (ln(1/W−1) + ln(1/L−1)))², z Lc0) albo „WDL sensitivity”. Do tego „łatwość wariantu”
  (matstc): najniższa łatwość na kilku kolejnych ruchach, bo przeciwnik może celowo prowadzić w trudne
  pozycje.
* Stockfish też podaje WDL (już je zbieramy), ale jego model WDL jest dopasowany do gry silników. Lepsza
  byłaby głowica wartości Mai-3.

### 2.6 Kalibracja Mai na prawdziwych partiach — zysk: średni, praca: M
* W pozycjach z dużą liczbą partii można porównać przewidywany odsetek błędu (Maia) z prawdziwym
  (explorer). Z tego wyjdzie, ile Maia myli się w danym rankingu i typie pozycji, i można automatycznie
  dobrać `PRIOR_GAMES` (dziś na sztywno 25) albo poprawić przewidywania.
* Dane z ostatniego szukania: 45–55% pułapek ma 0 partii w bazach, więc tam liczy się tylko Maia i jej
  kalibracja jest kluczowa.

---

## 3. Lepsze nowinki

### 3.1 Rzadkość względna i osobno dla każdego źródła — zysk: duży, praca: S
* Teraz nowinka to ≤ 10 partii łącznie. Przy graczach Lichess prawie nic nie jest rzadkie (miliony partii),
  a przy małej bazie wszystko jest rzadkie.
* Pomysł: udział ruchu w pozycji (np. ≤ 1–2% partii z tej pozycji) i osobna ocena źródeł: „Masters: 0,
  gracze 1600–2000: 0,4%” znaczy co innego niż „Masters: 40, gracze: 0”.

### 3.2 Wartość nowinki = co dzieje się dalej — zysk: duży, praca: M
* Dziś nowinka jest dobra, jeśli jest rzadka i bliska najlepszemu ruchowi. Liczy się jednak to, czy
  przeciwnik zaraz popełni błąd albo musi sam myśleć w nieznanej pozycji.
* Pomysł: ocena nowinki = oczekiwany zysk praktyczny w 2–3 kolejnych ruchach (miara z 2.1) plus premia
  „przeciwnik poza teorią” (0 partii po nowince, Maia niepewna, wysoka ostrość).

### 3.3 Asymetria przygotowania — zysk: średni, praca: S
* WickedLines: Schliemann gra 9,5% czarnych, którzy chcą go grać, ale biały spotyka go w 0,43% partii,
  więc jest dużo słabiej przygotowany.
* Pomysł: dla linii pokazać „jak często dostanę to na szachownicę, jeśli chcę” (już jest jako dojście) obok
  „jak często przeciwnik widzi tę pozycję” (udział w jego wszystkich partiach z tego debiutu). Duża różnica
  oznacza przewagę przygotowania.

### 3.4 Tańsze szukanie nowinek — zysk: średni (czas), praca: S
* MultiPV 8 na głębokości 22 jest drogie (każda linia więcej to mniej mocy na linię).
* Pomysł (ChessMonitor): faza 1, szybki przegląd wszystkich rzadkich ruchów na głębokości ~14; faza 2,
  głęboko tylko te, które w fazie 1 mieszczą się w progu.

---

## 4. Szybsze szukanie

### 4.1 Głębokość zależna od ważności pozycji — zysk: bardzo duży, praca: M
* Teraz każda pozycja liczy się na tej samej głębokości (np. 22, ~14 s). Gałąź, do której przeciwnik
  dochodzi w 0,1% partii, kosztuje tyle samo co główna linia.
* Pomysł: głębokość z dojścia, np. 14–16 dla rzadkich gałęzi, 20–22 dla ważnych, a na końcu pogłębienie
  wszystkiego, co trafia do wyników. Głębokość 16 to ~2 s zamiast ~14 s, więc przy tym samym czasie wyjdzie
  kilka razy więcej pozycji.
* Ryzyko: płytka ocena może przegapić pułapkę. Weryfikacja i tak ją potem sprawdzi.

### 4.2 Transpozycje (zob. 1.3) — zysk: ~15–20% budżetu, praca: S

---

## 5. Model człowieka: Maia-3

* Wydana w maju 2026 (CSSLab, Toronto; artykuł „Chessformer”, ICLR 2026). Obejmuje rankingi **600–2600**,
  bierze pod uwagę **ranking obu graczy** i **historię 7 poprzednich pozycji**.
* Trafność ruchu ludzi: 57,1% (model 79M), 55,4% (5M) wobec 52,0% Mai-2.
* Ma **głowicę wartości**: przewiduje wynik partii ludzi (W/D/L). To brakujący element do miary z 2.1.
* Na Twój laptop pasuje model **5M** (~276 mln operacji na pozycję, Maia-2 ma ~23M parametrów).
* Uwagi: trenowana na blitzu Lichess (2023–2025), więc dla partii klasycznych może przeszacowywać błędy.
  Licencja kodu to AGPL-3.0 (zgodna z naszym GPL-3.0, ale warto ją zaznaczyć). Interfejs to UCI
  (`maia3-uci`), bez gotowego API Pythona. Instalacja z gita, nie z PyPI.
* Najpierw trzeba zmierzyć szybkość na CPU i porównać przewidywania z Maią-2 na Twoich pozycjach.

---

## 6. Nowe funkcje

### 6.1 Przygotowanie pod konkretnego przeciwnika — zysk: bardzo duży, praca: M–L
* Pobranie partii przeciwnika z Lichess (`/api/games/user/{nick}`, NDJSON) albo z chess.com; powstaje jego
  drzewo debiutowe.
* Szukanie od pozycji, do których on faktycznie dochodzi, z jego własnymi częstościami ruchów (gdzie jest
  ich dość) zamiast średniej dla rankingu. Pokazanie pułapek, w które już wpadał.
* Podobne narzędzia: OpeningTree, Chessboard Magic „Opponent Prep”, ChessBase 18/26.

### 6.2 Mój repertuar — zysk: duży, praca: M
* Import mojego repertuaru (PGN / studium Lichess) i szukanie pułapek tylko w liniach, które gram.
* Wykrywanie luk, czyli częstych ruchów przeciwnika (Maia + statystyki), na które nie mam odpowiedzi
  (pomysł HollowLeaf).

### 6.3 Tryb treningu — zysk: duży, praca: L
* Maia gra za przeciwnika z mojego rankingu (raz wpada w pułapkę, raz broni się dobrze); ja odtwarzam
  swoje ruchy i karę. Pomyłki wracają częściej (powtórki w odstępach, jak Chessbook / Chessable).

### 6.4 „Co mi grozi” — zysk: średni, praca: M
* To samo szukanie z odwróconymi rolami: gdzie w moich liniach **ja** łatwo popełniam błąd.

### 6.5 Repertuar z wielu wyszukiwań — zysk: średni, praca: M
* Łączenie zapisanych wyszukiwań w jedno drzewo (z transpozycjami), z gwiazdkami jako „mój repertuar”.

### 6.6 Kolejka na noc — zysk: średni, praca: S–M
* Kilka pozycji do przeliczenia po kolei (np. 3 warianty po 4 h), razem z trybem „do godziny” z 1.4.

---

## 7. Do sprawdzenia po nocnym szukaniu (2500 pozycji, Najdorf 6.Bg5 e6 7.f4, białe)

1. Ile pułapek, linii i nowinek się zmieściło, i czy limity 40 / 30 / 30 coś ucięły.
2. Ile wyników zostało zweryfikowanych i ile weryfikacja odrzuciła; ile trwała weryfikacja.
3. Średni czas na pozycję na początku i na końcu (cache, transpozycje).
4. Rozkład dojścia i długości linii przy głębokości wariantu 18: czy budżet poszedł w głąb, czy wszerz.
5. Ile pułapek ma partie w bazach, i porównanie przewidywanego błędu (Maia) z prawdziwym.
6. Czy w wynikach są pułapki bardzo podobne do siebie (transpozycje, ta sama idea w kilku miejscach).
7. Czy pułapki są „grywalne” dla mnie: koszt, łatwość kary (ręczny przegląd kilku najlepszych).

---

## Źródła
* jk_182, „Different Ways to Measure the Complexity of Chess Positions” (lichess.org, 12.11.2026) i
  „Evaluating Sharpness using LC0's WDL” (04.2024).
* matstc, „A Chess Metric: Ease (for Humans)”; Noobmasterplayer123, „Extended Ease Metric (2): Variation Ease” (lichess.org).
* LKama, „Finding Opening Gold: A Statistical Hunt for Wicked Lines” (narzędzie WickedLines, MIT).
* ChessMonitor, „Engine-Powered Opening Explorer” (dwufazowe MultiPV).
* chessenginelab, „Expected Scores for Chess Positions” (Maia/Leela WDL vs wyniki Lichess, błąd ~3%).
* Monroe i in., „Chessformer: A Unified Architecture for Chess Modeling” (arXiv 2605.19091, Maia-3);
  github.com/CSSLab/maia3; Tang i in., „Maia-2” (NeurIPS 2024).
* Jacob i in., „Modeling Strong and Human-Like Gameplay with KL-Regularized Search” (ICML 2022);
  „Human-aligned Chess with a Bit of Search” (arXiv 2410.03893).
* Chessbook / Chessboard Magic (HollowLeaf): repertuar, luki, przygotowanie pod przeciwnika, powtórki.
