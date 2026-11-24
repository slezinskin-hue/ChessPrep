# Historia zmian

## [0.6.0] – 2026-11-24

Zmiany na podstawie dwóch przeglądów długiego szukania z Maią-3 (`docs/REVIEW-2026-11-24.md`,
`docs/REVIEW-2026-11-24-b.md`).

### Poprawki
- **Dojście przy transpozycjach** było zawyżone (średnio ~3×): drogi różniące się kolejnością
  Twoich ruchów były sumowane, choć zagrasz tylko jedną. Teraz dojście liczone jest dokładnie
  (Twoje alternatywy – maksimum, ruchy przeciwnika – suma). Zmienia ranking pułapek i „na 100
  partii”, a szukanie nie wciąga się już w gałęzie z wieloma kolejnościami ruchów.
- **Głębokość ukrycia i znacznik „ukryta”**: płytkie analizy dostawały z pamięci ocen wyniki
  głębokich, więc „ukryta” przy niezweryfikowanych pułapkach była zawsze fałszem. Teraz liczy je
  osobny mały proces silnika z czystą pamięcią.
- Nagłówek PGN drzewa podaje właściwy model (Maia-3 zamiast zawsze Maia-2).

### Mniej fałszywych pułapek
- Kalibracja odsetka błędów osobno dla źródła partii (gracze Lichess / Masters / baza lokalna),
  bez progu 30 partii (waga modelu robi to płynnie), ta sama definicja błędu przy zbieraniu danych
  i ich stosowaniu, każda pozycja liczy się raz (powtórki tego samego otwarcia nie przeważają).
  Na koniec szukania wszystkie pozycje są przeliczane jedną krzywą, z danymi z tego szukania.
  Dane z 0.5.0 są przenoszone.
- Ostrzejsza krzywa startowa dla silnych graczy (płynnie od rankingu 1800 do 2200, w pełni dla
  partii klasycznych) – modele przeszacowywały ich błędy kilkukrotnie. Błędy, które silnik widzi od
  razu, dostają dodatkowo ×0,6, dopóki nie ma pomiaru z „Kalibruj model”.
- **Kalibruj model** (⚙): pomiar na losowych pozycjach z partii z Twojej bazy, osobno dla błędów
  widocznych od razu i ukrytych. Działa też dla szukań o podobnym zakresie rankingu; kolejne
  kalibracje się sumują. Głębokość ukrycia liczona jest w trakcie szukania dla każdej pozycji
  z błędem (do głębokości 10), więc kalibracja wie, który to rodzaj.
- Maia-3 przy zakresie powyżej 2600 przesuwa dolny ranking w górę (2000–3300 → 2400–2600,
  1800–2700 → 1900–2600).
- Pozycje osiągalne różnymi ruchami przeciwnika dostają w kolejce szukania dokładne dojście.

### Szybciej, czytelniej
- Bez zapytań do Lichess o pozycje, których poprzedniczki nie mają tam partii (w długim szukaniu
  ~58% zapytań); dla wyników program i tak dopytuje.
- Weryfikacja na przemian pułapka – linia – nowinka: gdy skończy się czas, każda grupa ma
  sprawdzone swoje najlepsze wyniki. W podsumowaniu: ile sprawdzono z ilu, czy przerwano, ile
  pozycji odcięto, i jak budżet rozłożył się na pierwsze ruchy przeciwnika.
- Karty pułapek: „widać od razu (gł. N)” dla błędów z samego modelu, które silnik widzi płytko,
  „ukryta (gł. > N)”, filtr „pewne (z partii albo ukryte)” i **netto** linii: czy pierwsze odejście
  od najlepszego ruchu opłaca się razem ze wszystkimi pułapkami po nim.
- Panel „Ruchy ludzi” mówi, czy Maia-3 widzi historię ruchów (bez historii przewidywania są inne).
- Lista zapisanych wyszukiwań nie wczytuje za każdym razem wszystkich plików.

### Aktualizacje modeli
- Przy starcie program sprawdza nowe wersje Mai-3 (program i wagi) oraz Mai-2 i pyta, czy
  zaktualizować. Wagi pobierają się od razu, a paczki instalują przy następnym uruchomieniu
  programu (wcześniej widać, co się zmieni, np. torch). Wagi Mai-3 nie podmieniają się już same.
- Weryfikacja pilnuje czasu także w środku wyniku; panel „Ruchy ludzi” sprawdza historię ruchów.

## [0.5.0] – 2026-11-23

Zmiany na podstawie nocnego szukania na 2500 pozycji (wnioski: `docs/POMYSLY.md`, sekcja 0).

### Poprawki
- Kliknięcie wyniku ustawiało pozycję po kilku ruchach, gdy zapamiętana w oknie historia ruchów nie
  prowadziła do pozycji szukania. Historia zapisuje się teraz razem z wynikami (działa też po
  restarcie) i jest sprawdzana przy każdym kliknięciu.
- Weryfikacja sprawdza teraz także, czy błąd przeciwnika nadal jest błędem na głębokości
  weryfikacji; fałszywe pułapki odpadają.

### Lepsze wyniki
- Nowy ranking pułapek: oczekiwana strata przeciwnika na partię (dojście × siła) bez punktów „za samą
  siłę”; pułapki z bardzo małym dojściem idą na koniec („rzadkie dojście”), najwyżej kilka wyników z
  jednej rodziny wariantów na początku listy.
