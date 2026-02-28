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

## Version och logg

Öppna sedan `http://localhost:8080`.


## Version och logg

- `version.txt` i projektroten innehåller aktuell appversion.
- Fel loggas till `selgrid.log` i projektroten (roterande loggfil).

## Konfiguration

- `SELENIUM_REMOTE_URL` (default: `http://127.0.0.1:4444/wd/hub` utanför Docker, `http://selenium-hub:4444/wd/hub` i Docker)
- `APP_SECRET` (default: `dev-secret`)
- `SELENIUM_GRID_STATUS_URL` (default: `http://127.0.0.1:4444/status` utanför Docker, `http://selenium-hub:4444/status` i Docker)
- `CHROME_SELENIUM_STATUS_URL` (default: `http://127.0.0.1:5555/status` utanför Docker, `http://chrome-selenium:5555/status` i Docker)
- `SELENIUM_GRID_HOST` (valfritt hostnamn för Grid när URL:er inte satts explicit)
- `CHROME_SELENIUM_HOST` (valfritt hostnamn för Chrome Selenium när URL:er inte satts explicit)
- `DATABASE_URL` (default: lokal SQLite-fil `selgrid.db`)
- `DEFAULT_ADMIN_USERNAME` och `DEFAULT_ADMIN_PASSWORD` (valfritt: om båda sätts skapas admin-användaren automatiskt vid start)
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
- `assertElementVisible` / `assertElementNotVisible`
- `waitForElementPresent`
- `waitForElementVisible` / `waitForElement`
- `waitForElementNotPresent` / `waitForElementNotVisible`
- `waitForElementClickable`
- `clear`
- `setWindowSize`
- `comment` / `echo` / `note` (textinformation i flödet)


Locator i `target` kan skrivas både som Selenium IDE-prefix (t.ex. `xpath=//button`) och i Selenium Python-stil (t.ex. `By.XPATH=//button`, `By.ID=email`, `By.CSS_SELECTOR=.btn`).

Om ett kommando inte stöds ännu loggas det som **warning** på steget och visas tydligt i GUI.
