# Historia zmian

## [Niewydane]

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
