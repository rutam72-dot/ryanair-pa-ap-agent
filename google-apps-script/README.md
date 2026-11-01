# Bezpłatna synchronizacja Google Apps Script

Ten wariant **nie wymaga Google Cloud, konta usługi ani klucza JSON**.
Skrypt działa na koncie Google właściciela i odczytuje publiczny plik
`state/history.json` z GitHub. Repozytorium musi być publiczne lub należy
zastosować inny, uwierzytelniony sposób udostępnienia danych.

## Uruchomienie bez zapisów
1. Zaloguj się na `rutam72@gmail.com`, otwórz https://script.google.com/.
2. Utwórz nowy projekt `Ryanair PA AP`.
3. Skopiuj pełną zawartość `google-apps-script/Code.gs` do edytora.
4. Pozostaw `DRY_RUN: true`.
5. Wybierz funkcję `syncRyanair` i kliknij **Uruchom**.
6. Nadaj wymagane uprawnienia do kalendarzy i pobierania danych.
7. Otwórz **Dziennik wykonania**. Powinien pokazać trzy kalendarze
   i liczbę planowanych wpisów. Nie powinien zmienić wydarzeń.

## Po zatwierdzeniu testu
- Ustaw `DRY_RUN: false`, zapisz i uruchom `syncRyanair` ręcznie.
- Sprawdź kilka wpisów we wszystkich trzech kalendarzach.
- Dopiero potem uruchom `installFourHourTrigger` jeden raz.
- `checkStatus` pokazuje ostatnią udaną synchronizację i liczbę wyzwalaczy.

## Ograniczenia i bezpieczeństwo
- Skrypt modyfikuje tylko wpisy zawierające własny znacznik w opisie;
  nie usuwa wydarzeń i nie dotyka innych wpisów.
- Wykryte duplikaty raportuje, ale nie kasuje ich automatycznie.
- Przy braku danych z Ryanair zachowuje poprzednie wpisy.
- Historia z GitHub zawiera tylko ceny z potwierdzonych odczytów, a nie
  gwarantowany kompletny rozkład lotów.
- Pierwsze uruchomienie nie wygeneruje dzwoneczków historycznych.
- Dzwoneczki powstają dla zmian co najmniej 10% między kolejnymi
  **udanymi synchronizacjami Apps Script**; alerty obserwowane w tym
  samym dniu dla tego samego lotu mogą zostać scalone przez znacznik.
- Kolory: zielony spadek, czerwony wzrost.
- Google Apps Script ma limity i nie gwarantuje wykonania dokładnie
  o konkretnej minucie.
- Dotychczasowy workflow GitHub monitoruje ceny niezależnie.
- Stary workflow `calendar-dry-run.yml` oparty na koncie usługi nie
  jest wymagany i nie należy go uruchamiać w tym wariancie.
