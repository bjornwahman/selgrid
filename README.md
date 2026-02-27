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

## Synology NAS (starta container först, ladda ner repo inuti containern)

För Synology där build-motorn strular med Git kan du köra utan `docker build`.
Containern startar från `selenium/standalone-chrome` och scriptet `synology-start.sh`
installerar beroenden, klonar repo och startar Selgrid.

1. Skapa mapp på NAS, t.ex. `/volume1/docker/selgrid`.
2. Kopiera dit dessa filer från repot:
   - `docker-compose.synology.yml`
   - `.env.synology.example` (byt namn till `.env`)
   - `synology-start.sh`
3. Uppdatera värden i `.env`.
4. Starta:

```bash
docker compose --env-file .env -f docker-compose.synology.yml up -d
```

Scriptet i containern gör detta vid start:
- installerar `git`, `python3`, `pip`, `supervisor`
- klonar `https://github.com/<owner>/<repo>.git` (eller med token för privat repo)
- checkout av `GITHUB_REF`
- `pip install -r requirements.txt`
- startar `supervisord` (Selenium + Selgrid webbapp)

Uppdatering till ny version:

```bash
docker compose --env-file .env -f docker-compose.synology.yml restart selgrid
```

Byt `GITHUB_REF` i `.env` om du vill pinna annan branch/tag.

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
