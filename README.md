# Ryanair PA/AP — jeden kalendarz, bezpłatna wersja

Przygotowany zamiennik kodu z main `438d4d64f3f48860d5e46cacbbe8d1645a3cab48`.
Wersja do wdrożenia zatwierdzona przez właściciela: po scaleniu do main uruchamia
pierwszy zapis i godzinowy harmonogram. RYANAIR_SIMPLE_ENABLED=0 wstrzymuje zapisy.
Gałąź przygotowawcza może zostać opublikowana do przeglądu; nie oznacza to wdrożenia main.
Push gałęzi prepare/simple-free-* uruchamia testy i podgląd Ryanair bez sekretów Google.

## Uzgodnione działanie

- Jedyny cel zapisów: **ryanairsamolot@gmail.com**. PA = POZ→ALC, AP = ALC→POZ.
- Harmonogram: codziennie co godzinę, o minucie 07, Europe/Warsaw.
  GitHub może opóźniać lub pomijać uruchomienia; to nie monitoring czasu rzeczywistego.
- Najbliższe 365 dni (od dziś do dziś + 365, włącznie).
- Jedno wydarzenie całodniowe dla kierunku i daty. Tytuł **PA 219** lub **AP 219**.
- Przy każdym przebiegu usuwane są własne wpisy cenowe i powiązane alerty oraz
  zakończone powiadomienia dla dat lotów wcześniejszych niż dzisiaj w Europe/Warsaw.
  Dzisiejsze i przyszłe daty pozostają. Agent zna datę lotu, nie potwierdza lądowania.
  Przed usunięciem ponownie sprawdza własność i datę, a po nim potwierdza brak wpisu.
  Wydarzenia obce, cykliczne, z uczestnikami lub niejednoznaczną datą są pomijane.
  Błędy usunięcia są zgłaszane i ponawiane przy kolejnym przebiegu.
- Kolory ceny: <300 zielony (10), 300–<500 żółty (5),
  500–<650 pomarańczowy (6), ≥650 czerwony (11).
- Opis: (POZ)-(ALC) lub (ALC)-(POZ), data lotu, czas poprawnego odczytu w Warszawie,
  link rezerwacji z kierunkiem, datą, jedną osobą dorosłą i podróżą w jedną stronę.
- Cena jest w PLN, bez „zł” w tytule; koszyk i taryfa Basic niepotwierdzone.
  Link wybiera trasę i datę, nie numer rejsu ani gwarantowaną cenę.
- Po każdym poprawnym odczycie aktualizowane są cena i czas, nawet bez zmiany ceny.
- Każdy dzień rozkładu jest odczytywany osobno: historyczna cena nie wyłącza kolejnych prób.
  Przy kilku ofertach wybierana jest najniższa cena danego kierunku i dnia.
- Nieudany lub pusty odczyt nie nadpisuje dotychczasowej ceny i jej czasu.
  Błędy jednego dnia nie odrzucają poprawnych danych z innych dni.
  Niewysłane operacje dla minionych dat lotów wygasają bez spóźnionego push.
- Poprawna odpowiedź Ryanair bez oferty (np. po odlocie lub przy braku miejsc)
  daje ostrzeżenie z listą dat, nie błąd całego zadania. Sam brak ceny nie dowodzi,
  że lot już się odbył. Awarie sieci, formatu danych, OAuth i zapisu nadal kończą
  zadanie błędem. Pole complete opisuje pokrycie cen, nie stan techniczny programu.
- Usunięty wpis cenowy odtwarza się po świeżym odczycie, pod nowym stabilnym ID.
  Stara oczekująca operacja bez świeżej ceny zostaje wycofana; cena i czas w stanie
  nie są nadpisywane. Usunięte alerty i push nie są odtwarzane, a ich wycofanie
  z kolejki jest zapisane jako ostrzeżenie.
- Ceny i procenty w kalendarzu są zaokrąglane do liczb całkowitych, połówki w górę
  (218,50 PLN → 219; 9,17% → 9%). Porównania i progi używają dokładnych wartości.
- Zmiana **>8%** tworzy osobny całodniowy alert: **🔔PA 9%** lub **🔔AP 9%**.
  Wzrost = czerwony (11), spadek = zielony (10). Dokładnie 8% nie generuje alertu.
