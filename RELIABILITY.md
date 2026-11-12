# Niezawodność i limity — 12.11.2026

## Zachowane działanie

Jeden pełny odczyt co godzinę (`7 * * * *`, Europe/Warsaw), 24 planowane
cykle na dobę. Oba kierunki, horyzont 365 dni, osobne zapytanie dla każdej
daty opublikowanego rozkładu. Nie przechodzimy na tygodniowe okna cenowe:
odpowiedź fare-finder może być niepełna, więc oszczędność zapytań nie może
odbywać się kosztem pomijania dat. Progi alertów i format kalendarza bez zmian.

## Zmiany

- Ryanair: wspólny dla rozkładu i ofert limiter 2 żądań/s, również pomiędzy
  czterema pracownikami. To ostrożny limit lokalny, nie oficjalny limit Ryanair.
- Google: 5 jednostek/s (około 300/min), z kosztem PATCH równym 3. Wszystkie
  żądania, w tym ponowienia i odczyty kontrolne, przechodzą przez limiter.
- Ponawiane są również 403 z powodami rateLimitExceeded/userRateLimitExceeded,
  z wykładniczym opóźnieniem i losowym rozrzutem. Zwykłe forbidden nie jest
  bezcelowo ponawiane. Numeryczne Retry-After do 60 s jest respektowane;
  dłuższe odracza pracę z zachowaniem kolejki.
- POST nadal nie jest ponawiany w ciemno po błędzie. Stabilne ID i istniejąca
  weryfikacja chronią przed duplikatami przy następnym cyklu.
- Błąd OAuth ujawnia wyłącznie dozwolony kod (np. invalid_grant), bez pełnej
  odpowiedzi Google i bez wartości danych uwierzytelniających.
- Strony `docs/` służą wyłącznie informacji i konfiguracji OAuth. Nie uruchamiają
  kolejnej automatyzacji ani nie zwiększają liczby zapytań cenowych.

## Oszacowanie obciążenia

Dla 602 dat lotów oraz 13 miesięcy w każdym z dwóch kierunków:

| Usługa | Jeden cykl | 24 cykle/dobę |
|---|---:|---:|
| Ryanair, bez ponowień | 602 ofert + 26 rozkładów = 628 | 15 072 |
| Ryanair, każde zapytanie ofertowe ponowione raz | 1 230 | 29 520 |
| Google, 601 istniejących wpisów cenowych | ok. 1 803 żądania / 3 005 jednostek + listowanie | ok. 43 272 żądania / 72 120 jednostek + listowanie |

Google: GET przed zmianą + PATCH (3 jednostki) + GET kontrolny = 3 żądania,
5 jednostek na zmieniony wpis. Alerty, push, usuwanie starych alertów, paginacja,
błędy i przywracanie usuniętych ID zwiększają te liczby. Nie jest to gwarantowana
górna granica. Brak zmiany ceny nie pomija aktualizacji czasu obserwacji.

Przy limiterach orientacyjny czas zdrowego cyklu wynosi około 5–6 minut odczytu
Ryanair i 10–13 minut dostawy cen Google, plus alerty, sieć i ponowienia. Timeout
45 minut pozostaje zabezpieczeniem; niezakończona kolejka przetrwa do następnego
cyklu. Nie należy zwiększać częstotliwości do 5 minut dla całego horyzontu.

Domyślna dokumentowana quota Google to 600/min na użytkownika/projekt oraz
10 000/min na projekt. Rzeczywiste ustawienia projektu i limity operacyjne
pojedynczego kalendarza mogą być inne. Inne aplikacje korzystające z tego samego
użytkownika/projektu współdzielą quota. Pacing zmniejsza ryzyko, nie gwarantuje
braku 403/429. Nie ma potwierdzonej publicznej quota dla użytych endpointów Ryanair.
Nie obchodzimy blokad ani limitów przez rotację kont/IP.

## Autoryzacja i utrzymanie

Obecny znany problem produkcji to odświeżanie OAuth. Zmiana kodu nie naprawia
wygasłego tokenu. Najpierw strona aplikacji i polityka, publikacja OAuth,
ponowna zgoda właściciela i zapis tokenu w Actions Secrets. Nie dodajemy tokenów
do repozytorium. Publikacja OAuth nie zapewnia nieodwołalności tokenu.

GitHub harmonogram może się opóźnić lub zostać pominięty. Concurrency i trwała
kolejka chronią stan, ale nie gwarantują uruchomienia o konkretnej minucie.
Nie dodajemy drugiego schedulera, który wykonywałby równolegle te same odczyty.
Po autoryzacji należy sprawdzić pełny cykl, pustą kolejkę oraz kolejny cykl z cron.
Brak błędu Actions nie dowodzi odebrania powiadomienia przez telefon.

## Źródła

- https://developers.google.com/workspace/calendar/api/guides/quota
- https://developers.google.com/workspace/calendar/api/guides/errors
- https://developers.google.com/workspace/calendar/api/v3/reference/events/patch
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
- https://developers.google.com/identity/protocols/oauth2#expiration
- https://rclone.org/drive/#making-your-own-client-id

Wnioski o limicie i retry są oparte na dokumentacji dostawców; szacunki wynikają
z kodu i ostatniego przebiegu. Dyskusje użytkowników o opóźnieniach Actions nie
stanowią gwarancji dostępności ani potwierdzenia limitu Ryanair.
