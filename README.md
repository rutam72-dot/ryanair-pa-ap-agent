# Ryanair PA/AP agent

Monitor bezpośrednich lotów Ryanair PA (POZ → ALC) i AP (ALC → POZ).

## Założenia
- Ceny potwierdzone bezpośrednio przez Ryanair, bez HeroFly; waluta PLN.
- Każdy dzień, w którym istnieje bezpośredni lot.
- Docelowo cztery sprawdzenia na dobę, także w nocy.
- Trzy kalendarze Google: krótkie tytuły PA•199zł / AP•249zł.
- Osobne zdarzenia 🔔 przy zmianach cen, kolor rozróżnia wzrost i spadek.
- Brak ceny: „Nie odczytano z Ryanair”, ponowna próba w następnym cyklu.
- Brak wpisów PA+AP, bez duplikatów, bez lotów z przeszłości.
- Linki do rezerwacji w polskiej wersji strony Ryanair.

## Stan
Dodano model cen, porównanie i testy jednostkowe. Integracja z Ryanair, Google Calendar, trwała historia i automatyczne uruchamianie są jeszcze do wdrożenia. Nie publikować fikcyjnych cen. Sekrety przechowywać poza repozytorium.

## Testy
`python -m unittest discover -s tests`
