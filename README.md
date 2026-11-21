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
2. `db-init` runs `db/init.sql` and exits. The script is idempotent and creates the databases `catalog` and `loans` and the logins `catalog_user` and `loans_user`, each with access to its own database only and with only the roles `db_ddladmin`, `db_datareader` and `db_datawriter` there (the same as on Azure).
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

The UI reads the two service URLs from `ui/config.js`. The committed file points at localhost, and the deploy ui job writes the Azure addresses into the copy it uploads.

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

## Database migrations

Each service owns the schema of its own database as numbered files in `services/<name>/migrations/`. At startup the runner (`migrate.py`) creates `schema_migrations` if needed, applies every file that is not recorded there, in number order, and records each one in the same transaction. A file that fails is rolled back whole, and the service does not start. A second start logs `no pending migrations`. Files that were applied are never edited: a correction is a new file. There are no down migrations: the recovery plan is in ADR-7.

| Service | Migration | What it does to data that already exists |
|---|---|---|
| catalog | `001_init.sql` | creates `tools` |
| catalog | `002_add_retired_at.sql` | adds the nullable column `tools.retired_at` and gives the tools that are already retired their `created_at` as the best date known. Every row keeps its id and values. |
| loans | `001_init.sql` | creates `members` and `loans` |

`services/catalog/tests/test_migrations.py` proves 002 keeps data: it builds a separate database at version 001, inserts tools (two of them retired), lets the real runner apply what is pending, and checks the ids and values are unchanged, the retired tools are backfilled, the others stay empty, each migration is recorded once, and a failing migration leaves no half-applied column behind.

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
3. **system**, once: validate [`system.yaml`](system.yaml) against the provided [`system.schema.json`](system.schema.json) and check it against the code (see below).
4. **images**, once per service, only if `test`, `integration` and `system` passed: build the image from `services/<name>/Dockerfile`. A failed build fails the run.
5. **publish**, only on a push to `main`: the same `images` job logs in to GHCR with the built-in `GITHUB_TOKEN` and pushes the image, tagged with the commit SHA and `latest`.
6. **deploy services**, only on a push to `main`, after `images`: see "Deployment to Azure" below.
7. **deploy ui**, only on a push to `main`, after `deploy services`: publishes the UI to the storage account's static website.
8. **verify deployment**, only on a push to `main`, after both deploy jobs: checks that what runs on Azure is this commit.

### System description

[`system.yaml`](system.yaml) describes every service, every environment variable it reads, its calls across the seam and its Azure resources, in the format of the provided `system.schema.json` (copied unchanged). Each service's endpoints are in `services/<name>/openapi.yaml`. A schema only checks the shape of a file, so `tests/system/test_system.py` also checks that the file tells the truth:

- the variables listed for a service are exactly the ones its code reads, a variable with a default in the code has the same default in the file, and a variable without one is marked `secret: true`;
- every endpoint under `calls` exists in the other service's `openapi.yaml`, and the `url_env` is a listed variable;
- the services are the ones `docker-compose.yml` builds, and the Azure names are the prefix plus the service name.

```bash
pip install -r tests/system/requirements.txt
pytest tests/system -v
```

`timeout_s` under `calls` is the deployed value (10 seconds). The code default is 2 seconds (`LOANS_TIMEOUT_S`, `CATALOG_TIMEOUT_S`) and the pipeline sets 10 on Azure so that a cold start does not fail the call.

Images:

```bash
docker pull ghcr.io/velkosska/lend-catalog:<commit-sha>
docker pull ghcr.io/velkosska/lend-loans:<commit-sha>
```

Pull requests only build. Nothing is pushed until the change is merged to `main`.

### Deployment to Azure

On a push to `main`, after every job above passed for that same commit, the **deploy services** job (`needs: [images]`) deploys to Azure with no manual step. It never runs on a pull request.