- W całym kalendarzu pozostaje **najwyżej jeden całodniowy alert dla kierunku i daty**:
  jeden PA i jeden AP na datę lotu. Nowy alert zastępuje poprzedni po potwierdzeniu
  zapisu i odczytu kontrolnego Google. Nie wygrywa największy procent, tylko
  najnowszy poprawny odczyt, który spełnił próg. Zmiana poniżej progu pozostawia
  poprzedni alert. Wpis z bieżącą ceną pozostaje osobno.
- Każdy przebieg porządkuje również wcześniejsze powielone własne alerty w całym
  kalendarzu, także dla dat minionych i poza horyzontem odczytu. Wybiera najnowszy
  według czasu obserwacji; starsze usuwa dopiero po sprawdzeniu zachowanego wpisu.
  Błąd usuwania jest zgłaszany i ponawiany. Obce wpisy i wydarzenia z uczestnikami
  nie są usuwane. Dane własności muszą wskazywać dokładny kierunek i datę.
- Nowszy alert zastępuje też starsze niewysłane alerty i powiadomienia w kolejce
  dla tego kierunku i daty. Zakończone powiadomienia telefonu są osobną historią:
  ograniczenie jednego dzwoneczka dotyczy całodniowych alertów na dacie lotu.
- Próg liczymy od ceny ostatniego wygenerowanego alertu, a po resecie tygodniowym
  od ceny bazowej; pierwsza obserwacja nowej daty ustanawia bazę bez alertu.
  Przykład: 200→210→218 = alert +9%; dalsze porównania do 218.
- Przy zmianie **≥20%** powstaje też krótki wpis powiadomienia na teraz +5 minut
  z przypomnieniem popup w chwili rozpoczęcia. All-day alert zostaje na dacie lotu.
  To konieczne: przypomnienie na dacie lotu nie powiadomiłoby o zmianie dziś.
- Nie ma rezerwacji, płatności, modeli AI, SMS, zewnętrznych usług push ani serwera.
- Raz w tygodniu, od poniedziałku **04:07**: pełne odświeżenie cen i baz oraz
  usunięcie własnych alertów i krótkich wpisów push. Pierwszy przebieg jest bazowy.
  Jeśli planowy termin został pominięty, reset wykona najbliższy udany przebieg.
- Reset wymaga odczytu bez błędów technicznych i co najmniej jednej świeżej ceny.
  Brak oferty dla pojedynczych dni nie blokuje resetu; ich ostatnie ceny, czasy
  i bazy pozostają bez zmian. Pusta cała lista cen odkłada reset.
- Niedokończony reset ponownie odczytuje oferty przed ponowieniem zapisu.
  Nie tworzy wtedy alertów zmian, tylko odświeża bazę. Dopiero zweryfikowany zapis
  dostępnych cen pozwala usuwać stare alerty; błąd techniczny blokuje czyszczenie.
- Wydarzenia prywatne i wpisy bez jednoznacznego znacznika pozostają nietknięte.

## Jak to jest zbudowane

Jeden workflow, moduł `monitor.py`, adapter `calendar_sync.py`, adaptery odczytu
Ryanair i model danych. Jeden plik roboczy `state/runtime.json` przechowuje ceny,
czasy, bazy, status tygodnia i trwałą kolejkę operacji. Nie używamy dawnej kolejki
z trzech kalendarzy ani nie interpretujemy starych cen jako świeżych obserwacji.

Kolejka jest publikowana do GitHuba **przed** zapisami Google. Każde wydarzenie
ma stabilne ID. Postęp usuwamy z kolejki dopiero po odczycie kontrolnym.
Awaria po utworzeniu wydarzenia, a przed zapisem potwierdzenia, nie tworzy
nowego wydarzenia przy ponowieniu. Zwykły push odrzuca konflikt; nie używamy force.
Po wyjściu z kolejki monitor pobiera i testuje aktualny main, łącznie ze stanem
zapisanym przez poprzedni przebieg, zamiast wersji z chwili wyzwolenia harmonogramu.

Odczyt Google jest stronicowany, token OAuth odnawiany, przejściowe błędy
bezpiecznych operacji ponawiane. Błąd dostawy daje niezerowy kod zakończenia.
Logi raportują kompletność odczytu i pozostałe operacje.
`python monitor.py status` sprawdza błędy techniczne oraz kolejkę, osobno pokazując
ostrzeżenia o brakujących ofertach. Nie wyciszamy powiadomień GitHuba o awariach.

## Bezpłatne platformy i ograniczenia

- Publiczne repozytorium GitHub + standardowy runner Ubuntu GitHub Actions:
  standardowe runnery w publicznych repozytoriach są bezpłatne.
  https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Standardowe użycie Google Calendar API jest bez dodatkowej opłaty, w granicach limitów.
  https://developers.google.com/workspace/calendar/api/guides/quota
