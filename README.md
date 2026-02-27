# Selgrid

Selgrid är en modern webbapp för att köra Selenium IDE (`.side`) tester mot Selenium Grid på samma server.

## Funktioner

- Dark mode GUI med professionell dashboard
- Uppladdning och körning av `.side`-checkar
- Editera, pausa och ta bort checkar
- Körhistorik med trendgraf över svarstid/status
- Secrets per check via `${SECRET_KEY}`
- Admin-sida för API bearer tokens (`/admin`)
- Swagger/OpenAPI-dokumentation under `/docs`

## Inloggning

Självregistrering är avstängd.

Standardkonto vid ny installation:
- username: `admin`
- password: `admin`

Du kan ändra standardvärden med miljövariabler:
- `DEFAULT_ADMIN_USERNAME`
- `DEFAULT_ADMIN_PASSWORD`

## Selenium Grid lokalt på servern

```bash
scripts/install_local_grid.sh
scripts/start_local_grid.sh
```

## Starta webbappen

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## API (Bearer Auth)

1. Logga in som admin och skapa token på `/admin`.
2. Anropa API med header:

```bash
Authorization: Bearer <token>
```

Exempel:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/tests
```

## Konfiguration

- `SELENIUM_REMOTE_URL` (default: `http://127.0.0.1:4444/wd/hub`)
- `APP_SECRET` (default: `dev-secret`)
- `DATABASE_URL` (default: `sqlite:///selgrid.db`)
- `DEFAULT_ADMIN_USERNAME` (default: `admin`)
- `DEFAULT_ADMIN_PASSWORD` (default: `admin`)
