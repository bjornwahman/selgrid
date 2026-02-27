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

## Synology NAS (kör appen direkt i Docker-container)

Bra för ditt fall: kör Selgrid som en vanlig container på NAS:en.

1. Lägg hela repo-mappen på NAS, t.ex. `/volume1/docker/selgrid`.
2. I den mappen, kopiera `.env.synology.example` till `.env` och sätt minst `APP_SECRET`.
3. Starta:

```bash
cd /volume1/docker/selgrid
docker compose --env-file .env -f docker-compose.synology.yml up -d --build
```

Detta bygger imagen från repo (`Dockerfile`) och startar **en container** med både:
- Selenium (standalone chrome)
- Selgrid-webbappen

Uppdatering efter ändringar i repo:

```bash
cd /volume1/docker/selgrid
docker compose --env-file .env -f docker-compose.synology.yml up -d --build
```

Stoppa:

```bash
docker compose --env-file .env -f docker-compose.synology.yml down
```

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
