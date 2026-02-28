# Selgrid

Selgrid är en webbapp för att köra Selenium IDE (`.side`) tester mot Selenium Grid på samma server.

## Funktioner

- Form-baserad autentisering
- Uppladdning av `.side` via fil **eller raw JSON (paste)**
- Redigering av check-inställningar (namn, intervall, aktiv)
- Redigering av `.side`-steg i GUI i tabellform (kommando/target/value)
- Lägg till textnoteringar i testflödet ("nu startar vi", "nu klickar vi", etc.)
- Varningar i UI om checken innehåller kommandon utan implementation
- Manuell körning + schemalagda körningar
- Körhistorik med stegmetrics och trendgraf över total körtid
- Extra metrics: antal körningar, success rate, snittid och varningssteg
- Dark mode UI med orange kontrastfärg

## Krav på servern

- Python 3.10+
- Java 17+
- Chrome/Chromium installerad
- Selenium Grid (standalone) installerad lokalt

## Installera Selenium Grid lokalt på servern

```bash
scripts/install_local_grid.sh
```

Starta sedan Grid:

```bash
scripts/start_local_grid.sh
```

Grid startar som standard på `http://127.0.0.1:4444`.

## Starta webbappen

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/tests
```

Öppna sedan `http://localhost:8080`.

## Konfiguration

- `SELENIUM_REMOTE_URL` (default: `http://127.0.0.1:4444/wd/hub`)
- `APP_SECRET` (default: `dev-secret`)
- `DATABASE_URL` (default: lokal SQLite-fil `selgrid.db`)
- `GRID_PORT` (används av `scripts/start_local_grid.sh`, default: `4444`)
- `SELENIUM_VERSION` (används av scripts, default: `4.27.0`)
- `SELENIUM_DIR` (används av scripts, default: `./.selenium`)

## Stödda Selenium IDE-kommandon

- `open`
- `click`
- `doubleClick`
- `type`
- `sendKeys`
- `select` (`label=`, `value=`, `index=`)
- `check` / `uncheck`
- `mouseOver`
- `submit`
- `pause`
- `assertTitle`
- `assertText`
- `assertValue`
- `assertElementPresent`
- `assertElementNotPresent`
- `waitForElementPresent`
- `waitForElementVisible` / `waitForElement`
- `waitForElementNotPresent` / `waitForElementNotVisible`
- `setWindowSize`
- `comment` / `echo` / `note` (textinformation i flödet)

Om ett kommando inte stöds ännu loggas det som **warning** på steget och visas tydligt i GUI.