1. **Sign in with OpenID Connect.** `azure/login` exchanges the run's GitHub token for an Azure token as the managed identity `mvelkoska-github`. That identity trusts only runs on `main` of this repository and has Contributor on `rg-mvelkoska` only. No Azure password exists anywhere, and the job has `id-token: write` and nothing else beyond reading the repository.
2. **Set the database secret.** `az containerapp secret set` stores `CATALOG_DB_URL` or `LOANS_DB_URL` (GitHub secrets, written by `infra/setup.sh`) as a Container Apps secret. The app only receives `DATABASE_URL=secretref:database-url`, never the value as a plain environment variable.
3. **Update the app.** `az containerapp update` points the app at `ghcr.io/velkosska/lend-<service>:<commit-sha>`, the image CI built and tested for this commit, and sets the other variables: `PORT`, `ALLOWED_ORIGIN` (the UI's static website, the only origin allowed by CORS), `GIT_SHA` (so `/health` reports the running version), and the other service's URL and a 10 second timeout, read from Azure and not hardcoded. It also sets 0 to 1 replicas on every deploy, so a migration at startup never runs twice at once.

Only one deployment runs at a time (`concurrency: deploy`, without cancelling), and a newer push to `main` does not cancel a run in progress. Do not add `environment:` to the job: it would change the token's subject and Azure would refuse the sign-in.

Then the **deploy ui** job (`needs: [deploy-services]`, also only on `main`) publishes the UI:

1. It signs in the same way, copies `ui/` to a temporary folder and writes the `config.js` of that copy, with the two service addresses read from Azure (`az containerapp show`). The committed `ui/config.js` stays the localhost one used by docker compose.
2. `az storage blob upload-batch` uploads the copy to the `$web` container of the storage account, with `Cache-Control: no-cache` so a visitor never sees an old page next to a new API. The identity is Contributor on the resource group, which lets the command read the storage account key at that moment. No key is stored anywhere, and the identity holds no blob data role.

The UI then lives at https://mvelkoska.z28.web.core.windows.net, which is also the only origin the services accept.

Last, the **verify deployment** job (`needs: [deploy-services, deploy-ui]`) checks the result, so a deploy that starts a broken app, or leaves an older revision serving, turns the run red instead of looking green:

1. The UI's `index.html` is served and its `config.js` names both service addresses.
2. For each service, `/health` is asked until it answers `200` with `status` and `db` both `ok` and a `version` equal to the commit being deployed (`github.sha`). That is what proves the new revision is the one serving. The request also carries `Origin` set to the UI, and the answer must allow exactly that origin.
3. It keeps asking for up to 10 minutes per service (`HEALTH_WAIT_S`), because a service scaled to zero has to start its container and wake a paused database first, which takes about a minute. On failure it prints the last answer. Either way it writes a table with the versions it found in the run summary.

**Waking services.** On Azure a service that has been idle is scaled to zero and its database may be paused, so the first request can take about a minute. Azure answers it with `503 App port is not ready` after about 30 seconds, without CORS headers, so the browser reports a failed fetch. `ui/app.js` therefore waits up to 35 seconds per attempt, tries a read up to three times, and shows "Catalog is starting up after a quiet period" while a request is slow. Writes are never repeated automatically, because one that got no answer may have happened. A 503 with a JSON `error` is the service reporting that the other one is down, which is a real answer and is shown as it is.

| GitHub secret | Holds | Written by |
|---|---|---|
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Identifiers of the managed identity and subscription, not passwords | `infra/setup.sh` |
| `CATALOG_DB_URL`, `LOANS_DB_URL` | Connection string of each service's own database | `infra/setup.sh` (random password, never printed) |

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
3. Creates the managed identity `mvelkoska-github` with a federated credential for `repo:velkosska@139571183/assignment-2-devops@1453003403:ref:refs/heads/main`, so only workflow runs on `main` of this repository can sign in as it (OpenID Connect, no password anywhere). It gets Contributor on the resource group and nothing wider. The numbers are the owner id and the repository id, which the script reads from GitHub: GitHub puts them in the token's subject, so a trust written with names only never matches and Azure answers `AADSTS700213`. If an older run left the name-only form, the script corrects it.
4. If `PROFESSOR_ID` is set, gives the professor Reader on the resource group. I did it by hand on 2026-11-20 with `az role assignment create --assignee amartini@faculty.ie.edu --role Reader --scope "$RG_ID"` (`RG_ID` is the id of `rg-mvelkoska`), which does the same as this step.
5. Renames the database from the guide to `mvelkoska-catalog-db`, creates `mvelkoska-loans-db`, and makes sure both are on the free offer with 0.5 to 1 vCore and locally redundant backups. The server from the guide had its public endpoint switched off, which blocks every firewall rule, so the script switches it on (the Container Apps reach the database through it) and allows Azure services through the firewall.
6. Opens the firewall for one IP only while it runs (the public IP the database sees, read from inside the container that connects, because it can differ from your machine's own). It does this early, because a new rule can take a few minutes to spread, and it keeps retrying for up to 5 minutes while the rule spreads or a paused database resumes. It asked for the SQL admin password at the start (not shown, not saved). Then it creates one login per service, `catalog_user` and `loans_user`, each with access to its own database only. It then proves this by connecting as each login and failing to open the other database. Each user gets only `db_ddladmin`, `db_datareader` and `db_datawriter` in its own database: enough to run its migrations at startup and to read and write its data, but not `db_owner`, which would also let the service manage users and role members. A user that an earlier run made `db_owner` is corrected, so a second run upgrades the live databases. The script checks the result by acting as each user from the admin connection: it must be able to create a table, insert and select, and must not be able to create a user. This step runs inside the `lend-catalog` image, which already contains `pymssql`, so nothing is installed on your machine.
7. Creates the Container Apps environment and the two apps, from the public `latest` images, with minimum 0 and maximum 1 replica, so migrations run at startup never run twice at once. The pipeline replaces the image with the commit SHA and sets every secret and variable.
8. Checks that the storage account's static website is enabled.
9. Stores the pipeline's secrets in GitHub with `gh secret set`, reading the values from standard input: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `CATALOG_DB_URL` and `LOANS_DB_URL`. The generated database passwords are never printed, written to a file or put in a command line.

An existing login is left alone, so a second run does not break the secret that is already in GitHub. To replace a password (for example after a leak), run it again with `ROTATE=1`: it changes the password in Azure and in the GitHub secret together.

## Assignment 1

The Assignment 1 monolith (`app.py`, `lend/`, `templates/`, one SQLite file) was replaced by the split above. It is still in the Git history at the commit "Import the Assignment 1 monolith as the starting point". ADR-1 to ADR-5 describe it.
