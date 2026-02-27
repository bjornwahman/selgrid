# Selgrid

Selgrid är en webbapp för att köra Selenium IDE (`.side`) tester mot Selenium Grid med:

- Form-baserad autentisering
- Uppladdning och parsing av `.side`
- Schemalagda körningar (minut-intervall)
- Testdetalj-sidor med metrics per körning och steg
- Secrets per test som kan användas i steg via `${SECRET_KEY}`
- En enda Docker image som kör både Selenium Grid (standalone chrome) och webbappen

## Start med Docker

```bash
docker build -t selgrid .
docker run --rm -p 8080:8080 -p 4444:4444 selgrid
```

Öppna sedan `http://localhost:8080`.

## Lokalt utan Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

> Obs: appen behöver Selenium Grid endpoint, default är `http://127.0.0.1:4444/wd/hub`.

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

Övriga kommandon loggas fortfarande som fel per steg.
