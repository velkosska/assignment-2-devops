"""Check that system.yaml matches the schema AND the code.

The schema only checks the shape of the file. These tests check that what the file
says is true: the variables each service really reads, the calls it really makes,
and the names of the Azure resources.
"""

import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = yaml.safe_load((ROOT / "system.yaml").read_text())
SCHEMA = yaml.safe_load((ROOT / "system.schema.json").read_text())
SERVICES = list(SYSTEM["services"])
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# os.environ.get("NAME") or os.environ.get("NAME", "default") in the service code.
ENV_READ = re.compile(r"""os\.environ\.get\(\s*"([A-Z][A-Z0-9_]*)"\s*(?:,\s*"([^"]*)")?\s*\)""")


def code_env(service):
    """Every variable the service code reads, with its default (None when it has none)."""
    found = {}
    for path in (ROOT / SYSTEM["services"][service]["path"]).glob("*.py"):
        for name, default in ENV_READ.findall(path.read_text()):
            found[name] = default or found.get(name)
    return found


def openapi_operations(service):
    spec = yaml.safe_load((ROOT / SYSTEM["services"][service]["openapi"]).read_text())
    return {
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def test_system_yaml_is_valid_against_the_schema():
    errors = sorted(Draft202012Validator(SCHEMA).iter_errors(SYSTEM), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors)


@pytest.mark.parametrize("service", SERVICES)
def test_env_lists_exactly_the_variables_the_code_reads(service):
    listed = set(SYSTEM["services"][service]["env"])
    read = set(code_env(service))
    assert read - listed == set(), "the code reads these, system.yaml does not list them"
    assert listed - read == set(), "system.yaml lists these, the code never reads them"


@pytest.mark.parametrize("service", SERVICES)
def test_env_defaults_and_secrets_match_the_code(service):
    for name, default in code_env(service).items():
        entry = SYSTEM["services"][service]["env"][name]
        if default is None:
            assert entry.get("secret") is True, f"{name} has no default in the code, so it must be a secret"
        else:
            assert not entry.get("secret"), f"{name} has a default in the code, so it is not a secret"
            assert str(entry["default"]) == default, (
                f"{name}: system.yaml says {entry['default']}, code says {default}"
            )


@pytest.mark.parametrize("service", SERVICES)
def test_calls_use_a_listed_url_variable_and_endpoints_that_exist(service):
    for call in SYSTEM["services"][service].get("calls", []):
        assert call["service"] in SERVICES
        assert call["url_env"] in SYSTEM["services"][service]["env"]
        missing = set(call["endpoints"]) - openapi_operations(call["service"])
        assert missing == set(), f"not in the openapi.yaml of {call['service']}"


def test_services_are_the_compose_services_that_are_built():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    built = {name for name, service in compose["services"].items() if "build" in service}
    assert built == set(SERVICES)
    assert set(SYSTEM["ui"]["calls"]) <= set(SERVICES)


@pytest.mark.parametrize("service", SERVICES)
def test_azure_names_follow_the_prefix(service):
    prefix = SYSTEM["prefix"]
    azure = SYSTEM["services"][service]["azure"]
    assert azure["container_app"] == f"{prefix}-{service}"
    assert azure["sql_database"] == f"{prefix}-{service}-db"
    assert SYSTEM["student"].replace(".", "-").startswith(prefix)
    assert SYSTEM["public_url"].startswith(f"https://{SYSTEM['ui']['storage_account']}.")


@pytest.mark.parametrize("service", SERVICES)
def test_paths_in_system_yaml_exist(service):
    entry = SYSTEM["services"][service]
    assert (ROOT / entry["openapi"]).is_file()
    assert (ROOT / entry["path"] / "Dockerfile").is_file()
    assert list((ROOT / entry["migrations"]).glob("*.sql")), "no migrations found"
    assert (ROOT / SYSTEM["ui"]["path"]).is_dir()
