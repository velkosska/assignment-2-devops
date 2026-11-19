#!/usr/bin/env bash
# One-time Azure setup for the Lend system. Run it yourself, from your terminal,
# after `az login`. It is idempotent: every step first checks whether the thing
# already exists, so running it twice changes nothing the second time.
#
# What it makes (all in the resource group you already have, which it never creates):
#   - the resource providers Container Apps needs (registered once)
#   - a managed identity <prefix>-github that GitHub Actions signs in as through
#     OpenID Connect (no password), trusted only for runs on main of this repo,
#     with Contributor on the resource group only
#   - two serverless databases on the free offer, one per service, plus one SQL
#     login per service that can open its own database only
#   - one Container Apps environment (Azure adds the Log Analytics workspace) and
#     two apps, scaling from 0 to at most 1 replica
#   - the static website of the UI storage account, if it is not enabled yet
#   - the GitHub Actions secrets the pipeline needs, set with `gh` so that no
#     secret is ever printed or written to a file
#
# Usage:
#   PREFIX=mvelkoska RESOURCE_GROUP=rg-mvelkoska RENAME_DB_FROM=free-sql-db-3497981 \
#     ./infra/setup.sh
#
# Optional variables:
#   PROFESSOR_ID   object id or sign-in name that gets Reader on the resource group
#   ROTATE=1       replace the password of logins that already exist (and update
#                  the GitHub secret). Without it, existing logins are left alone.
#   SQL_ADMIN_PASSWORD   otherwise the script asks for it, without echo
set -euo pipefail

# ---------------------------------------------------------------- configuration
PREFIX="${PREFIX:?set PREFIX, for example PREFIX=mvelkoska}"
RESOURCE_GROUP="${RESOURCE_GROUP:?set RESOURCE_GROUP, for example RESOURCE_GROUP=rg-mvelkoska}"
GITHUB_REPO="${GITHUB_REPO:-velkosska/assignment-2-devops}"
# The server and the storage account were created from the course guide before this
# script existed, so they carry the bare prefix as their name.
SQL_SERVER="${SQL_SERVER:-$PREFIX}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-$PREFIX}"
RENAME_DB_FROM="${RENAME_DB_FROM:-}"
PROFESSOR_ID="${PROFESSOR_ID:-}"
ROTATE="${ROTATE:-0}"

IDENTITY="${PREFIX}-github"
ENVIRONMENT="${PREFIX}-env"
REGISTRY="ghcr.io/${GITHUB_REPO%%/*}"
IMAGE="${REGISTRY}/lend-catalog:latest" # has pymssql: used to talk to the database
SQL_FQDN="${SQL_SERVER}.database.windows.net"
SERVICES="catalog loans"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! "$PREFIX" =~ ^[a-z0-9-]+$ ]]; then
  echo "PREFIX may only contain lowercase letters, digits and hyphens." >&2
  exit 1
fi

step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
quiet() { "$@" >/dev/null 2>&1; }

# ------------------------------------------------------------------ preflight
step "Checking the tools and the Azure subscription"
for tool in az gh docker openssl awk; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first: the script stores the pipeline's secrets with gh." >&2; exit 1; }

SUBSCRIPTION_NAME="$(az account show --query name -o tsv)"
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
case "$SUBSCRIPTION_NAME" in
  *"Azure for Students"*) note "subscription: $SUBSCRIPTION_NAME" ;;
  *)
    echo "The active subscription is '$SUBSCRIPTION_NAME', not Azure for Students." >&2
    echo "Run: az account set --subscription <your Azure for Students id>" >&2
    exit 1
    ;;
esac

# The resource group must already exist. This script never creates one.
LOCATION="$(az group show -n "$RESOURCE_GROUP" --query location -o tsv)"
RG_ID="$(az group show -n "$RESOURCE_GROUP" --query id -o tsv)"
note "resource group: $RESOURCE_GROUP ($LOCATION)"
az sql server show -g "$RESOURCE_GROUP" -n "$SQL_SERVER" --query name -o tsv >/dev/null
az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query name -o tsv >/dev/null