- Szukanie nie „nurkuje” w jedną forsowną linię: priorytet łączy szansę dojścia warunkową i
  bezwarunkową, a jedna rodzina wariantów może zająć najwyżej część budżetu.
- Kalibracja modelu ludzkich ruchów na prawdziwych partiach (Maia przeszacowuje wysokie odsetki
  błędów: przewidywane 60–100% to w partiach ~42%). Kalibrowana jest tylko część przewidywania
  pochodząca z modelu, nie z partii. Krzywa uczy się z każdego szukania.
- Nowe miary: zysk praktyczny (oczekiwany wynik przeciw człowiekowi), punkty na 100 partii, koszt
  linii, głębokość ukrycia błędu. Dotychczasowe wskaźniki zostały.
- Trudne linie: wynik = oczekiwana liczba błędów przeciwnika (ważona szansą dojścia), bez dubli.
- Nowinki: rzadkość także jako udział w partiach („rzadki wariant”); ranking według tego, czy
  przeciwnik zaraz potem się myli i czy wychodzi poza teorię.
- Limity wyników zależne od wielkości szukania (do 300 pułapek, 150 linii, 150 nowinek), więcej
  wyników weryfikowanych.
- Przy „W partiach” 95% przedział ufności i oznaczenie małej próby.

### Szybsze i dłuższe szukanie
- Transpozycje: ta sama pozycja jest liczona raz (w nocnym szukaniu powtórki zjadały 30% budżetu).
  Gdy weryfikacja odrzuci jedną kolejność ruchów, wyniki osiągalne inną kolejnością zostają.
- Konfigurowalna mniejsza głębokość silnika dla mało prawdopodobnych gałęzi.
- Tryb „do godziny”, zapis wyników co 15 minut i podgląd wyników częściowych (także z listy
  zapisanych), „Zakończ i zweryfikuj”, kolejka szukań w kolejności dodania, z usuwaniem z kolejki.

### Nowe
- **Maia-3** (opcjonalna, rankingi 600–2600) z wyborem modelu w ustawieniach; Maia-2 zostaje jako
  domyślna, gdy Maia-3 nie jest zainstalowana albo nie da się jej załadować.
- **Przygotowanie pod przeciwnika**: partie z Lichess, chess.com albo z lokalnej bazy (po nazwisku);
  jego ruchy w szukaniu, kolumna w „Ruchy ludzi”, informacja przy pułapkach.

## [0.4.0] – 2026-11-22

### Nowe
- **Kara za błąd**: przy każdym złym ruchu przeciwnika (pułapki i trudne linie) widać linię
  silnika, która go wykorzystuje. Kliknięcie ustawia pozycję po błędzie, a dalsze ruchy
  przechodzi się strzałką →. Nowe wyszukiwania zapisują karę od razu; w starszych liczy ją
  przycisk „jak ukarać?”.
- **Prawdziwe partie**: przy każdej pułapce widać, ilu graczy z wybranego rankingu naprawdę w
  nią wpadło (baza lokalna, Lichess, Masters) i jaki był potem Twój wynik w porównaniu z
  wynikiem ze wszystkich partii z tą pozycją.
- **Rodziny wariantów**: wyniki o wspólnym początku stoją obok siebie, z nagłówkami, które można
  zwijać (do 3 poziomów). Wspólna część linii jest wyszarzona. Przełącznik „od najlepszych”
  przywraca dawny układ.
- **Szukaj głębiej**: przycisk przy rodzinie uruchamia nowe wyszukiwanie od jej pozycji, z
  ustawieniami tamtego wyszukiwania.
- **Gwiazdki i notatki** przy pułapkach, liniach i nowinkach, z filtrem „★ moje”. Do tego filtry
  „zweryfikowane”, „ukryte” i „TN”.
- **Eksport do studium Lichess**: nowe albo istniejące studium, osobny rozdział dla każdej
  rodziny, komentarze, `?`/`!`, kara za błędy, notatki. Okno sprawdza uprawnienia tokenu i podaje
  link do utworzenia tokenu ze `study:write`. „Kopiuj PGN” daje te same rozdziały do schowka.
- Dokumentacja techniczna: `docs/ARCHITEKTURA.md`.

## [0.3.1] – 2026-11-22
- „✕ Zamknij” zamyka otwarte wyszukiwanie, a „Nowa” ustawia pozycję wyjściową.
- Długi opis zapisanego wyszukiwania nie wypycha przycisków poza okno.
- Pliki interfejsu są wysyłane z `no-cache`, więc po aktualizacji od razu działa nowa wersja.

## [0.3.0] – 2026-11-22
- Wybór silnika w ustawieniach, z osobną pamięcią ocen dla każdego silnika.
- Wybór bazy PGN z automatycznym indeksowaniem w tle (przerwij/wznów, pasek postępu).
- Wyniki podążają za szachownicą: lista przewija się do pozycji, którą ustawisz.

## [0.2.0] – 2026-11-22
- Nowinki (rzadkie ruchy w teorii, ≤ 0,5 piona od silnika, TN).
- Trudne linie: serie trudnych, nieoczywistych ruchów przeciwnika.
- Ukryte pułapki (silnik na małej głębokości też by się pomylił).
- Wybór bazy Lichess: gracze, Masters lub obie.

## [0.1.0] – 2026-11-22
- Pierwsza wersja: szukanie pułapek (Stockfish + Maia-2 + baza PGN + Lichess), okno desktopowe,
  zapisywane wyszukiwania, eksport drzewa do PGN.