- Kod Python korzysta tylko ze standardowej biblioteki. Nie wymaga instalacji pakietów.
- Ryanair: nieoficjalne endpointy, bez gwarancji dostępności lub niezmienności.
  Do około 732 zapytań ofertowych na godzinę plus rozkład; czterech pracowników,
  maksymalnie jedna dodatkowa próba na dzień. Należy sprawdzić zachowanie API,
  limity i regulamin przed aktywacją. Horyzont 365 dni jest głównym źródłem obciążenia.
- Powiadomienie na telefon zależy od synchronizacji, uprawnień do powiadomień,
  konta Google, ustawień systemu i połączenia. API nie potwierdza odebrania push.
  Konto autoryzujące OAuth musi być używane również w Google Calendar na telefonie:
  przypomnienia są ustawieniami użytkownika, nie wszystkich osób współdzielących kalendarz.
  Dokumentacja przypomnień: https://developers.google.com/workspace/calendar/api/concepts/reminders
- Brak płatnych zasobów w konfiguracji. Zadanie zapisujące jest zablokowane w repo prywatnym.
  Nie włączaj płatnych runnerów ani dodatkowych usług.
- Publiczny stan ujawnia monitorowane kierunki, daty i ceny, ale nie tokeny.
  Sekrety wyłącznie w GitHub Actions Secrets; nie kopiuj ich do plików/czatu.

## Weryfikacja lokalna

```sh
python -m unittest discover -s tests -v
python monitor.py preview
```

Testy są offline, na fikcyjnych danych i fałszywym API.
`preview` czyta rzeczywiste Ryanair, ale nie Google i nie zapisuje stanu.
Sieciowy preview może potrwać; sam brak dostępu w danym środowisku nie dowodzi awarii Ryanair.

## Wdrożenie — po akceptacji kodu i podglądu

1. Wprowadź przygotowaną zmianę do produkcyjnego repozytorium, po przeglądzie diffu.
   Wymagany Python 3.12. Stare workflow i alternatywne integracje są usunięte w tej kopii.
2. Zatrzymaj ewentualne zewnętrzne zadania ChatGPT/Apps Script, które zapisują PA/AP.
   Samo wdrożenie kodu nie wyłącza usług działających poza repozytorium.
3. Sprawdź testy i ręcznie uruchom workflow w trybie `preview`.
   Tylko main może wykonywać zadanie monitorujące.
4. Użyj istniejącego OAuth lub własnego klienta Google OAuth z Calendar API
   i zakresem calendar.events. Konto autoryzujące musi edytować jedyny kalendarz.
   Sprawdź tryb publikacji aplikacji OAuth i ważność tokenu odświeżania;
   tryb testowy może wymagać ponownej autoryzacji.
5. Ustaw sekrety GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN.
   Nie zmieniamy ich w ramach przygotowania.
6. Przejrzyj istniejące wydarzenia: migracja przejmuje wyłącznie własne loty
   z private.managed_by=ryanair-pa-ap-agent, kind=flight i pasującą trasą/datą.
   Dwa takie wpisy dla tej samej daty blokują operację do ręcznego wyjaśnienia.
   Wpisy rozpoznawalne tylko po tytule/opisie nie są przejmowane ani usuwane.
   Jeśli istnieją, trzeba najpierw zatwierdzić osobny plan porządkowania po ID.
7. Po zatwierdzeniu wdrożenia scalenie do main rozpoczyna pierwszy zapis.
   Brak zmiennej **RYANAIR_SIMPLE_ENABLED** oznacza aktywny monitor;
   wartość 1 również go włącza, a 0 wstrzymuje. Podgląd ręczny pozostaje dostępny.
   Publikacja kolejki wymaga contents:write i możliwości zwykłego push pliku stanu.
8. Sprawdź wpis cenowy, kolory graniczne i czas/link w jednym kalendarzu.
   Test telefonu musi obejmować rzeczywiste odebranie przypomnienia popup
   na krótkim wydarzeniu testowym, bez wymyślania ceny lotu.
9. Zweryfikuj pierwszy reset i kolejny godzinowy przebieg; w razie problemu
   ustaw RYANAIR_SIMPLE_ENABLED=0. Nie czyść pliku stanu podczas ponowień.

Podgląd i testy nie tworzą wydarzeń. Zapis produkcyjny jest ograniczony do main.
