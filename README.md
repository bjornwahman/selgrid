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

## Synology NAS (Docker Compose + GitHub token)

Om du vill att NAS:en själv hämtar källkod från GitHub och startar Selgrid:

1. Kopiera filerna `docker-compose.synology.yml` och `.env.synology.example` till din NAS.
2. Byt namn på `.env.synology.example` till `.env` och fyll i värden.
3. Kör:

```bash
docker compose --env-file .env -f docker-compose.synology.yml up -d --build
```

Detta använder `build.context` från GitHub **tarball (codeload)** i stället för `repo.git`, vilket undviker felet `unable to find 'git'` som ofta uppstår i Synology-miljöer:

- `GITHUB_USERNAME`
- `GITHUB_TOKEN` (PAT / fine-grained token med repo-read)
- `GITHUB_REPOSITORY` (t.ex. `ditt-konto/selgrid`)
- `GITHUB_REF` (branch-namn, t.ex. `main`)

> Tips: använd en token med minst `repo:read` / motsvarande fine-grained read access till repot.

För uppdatering till ny commit:

```bash
docker compose --env-file .env -f docker-compose.synology.yml build --no-cache
docker compose --env-file .env -f docker-compose.synology.yml up -d
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
