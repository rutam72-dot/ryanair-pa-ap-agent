# Ryanair PA/AP — monitor Poznań ↔ Alicante

PA = Poznań (POZ) → Alicante (ALC); AP = Alicante (ALC) → Poznań (POZ).

## Zasady
- Tylko rzeczywiście potwierdzone bezpośrednie loty i ceny PLN z nieoficjalnego API Ryanair; bez HeroFly. To oferty, nie potwierdzone koszyki Basic.
- Horyzont monitorowania: 365 dni, o ile dane Ryanair są dostępne. Daty bez cen nie są przedstawiane jako zweryfikowane oferty.
- Sześć uruchomień GitHub Actions na dobę (00:07, 04:07, 08:07, 12:07, 16:07, 20:07, Europe/Warsaw); GitHub może opóźniać start.
- Zmiana ceny o dowolną niezerową kwotę zapisuje informację o wzroście/spadku w historii i generuje osobny alert.
- Historia porównań w `state/history.json`. Plik `state/latest_verified.json` zapisuje dla każdej faktycznie odczytanej ceny PLN czas `observed_at` i źródło `Ryanair unofficial API`; starsze rekordy nie są świeżą ofertą.
- Brak wydarzeń łączonych PA+AP; nie ingeruj w prywatne wpisy kalendarzy.

## Google Calendar — osobna autoryzacja
Moduł `calendar_sync.py` synchronizuje trzy kalendarze:
`rutam72@gmail.com`, `b.prokglock@gmail.com` oraz `ryanairsamolot@gmail.com`.

Aby włączyć synchronizację GitHub → Google Calendar:
1. W Google Cloud włącz **Google Calendar API** i skonfiguruj klienta **OAuth 2.0** z zakresem `https://www.googleapis.com/auth/calendar.events`; użytkownik Google autoryzujący dostęp musi posiadać uprawnienia do edycji wszystkich trzech kalendarzy.
2. Uzyskaj własny token odświeżania OAuth dla tego klienta, z dostępem offline.
3. Dodaj w GitHub **Settings → Secrets and variables → Actions** trzy szyfrowane sekrety:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`
4. Uruchom workflow `Ryanair PA AP monitor` z zakładki Actions i sprawdź logi oraz trzy kalendarze.

**Nie wpisuj tokenów, haseł ani danych OAuth do repozytorium, czatu lub plików z historią.**
Agent ChatGPT może czytać świeże rekordy `state/latest_verified.json` i aktualizować podłączone kalendarze swoim uprawnieniem, BEZ sekretów Google w GitHubie. Jest to osobny mechanizm od bezpośredniej synchronizacji GitHub → Google Calendar. Połączenie Google Calendar w samym ChatGPT **nie jest** automatycznie udostępniane GitHub Actions. Bez tych sekretów skrypt jawnie wypisuje `Google Calendar SYNC SKIPPED` i nadal może zapisywać historie cen. Jeśli sekrety są ustawione, błąd autoryzacji/uprawnień zatrzymuje krok synchronizacji zamiast pozorować sukces.

Synchronizator aktualizuje istniejące wydarzenie zamiast duplikować je, zapisuje osobne alerty, utrzymuje ceny historyczne przy braku nowego odczytu, czyści jednoznacznie rozpoznane agentowe wpisy historyczne i nie tworzy wydarzeń weekendowych. Aktualizacje wykonuje w ograniczonych partiach (do 60 operacji na kalendarz na przebieg). Pełna migracja dużego kalendarza może wymagać wielu przebiegów.

**Uwaga:** osobny agent harmonogramu ChatGPT może nadal modyfikować te same kalendarze. Po uruchomieniu synchronizacji GitHub należy wskazać jednego właściciela zapisów, aby uniknąć konfliktów.

## Testy
`python -m unittest discover -s tests -v`

Kod źródłowy: `monitor.py`, `ryanair_client.py`, `ryanair_schedule.py`, `alerts.py`, `calendar_sync.py`.
