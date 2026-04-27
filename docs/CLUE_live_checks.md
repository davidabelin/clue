# Clue v1.8.0 Live Checks

Use these checks when validating the live chatbot path. Write-based proof belongs on the smoke service only; production checks are read-only.

## Local OpenAI + SQLite Smoke

```powershell
pip install -r requirements.txt
$env:CLUE_DB_PATH = "$PWD\data\clue-live-smoke.db"
$env:CLUE_ADMIN_TOKEN = "local-admin"
$env:OPENAI_API_KEY = "<local key or omit when using OPENAI_API_KEY_SECRET_VERSION>"
python run.py
```

Create a table with at least one LLM seat, confirm LLM turns fail loudly when credentials are missing, and confirm live LLM seats can chat/act when credentials are present.

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
openai-api-key
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
- `/clue/admin` rejects missing/invalid admin tokens
- `/clue/api/v1/admin/games` accepts the configured admin token
- existing saved-game/admin data is readable
- Secret Manager-backed config resolves at startup

Do not use production for write-based chatbot proof.
