# E2E — Smoke Playwright.dev (test checklist)

Checklist sintetica usata come banco di prova del runner locale. Naviga su un sito pubblico e stabile (`playwright.dev`).

## Prerequisiti
- Connessione internet
- Browser Chromium installato (`playwright install chromium`)

## Sezione 1 — Homepage

| # | Azione | Risultato Atteso | Pass |
|---|--------|------------------|------|
| 1 | Apri il browser e naviga a `https://playwright.dev/` | La homepage si carica e il titolo dell'eroe contiene la frase "Playwright enables reliable web automation". | [ ] |
| 2 | Clicca il bottone verde "Get started" nell'eroe della homepage | Il browser naviga su una pagina la cui URL contiene `/docs` e in cui è visibile il testo "Installation". | [ ] |

## Sezione 2 — Navigazione documentazione

| # | Azione | Risultato Atteso | Pass |
|---|--------|------------------|------|
| 3 | Dalla pagina docs, clicca la voce "API" nella navbar in alto | Si apre la pagina dell'API reference; è visibile il testo "Playwright Library" o "API reference" nella zona principale. | [ ] |
| 4 | Clicca il logo "Playwright" in alto a sinistra | Il browser torna alla homepage; la headline "Playwright enables reliable web automation" è di nuovo visibile. | [ ] |
| 5 | Verifica visivamente che nella homepage siano visibili le tre card "Playwright Test", "Playwright CLI", "Playwright MCP" | I tre titoli compaiono in fila sotto l'eroe. | [ ] |
