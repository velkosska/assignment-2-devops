import re
from pathlib import Path

import yaml

from app import create_app

SPEC = Path(__file__).parent.parent / "openapi.yaml"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def documented_operations():
    spec = yaml.safe_load(SPEC.read_text())
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def implemented_operations():
    operations = set()
    for rule in create_app().url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule.rule)
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            operations.add((method, path))
    return operations


def test_openapi_document_lists_exactly_the_routes_in_the_code():
    documented = documented_operations()
    implemented = implemented_operations()
    assert implemented - documented == set(), "in the code but not in openapi.yaml"
    assert documented - implemented == set(), "in openapi.yaml but not in the code"
