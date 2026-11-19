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

### Cross-seam integration tests

`tests/integration/test_seam.py` runs against the real compose stack: both services, SQL Server and real HTTP between them. It lends and returns a tool across the seam, then stops each service with `docker compose stop` and checks that the other one answers a clean 503 within 10 seconds and keeps serving its own domain.

```bash
cp .env.example .env
docker compose up --build -d
pip install -r tests/integration/requirements.txt
pytest tests/integration -v
docker compose down -v
```

The tests stop and start the `catalog` and `loans` containers themselves, so do not run them against a stack someone is using.

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request:

1. **test**, once per service: install `requirements-dev.txt`, run `ruff check` and `ruff format --check`, validate `services/<name>/openapi.yaml` with `openapi-spec-validator`, start a throwaway SQL Server container with a random masked SA password, and run `pytest --cov`. The job fails below 70% coverage. CI never connects to Azure SQL.
2. **integration**, once: write a `.env` with random masked passwords, start the compose stack and run `tests/integration`. The `.env` is never committed and is not a GitHub secret.
3. **images**, once per service, only if `test` and `integration` passed: build the image from `services/<name>/Dockerfile`. A failed build fails the run.
4. **publish**, only on a push to `main`: the same `images` job logs in to GHCR with the built-in `GITHUB_TOKEN` and pushes the image, tagged with the commit SHA and `latest`.

Images:

```bash
docker pull ghcr.io/velkosska/lend-catalog:<commit-sha>
docker pull ghcr.io/velkosska/lend-loans:<commit-sha>
```

Pull requests only build. Nothing is pushed until the change is merged to `main`.

## First-time Azure setup

Everything is deployed into one resource group in my own Azure for Students subscription, and everything is named with the prefix `mvelkoska`. The resource group `rg-mvelkoska`, the SQL server `mvelkoska` and the storage account `mvelkoska` (static website, `https://mvelkoska.z28.web.core.windows.net`) were created first from the course guide. [`infra/setup.sh`](infra/setup.sh) does the rest, once. After it has run, nothing is created or changed by hand: the pipeline deploys.

| Resource | Name | Cost model |
|---|---|---|
| Resource group | `rg-mvelkoska` (already existed) | free |
| Managed identity for GitHub Actions | `mvelkoska-github` | free |
| Container Apps environment (and its Log Analytics workspace) | `mvelkoska-env` | pay per use, logs free up to 5 GB a month |
| Container apps, 0 to 1 replica | `mvelkoska-catalog`, `mvelkoska-loans` | pay per use, nothing while idle |
| SQL server | `mvelkoska` | free |
| Serverless databases (free offer, General Purpose Gen5, 0.5 to 1 vCore, local backups) | `mvelkoska-catalog-db`, `mvelkoska-loans-db` | free offer, pauses after 60 minutes idle |
| Storage account with static website (the UI) | `mvelkoska` | pay per use |

Requirements on your machine: Azure CLI (`az login` into the Azure for Students subscription), GitHub CLI (`gh auth login`) and Docker running.

The SQL server must allow SQL logins, because each service connects with its own username and password. The server from the guide was created with Microsoft Entra-only authentication, so before the first run I turned that off (`az sql server ad-only-auth disable -g rg-mvelkoska -n mvelkoska`) and set a password for the server admin in the portal. The script asks for that password once.

```bash
PREFIX=mvelkoska RESOURCE_GROUP=rg-mvelkoska RENAME_DB_FROM=free-sql-db-3497981 \
  ./infra/setup.sh
```

What the script does, in order. Each step checks first and skips what already exists, so a second run changes nothing:

1. Refuses to run unless `PREFIX` and `RESOURCE_GROUP` are set, the active subscription is Azure for Students, and the resource group exists. It never creates a resource group.
2. Registers the providers `Microsoft.App`, `Microsoft.OperationalInsights` and `Microsoft.ManagedIdentity`. They are off by default on a student subscription and Container Apps cannot be created without them.
3. Creates the managed identity `mvelkoska-github` with a federated credential for `repo:velkosska/assignment-2-devops:ref:refs/heads/main`, so only workflow runs on `main` of this repository can sign in as it (OpenID Connect, no password anywhere). It gets Contributor on the resource group and nothing wider.
4. If `PROFESSOR_ID` is set, gives the professor Reader on the resource group. Run the script again with it before submitting if it was not known the first time.
5. Renames the database from the guide to `mvelkoska-catalog-db`, creates `mvelkoska-loans-db`, and makes sure both are on the free offer with 0.5 to 1 vCore and locally redundant backups. The server from the guide had its public endpoint switched off, which blocks every firewall rule, so the script switches it on (the Container Apps reach the database through it) and allows Azure services through the firewall.
6. Opens the firewall for one IP only while it runs (the public IP the database sees, read from inside the container that connects, because it can differ from your machine's own). It does this early, because a new rule can take a few minutes to spread, and it keeps retrying for up to 5 minutes while the rule spreads or a paused database resumes. It asked for the SQL admin password at the start (not shown, not saved). Then it creates one login per service, `catalog_user` and `loans_user`, each with access to its own database only. It then proves this by connecting as each login and failing to open the other database. This step runs inside the `lend-catalog` image, which already contains `pymssql`, so nothing is installed on your machine.
7. Creates the Container Apps environment and the two apps, from the public `latest` images, with minimum 0 and maximum 1 replica, so migrations run at startup never run twice at once. The pipeline replaces the image with the commit SHA and sets every secret and variable.
8. Checks that the storage account's static website is enabled.
9. Stores the pipeline's secrets in GitHub with `gh secret set`, reading the values from standard input: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `CATALOG_DB_URL` and `LOANS_DB_URL`. The generated database passwords are never printed, written to a file or put in a command line.

An existing login is left alone, so a second run does not break the secret that is already in GitHub. To replace a password (for example after a leak), run it again with `ROTATE=1`: it changes the password in Azure and in the GitHub secret together.

## Assignment 1

The Assignment 1 monolith (`app.py`, `lend/`, `templates/`, one SQLite file) was replaced by the split above. It is still in the Git history at the commit "Import the Assignment 1 monolith as the starting point". ADR-1 to ADR-5 describe it.
