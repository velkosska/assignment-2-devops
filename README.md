# Lend

A one-desk tool library for a volunteer workshop, split into two services along the seam of ADR-2:

| Part | Folder | Owns | Local URL |
|---|---|---|---|
| catalog service | `services/catalog` | `tools` (database `catalog`) | http://localhost:8001 |
| loans service | `services/loans` | `members`, `loans` (database `loans`) | http://localhost:8002 |
| static UI | `ui/` | nothing, it calls both services from the browser | http://localhost:8080 |

Each service is a Flask JSON API in its own container with its own SQL Server database and login. They talk to each other over HTTP only:

- Borrow and repeat: loans calls `GET {CATALOG_URL}/tools/{id}` to check that the tool exists and is not retired.
- Retire: catalog calls `GET {LOANS_URL}/loans?tool_id={id}` to refuse retiring a tool that is out.

Every outbound call has a 2 second timeout. If the other service is down, the call answers `503` with a clear message and writes nothing. The service's own endpoints and `/health` keep working.

## Run the whole system locally

Requirements: Docker Desktop. On Apple Silicon, enable *Settings → General → Use Rosetta for x86_64/amd64 emulation*, because the SQL Server image is amd64 only.

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:8080. Compose starts the services in this order:

1. `sqlserver` (`mcr.microsoft.com/mssql/server:2022-latest`) becomes healthy once `sqlcmd ... SELECT 1` answers.
2. `db-init` runs `db/init.sql` and exits. The script is idempotent and creates the databases `catalog` and `loans` and the logins `catalog_user` and `loans_user`, each with access to its own database only.
3. `catalog` and `loans` apply their pending migrations (`services/<name>/migrations/NNN_*.sql`, recorded in `schema_migrations`), then start gunicorn.
4. `ui` is stock `nginx:alpine` serving `ui/` read-only. It is not a service container.

Useful commands:

```bash
docker compose ps                 # what is running and healthy
docker compose logs -f loans      # one line per request: method, path, status, duration
curl localhost:8001/health        # {"status": "ok", "db": "ok", "version": "local"}
docker compose stop catalog       # partial failure: loans keeps serving, borrowing answers 503
docker compose start catalog
docker compose down               # keep the data volume
docker compose down -v            # also delete the database
```

## Configuration

| Variable | Service | Default | Meaning |
|---|---|---|---|
| `PORT` | both | `8000` | Port the service listens on |
| `DATABASE_URL` | both | none (secret) | `mssql://user:password@host:1433/database` |
| `ALLOWED_ORIGIN` | both | `http://localhost:8080` | The only origin allowed by CORS |
| `GIT_SHA` | both | `dev` | Version reported by `/health` |
| `DB_CONNECT_ATTEMPTS` | both | `10` | Connection attempts with exponential backoff |
| `LOANS_URL` | catalog | `http://loans:8000` | Base URL of the loans service |
| `LOANS_TIMEOUT_S` | catalog | `2` | Timeout for calls to loans |
| `CATALOG_URL` | loans | `http://catalog:8000` | Base URL of the catalog service |
| `CATALOG_TIMEOUT_S` | loans | `2` | Timeout for calls to catalog |

The UI reads the two service URLs from `ui/config.js`. The committed file points at localhost, and deployment will generate it.

## Run a single service

With the compose SQL Server running (`docker compose up -d sqlserver db-init`):

```bash
cd services/catalog
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
DATABASE_URL=mssql://catalog_user:Local-Catalog_Passw0rd@localhost:1433/catalog \
LOANS_URL=http://localhost:8002 python app.py
```

`python app.py` applies migrations and then serves on `PORT` (default 8000). The same works for `services/loans` with `loans_user`, database `loans` and `CATALOG_URL`.

## Tests and coverage

Tests run against a throwaway `*_test` database on the compose SQL Server. The other service is replaced by a stub function, so each service is tested on its own.

```bash
docker compose up -d sqlserver
cd services/catalog   # or services/loans
source .venv/bin/activate
TEST_DATABASE_URL=mssql://sa:Local-Sa_Passw0rd@localhost:1433/catalog_test \
  pytest --cov=. --cov-report=term-missing
ruff check . && ruff format --check .
```

Use `loans_test` as the database name for the loans service.

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request:

1. **test**, once per service: install `requirements-dev.txt`, run `ruff check` and `ruff format --check`, start a throwaway SQL Server container with a random masked SA password, and run `pytest --cov`. The job fails below 70% coverage. CI never connects to Azure SQL.
2. **build**, once per service, only if both test jobs passed: build the image from `services/<name>/Dockerfile`. A failed build fails the run.
3. **publish**, only on a push to `main`: the same build job logs in to GHCR with the built-in `GITHUB_TOKEN` and pushes the image, tagged with the commit SHA and `latest`.

Images:

```bash
docker pull ghcr.io/velkosska/lend-catalog:<commit-sha>
docker pull ghcr.io/velkosska/lend-loans:<commit-sha>
```

Pull requests only build. Nothing is pushed until the change is merged to `main`.

## Assignment 1

The Assignment 1 monolith (`app.py`, `lend/`, `templates/`, one SQLite file) was replaced by the split above. It is still in the Git history at the commit "Import the Assignment 1 monolith as the starting point". ADR-1 to ADR-5 describe it.