# Ask for the SQL admin password now, so the rest runs without waiting for you.
SQL_ADMIN_USER="$(az sql server show -g "$RESOURCE_GROUP" -n "$SQL_SERVER" --query administratorLogin -o tsv)"
if [ -z "${SQL_ADMIN_PASSWORD:-}" ]; then
  [ -t 0 ] || { echo "Set SQL_ADMIN_PASSWORD or run the script from a terminal." >&2; exit 1; }
  read -r -s -p "    SQL admin password for $SQL_ADMIN_USER (not shown, not saved): " SQL_ADMIN_PASSWORD
  echo
fi

# ------------------------------------------------------------------ providers
step "Registering the resource providers (once)"
for namespace in Microsoft.App Microsoft.OperationalInsights Microsoft.ManagedIdentity; do
  state="$(az provider show -n "$namespace" --query registrationState -o tsv)"
  if [ "$state" = "Registered" ]; then
    note "$namespace: already registered"
  else
    note "$namespace: registering (this can take a few minutes)"
    az provider register -n "$namespace" --wait
  fi
done

# ------------------------------------------------------------------- identity
step "Managed identity $IDENTITY for GitHub Actions (OpenID Connect)"
if ! quiet az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY"; then
  az identity create -g "$RESOURCE_GROUP" -n "$IDENTITY" -l "$LOCATION" -o none
  note "created"
else
  note "already exists"
fi
CLIENT_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY" --query clientId -o tsv)"
PRINCIPAL_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY" --query principalId -o tsv)"

# Only workflow runs on the main branch of this repository can sign in as it.
SUBJECT="repo:${GITHUB_REPO}:ref:refs/heads/main"
if ! quiet az identity federated-credential show -g "$RESOURCE_GROUP" --identity-name "$IDENTITY" -n github-main; then
  az identity federated-credential create -g "$RESOURCE_GROUP" --identity-name "$IDENTITY" -n github-main \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "$SUBJECT" --audiences "api://AzureADTokenExchange" -o none
  note "trust created for: $SUBJECT"
else
  note "trust already exists for: $SUBJECT"
fi

# Contributor on this resource group only, nothing wider.
if [ "$(az role assignment list --assignee "$PRINCIPAL_ID" --scope "$RG_ID" --role Contributor --query 'length(@)' -o tsv)" = "0" ]; then
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role Contributor --scope "$RG_ID" -o none
  note "Contributor granted on $RESOURCE_GROUP"
else
  note "Contributor already granted on $RESOURCE_GROUP"
fi

if [ -n "$PROFESSOR_ID" ]; then
  if [ "$(az role assignment list --assignee "$PROFESSOR_ID" --scope "$RG_ID" --role Reader --query 'length(@)' -o tsv)" = "0" ]; then
    az role assignment create --assignee "$PROFESSOR_ID" --role Reader --scope "$RG_ID" -o none
    note "Reader granted to the professor"
  else
    note "the professor already has Reader"
  fi
else
  note "PROFESSOR_ID is not set: skipping the professor's Reader role (run again with it before submitting)"
fi

