# Selgrid

Selgrid är en webbapp för att köra Selenium IDE (`.side`) tester mot en Selenium Grid endpoint.

## Funktioner

- Form-baserad autentisering
- Uppladdning och parsing av `.side`
- Manuell körning av uppladdade tester
- Schemalagda körningar (minut-intervall)
- Testdetalj-sidor med metrics per körning och steg
- Secrets per test som kan användas i steg via `${SECRET_KEY}`

## Kom igång lokalt

1. Starta Selenium Grid (exempel: standalone Chrome) så att den är nåbar, t.ex. på `http://127.0.0.1:4444/wd/hub`.
2. Starta webbappen:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Öppna sedan `http://localhost:8080`.

### Konfiguration

- `SELENIUM_REMOTE_URL` (default: `http://127.0.0.1:4444/wd/hub`)
- `APP_SECRET` (default: `dev-secret`)
- `DATABASE_URL` (default: lokal SQLite-fil `selgrid.db`)

Exempel:

```bash
export SELENIUM_REMOTE_URL="http://127.0.0.1:4444/wd/hub"
python app.py
```

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
