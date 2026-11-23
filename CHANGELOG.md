# Historia zmian

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