# ------------------------------------------------------------------- database
step "Serverless databases on $SQL_SERVER (free offer, General Purpose Gen5, 0.5 to 1 vCore, local backups)"
db_name() { printf '%s-%s-db' "$PREFIX" "$1"; }
db_exists() { quiet az sql db show -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$1"; }

# The server from the course guide has its public endpoint switched off, and Azure
# refuses every firewall rule while it is. The Container Apps reach the database
# through the public endpoint ("allow Azure services"), so it has to be on. The
# firewall below is what keeps everyone else out.
if [ "$(az sql server show -g "$RESOURCE_GROUP" -n "$SQL_SERVER" --query publicNetworkAccess -o tsv)" != "Enabled" ]; then
  az sql server update -g "$RESOURCE_GROUP" -n "$SQL_SERVER" --enable-public-network true -o none
  note "public network access: enabled (access is limited by the firewall rules)"
else
  note "public network access: already enabled"
fi

# Allow Azure services (the Container Apps reach the database this way).
az sql server firewall-rule create -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n AllowAllWindowsIps \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none
note "firewall: Azure services allowed"

# Open the firewall for the address that will really connect, early, because a new
# rule can take a few minutes to take effect. Only the container connects to the
# database, so ask the container for its public IP: it can differ from this
# machine's own (VPN, IPv6). The rule is removed when the script ends.
MY_IP="$(docker run --rm --platform linux/amd64 "$IMAGE" python -c \
  'import urllib.request; print(urllib.request.urlopen("https://api.ipify.org", timeout=15).read().decode())')"
[[ "$MY_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "Could not find the public IPv4 address (got '$MY_IP')." >&2; exit 1; }
TEMP_RULE="setup-${USER:-me}"
cleanup() { quiet az sql server firewall-rule delete -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$TEMP_RULE" || true; }
trap cleanup EXIT
az sql server firewall-rule create -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$TEMP_RULE" \
  --start-ip-address "$MY_IP" --end-ip-address "$MY_IP" -o none
note "firewall: $MY_IP is allowed until the script ends"

first_service="${SERVICES%% *}"
if [ -n "$RENAME_DB_FROM" ] && ! db_exists "$(db_name "$first_service")" && db_exists "$RENAME_DB_FROM"; then
  az sql db rename -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$RENAME_DB_FROM" --new-name "$(db_name "$first_service")" -o none
  note "renamed $RENAME_DB_FROM to $(db_name "$first_service")"
fi

for service in $SERVICES; do
  db="$(db_name "$service")"
  if db_exists "$db"; then
    note "$db: already exists"
  else
    az sql db create -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$db" -e GeneralPurpose -f Gen5 -c 1 \
      --compute-model Serverless --min-capacity 0.5 --backup-storage-redundancy Local \
      --use-free-limit --free-limit-exhaustion-behavior AutoPause -o none
    note "$db: created"
  fi
  # Bring a database made earlier (the renamed one) in line with the contract.
  settings="$(az sql db show -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$db" \
    --query '[currentSku.capacity, minCapacity, requestedBackupStorageRedundancy, useFreeLimit]' -o tsv | tr '\n' ' ')"
  read -r capacity min_capacity backup free_limit <<<"$settings"
  if awk -v a="$capacity" -v b="$min_capacity" 'BEGIN { exit !(a == 1 && b == 0.5) }' && [ "$backup" = "Local" ]; then
    note "$db: settings already match (max 1 vCore, min 0.5, local backups, free offer: $free_limit)"
  else
    az sql db update -g "$RESOURCE_GROUP" -s "$SQL_SERVER" -n "$db" -c 1 --min-capacity 0.5 \
      --backup-storage-redundancy Local -o none
    note "$db: settings updated to max 1 vCore, min 0.5, local backups"
  fi
done

# --------------------------------------------------------------------- logins
step "One SQL login per service, each with access to its own database only"
note "connecting can take up to 5 minutes: a paused database must resume and a new firewall rule must spread"

# Random URL-safe passwords. They only reach the container through its environment
# and GitHub through stdin: never the screen, never a file, never a command line.
PASSWORD_CATALOG="Lend-$(openssl rand -hex 16)"
PASSWORD_LOANS="Lend-$(openssl rand -hex 16)"
export SQL_FQDN SQL_ADMIN_USER SQL_ADMIN_PASSWORD ROTATE PASSWORD_CATALOG PASSWORD_LOANS
SERVICES_SPEC="catalog:$(db_name catalog) loans:$(db_name loans)"

# infra/create_logins.py runs inside the services' own image, which already has
# pymssql, so nothing has to be installed on this machine. Only its stdout (status
# lines, never a password) is captured; progress goes to the screen on stderr.
RESULTS="$(
  docker run --rm -i --platform linux/amd64 \
    -e SQL_FQDN -e SQL_ADMIN_USER -e SQL_ADMIN_PASSWORD -e ROTATE \
    -e PASSWORD_CATALOG -e PASSWORD_LOANS -e "SERVICES=$SERVICES_SPEC" \
    "$IMAGE" python - <"$SCRIPT_DIR/create_logins.py"
)"
field() { printf '%s\n' "$RESULTS" | awk -v kind="$1" -v service="$2" '$1 == kind && $2 == service { print $3 }'; }

set_secret() { printf '%s' "$2" | gh secret set "$1" --repo "$GITHUB_REPO" >/dev/null; note "GitHub secret $1 set"; }

for service in $SERVICES; do
  result="$(field RESULT "$service")"
  case "$result" in
    created | rotated)
      user="${service}_user"
      [ "$(field LOGIN_FORM "$service")" = "at-server" ] && user="${user}%40${SQL_SERVER}"
      secret_name="$(printf '%s' "$service" | tr '[:lower:]' '[:upper:]')_DB_URL"
      password_var="PASSWORD_$(printf '%s' "$service" | tr '[:lower:]' '[:upper:]')"
      set_secret "$secret_name" "mssql://${user}:${!password_var}@${SQL_FQDN}:1433/$(db_name "$service")"
      note "login ${service}_user $result and verified: it opens $(db_name "$service") and nothing else"
      ;;
    exists)
      note "login ${service}_user already exists: left unchanged (use ROTATE=1 to replace its password)"
      ;;
    *)
      echo "Unexpected result for $service: '$result'" >&2
      exit 1
      ;;
  esac
