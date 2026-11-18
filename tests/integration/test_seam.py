"""Cross-seam tests against the real docker compose stack.

Start it first (see README): `docker compose up --build -d`. These tests talk to
both services over HTTP and stop and start containers to check partial failure.
Every test uses unique names, so the databases do not need to be empty.
"""

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import requests

CATALOG = os.environ.get("CATALOG_URL", "http://localhost:8001").rstrip("/")
LOANS = os.environ.get("LOANS_URL", "http://localhost:8002").rstrip("/")
REPO_ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT_S = 150
# The services time out calls to each other after 2 s. The browser-facing request
# must come back well before a user would think it hung.
FAIL_FAST_S = 10


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=REPO_ROOT, check=True, capture_output=True)


def wait_until_healthy(base_url, timeout=STARTUP_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    last = "no answer"
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=3)
            if response.status_code == 200:
                return response.json()
            last = f"HTTP {response.status_code}"
        except requests.RequestException as error:
            last = str(error)
        time.sleep(2)
    pytest.fail(f"{base_url}/health was not 200 after {timeout}s: {last}")


@pytest.fixture(scope="session", autouse=True)
def stack_is_up():
    for base_url in (CATALOG, LOANS):
        health = wait_until_healthy(base_url)
        expected_sha = os.environ.get("GIT_SHA")
        if expected_sha:
            assert health["version"] == expected_sha


def unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def new_tool():
    response = requests.post(
        f"{CATALOG}/tools", json={"name": unique("Hammer"), "category": "Hand tools"}, timeout=10
    )
    assert response.status_code == 201
    return response.json()


def new_member():
    response = requests.post(
        f"{LOANS}/members",
        json={"name": "Ada", "email": f"{unique('ada')}@example.com"},
        timeout=10,
    )
    assert response.status_code == 201
    return response.json()


def borrow(tool_id, member_id):
    return requests.post(f"{LOANS}/loans", json={"tool_id": tool_id, "member_id": member_id}, timeout=30)


def retire(tool_id):
    return requests.post(f"{CATALOG}/tools/{tool_id}/retire", timeout=30)


def open_loans(tool_id):
    response = requests.get(f"{LOANS}/loans", params={"tool_id": tool_id}, timeout=10)
    assert response.status_code == 200
    return response.json()


def test_lend_and_return_across_the_seam():
    tool = new_tool()
    member = new_member()

    created = borrow(tool["id"], member["id"])
    assert created.status_code == 201
    loan_id = created.json()["id"]

    # catalog asks loans, and loans says the tool is out
    out = retire(tool["id"])
    assert out.status_code == 409
    assert "out on loan" in out.json()["error"]

    assert requests.post(f"{LOANS}/loans/{loan_id}/return", timeout=10).status_code == 200
    assert open_loans(tool["id"]) == []

    # now loans says nobody has it, so catalog retires it
    retired = retire(tool["id"])
    assert retired.status_code == 200
    assert retired.json()["retired"] is True

    # loans asks catalog, and catalog says the tool is retired
    assert borrow(tool["id"], member["id"]).status_code == 409


def test_borrowing_an_unknown_tool_is_refused():
    member = new_member()
    assert borrow(999_999_999, member["id"]).status_code == 409


def test_catalog_down_borrow_fails_cleanly():
    tool = new_tool()
    member = new_member()
    compose("stop", "catalog")
    try:
        started = time.monotonic()
        response = borrow(tool["id"], member["id"])
        elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert "Catalog service unavailable" in response.json()["error"]
        assert elapsed < FAIL_FAST_S

        # nothing was written, and loans keeps serving its own domain
        assert open_loans(tool["id"]) == []
        assert requests.get(f"{LOANS}/health", timeout=10).status_code == 200
        assert requests.get(f"{LOANS}/members", timeout=10).status_code == 200
    finally:
        compose("start", "catalog")
        wait_until_healthy(CATALOG)

    # the catalog data survived, and the flow works again
    assert borrow(tool["id"], member["id"]).status_code == 201


def test_loans_down_retire_fails_cleanly():
    tool = new_tool()
    compose("stop", "loans")
    try:
        started = time.monotonic()
        response = retire(tool["id"])
        elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert "Loans service unavailable" in response.json()["error"]
        assert elapsed < FAIL_FAST_S

        # the tool was not retired, and catalog keeps serving its own domain
        assert requests.get(f"{CATALOG}/tools/{tool['id']}", timeout=10).json()["retired"] is False
        assert requests.get(f"{CATALOG}/health", timeout=10).status_code == 200
        assert requests.get(f"{CATALOG}/tools", timeout=10).status_code == 200
    finally:
        compose("start", "loans")
        wait_until_healthy(LOANS)
