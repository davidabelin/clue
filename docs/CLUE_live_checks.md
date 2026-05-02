# Clue v1.9.0 Live Checks

Use these checks when validating the live chatbot path. Write-based proof belongs on the smoke service only; production checks are read-only.

## Local OpenAI + SQLite Smoke

From Command Prompt or by double-clicking in Explorer:

```bat
run-local.bat
```

This starts local Clue with `CLUE_ADMIN_TOKEN=local-admin` and `CLUE_DB_PATH=data\clue-dev.db`. Direct `python run.py` / `py -3.14 run.py` local starts also default to `local-admin` when no token is set. Open `http://127.0.0.1:5002/admin` and paste `local-admin`.

For local LLM runs, keep the Clue OpenAI service-account key in untracked `set_clue_env.bat` as `OPENAI_CLUE_SA_KEY`. Clue ignores generic `OPENAI_API_KEY` so it does not accidentally use a shared Zenbot key.

To put the local admin token on the clipboard:

```bat
echo local-admin|clip
```

PowerShell equivalent:

```powershell
pip install -r requirements.txt
$env:CLUE_DB_PATH = "$PWD\data\clue-live-smoke.db"
$env:CLUE_ADMIN_TOKEN = "local-admin"
$env:OPENAI_CLUE_PROJECT_ID = "proj_Lw53USO5NinnThSmUspUs1Kt"
$env:OPENAI_CLUE_SA_KEY = "<Clue service-account key or omit when using OPENAI_CLUE_SA_KEY_SECRET_VERSION>"
python run.py
```

Create a table with at least one LLM seat, confirm LLM turns fail loudly when credentials are missing, and confirm live LLM seats can chat/act when credentials are present.

## Admin Token Clipboard Commands

Production Admin page:

```text
https://aix-labs.uw.r.appspot.com/clue/admin
```

Copy the production token from Secret Manager to the clipboard:

```bat
gcloud secrets versions access latest --secret=clue-admin-token --project=aix-labs | clip
```

Smoke Admin token:

```bat
gcloud secrets versions access latest --secret=clue-smoke-admin-token --project=aix-labs | clip
```

A normal App Engine deployment does not create a new admin token. Re-copy from Secret Manager only when the secret is rotated or the pasted token stops working.

## Deploy Smoke Service

```powershell
gcloud app deploy app.smoke.yaml --project aix-labs
```

Smoke service URL:

```text
https://VERSION-dot-clue-smoke-dot-aix-labs.uw.r.appspot.com/smoke-clue/
```

Use the version-specific URL printed by `gcloud app deploy app.smoke.yaml`. The project dispatch rules can route the service hostname's `/api` and `/clue` paths away from `clue-smoke`, so smoke proof should use the deployed version URL plus the `/smoke-clue` base path.

Required smoke secrets:

```text
clue-smoke-database-url
clue-smoke-secret-key
clue-smoke-admin-token
clue-openai-api-key
```

The smoke database secret must point at an isolated smoke database such as `clue_smoke`, never the production `clue` database.

## Smoke Writes

On `clue-smoke`, create a table with LLM seats and verify:

- `/api/v1/games` can create a new game.
- LLM seats can run against OpenAI.
- durable `nhp_memory`, `nhp_relationships`, and `nhp_notes` rows appear in Admin Mode.
- normal player snapshots do not contain `memory_context`.

## Production Read-Only Checks

Production URL:

```text
https://aix-labs.uw.r.appspot.com/clue/
```

Run only checks that do not create games or write events:

- service responds at `/clue/`
- `/clue/admin` renders the Superplayer Admin token entry screen
- `/clue/admin?admin_token=<token>` opens the Superplayer Admin dashboard; use the `clue-admin-token` clipboard command above
- `/clue/api/v1/admin/games` accepts the configured admin token
- existing saved-game/admin data is readable
- Secret Manager-backed config resolves at startup

Do not use production for write-based chatbot proof.
