# Selgrid

Selgrid är en webbapp för att köra Selenium IDE (`.side`) tester mot Selenium Grid på samma server.

## Funktioner

- Form-baserad autentisering
- Uppladdning och parsing av `.side`
- Manuell körning av uppladdade tester
- Schemalagda körningar (minut-intervall)
- Testdetalj-sidor med metrics per körning och steg
- Secrets per test som kan användas i steg via `${SECRET_KEY}`

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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Öppna sedan `http://localhost:8080`.

## Konfiguration

- `SELENIUM_REMOTE_URL` (default: `http://127.0.0.1:4444/wd/hub`)
- `APP_SECRET` (default: `dev-secret`)
- `DATABASE_URL` (default: lokal SQLite-fil `selgrid.db`)
- `GRID_PORT` (används av `scripts/start_local_grid.sh`, default: `4444`)
- `SELENIUM_VERSION` (används av scripts, default: `4.27.0`)
- `SELENIUM_DIR` (används av scripts, default: `./.selenium`)

## Stödda Selenium IDE kommandon

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
- `waitForElementPresent`
- `setWindowSize`

Selektorer stöder `css=`, `xpath=`, `id=`, `name=`, `linkText=`, `partialLinkText=`, `class=` och `tag=`. Utan prefix tolkas target som CSS selector.

Övriga kommandon loggas som fel per steg.