done
unset SQL_ADMIN_PASSWORD PASSWORD_CATALOG PASSWORD_LOANS

# ------------------------------------------------------------- container apps
step "Container Apps environment $ENVIRONMENT and two apps (0 to 1 replica)"
if ! quiet az containerapp env show -g "$RESOURCE_GROUP" -n "$ENVIRONMENT"; then
  # Azure creates the Log Analytics workspace for the logs here. Keep it.
  az containerapp env create -g "$RESOURCE_GROUP" -n "$ENVIRONMENT" -l "$LOCATION" -o none
  note "environment created (with its Log Analytics workspace)"
else
  note "environment already exists"
fi

for service in $SERVICES; do
  app="${PREFIX}-${service}"
  if quiet az containerapp show -g "$RESOURCE_GROUP" -n "$app"; then
    note "$app: already exists"
  else
    # A first image so the app exists. The pipeline replaces it with the SHA-tagged
    # image and sets every secret and variable. Nothing runs at zero replicas.
    az containerapp create -g "$RESOURCE_GROUP" -n "$app" --environment "$ENVIRONMENT" \
      --image "${REGISTRY}/lend-${service}:latest" \
      --ingress external --target-port 8000 \
      --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi \
      --revisions-mode single -o none
    note "$app: created"
  fi
done

# -------------------------------------------------------------------- storage
step "Static website on the storage account $STORAGE_ACCOUNT"
enabled="$(az storage blob service-properties show --account-name "$STORAGE_ACCOUNT" --auth-mode key \
  --query staticWebsite.enabled -o tsv)"
if [ "$enabled" = "true" ]; then
  note "static website already enabled"
else
  az storage blob service-properties update --account-name "$STORAGE_ACCOUNT" --auth-mode key \
    --static-website --index-document index.html --404-document index.html -o none
  note "static website enabled"
fi
UI_URL="$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query primaryEndpoints.web -o tsv)"

# ------------------------------------------------------------ github secrets
step "GitHub Actions secrets for the pipeline ($GITHUB_REPO)"
set_secret AZURE_CLIENT_ID "$CLIENT_ID"
set_secret AZURE_TENANT_ID "$TENANT_ID"
set_secret AZURE_SUBSCRIPTION_ID "$SUBSCRIPTION_ID"

# -------------------------------------------------------------------- summary
step "Done"
note "UI (public URL):  ${UI_URL%/}"
for service in $SERVICES; do
  fqdn="$(az containerapp show -g "$RESOURCE_GROUP" -n "${PREFIX}-${service}" --query properties.configuration.ingress.fqdn -o tsv)"
  note "${service}: https://${fqdn}"
done
note "Next: the pipeline deploys the images and the UI. Nothing else is done by hand."
