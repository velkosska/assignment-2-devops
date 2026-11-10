import pytest
import requests

import loans_client
from app import create_app
from loans_client import LoansUnavailable


def loans_down(tool_id):
    raise LoansUnavailable("Loans service unavailable.")


@pytest.fixture
def client(db):
    return create_app(has_open_loan=lambda tool_id: False).test_client()


def test_health_reports_ok_with_the_version(db, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc123")
    response = create_app().test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "db": "ok", "version": "abc123"}


def test_health_is_503_when_the_database_is_unreachable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mssql://nobody:nothing@127.0.0.1:1/catalog")
    response = create_app().test_client().get("/health")
    assert response.status_code == 503
    assert response.get_json()["db"] == "unavailable"


def test_create_list_and_get_a_tool(client):
    created = client.post("/tools", json={"name": "Hammer", "category": "hand"})
    assert created.status_code == 201
    tool = created.get_json()
    assert client.get("/tools").get_json() == [tool]
    assert client.get(f"/tools/{tool['id']}").get_json() == tool


def test_create_tool_rejects_a_blank_name(client):
    response = client.post("/tools", json={"name": " ", "category": "hand"})
    assert response.status_code == 400
    assert "required" in response.get_json()["error"]


def test_unknown_tool_is_404(client):
    assert client.get("/tools/999999").status_code == 404
    assert client.post("/tools/999999/retire").status_code == 404


def test_retire_answers_409_when_the_tool_is_out(db):
    client = create_app(has_open_loan=lambda tool_id: True).test_client()
    tool = client.post("/tools", json={"name": "Hammer", "category": "hand"}).get_json()
    assert client.post(f"/tools/{tool['id']}/retire").status_code == 409


def test_retire_answers_503_when_loans_is_down(db):
    client = create_app(has_open_loan=loans_down).test_client()
    tool = client.post("/tools", json={"name": "Hammer", "category": "hand"}).get_json()
    response = client.post(f"/tools/{tool['id']}/retire")
    assert response.status_code == 503
    assert "Loans service unavailable" in response.get_json()["error"]
    assert client.get(f"/tools/{tool['id']}").get_json()["retired"] is False


def test_cors_allows_only_the_ui_origin(client, monkeypatch):
    allowed = client.get("/tools", headers={"Origin": "http://localhost:8080"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"
    other = client.get("/tools", headers={"Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in other.headers


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


def test_loans_client_sends_the_tool_id_with_a_timeout(monkeypatch):
    calls = {}

    def fake_get(url, params, timeout):
        calls.update(url=url, params=params, timeout=timeout)
        return FakeResponse([{"id": 1, "tool_id": 7}])

    monkeypatch.setenv("LOANS_URL", "http://loans.test")
    monkeypatch.setenv("LOANS_TIMEOUT_S", "2")
    monkeypatch.setattr(requests, "get", fake_get)
    assert loans_client.has_open_loan(7) is True
    assert calls == {"url": "http://loans.test/loans", "params": {"tool_id": 7}, "timeout": 2.0}


def test_loans_client_is_false_when_there_is_no_open_loan(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, params, timeout: FakeResponse([]))
    assert loans_client.has_open_loan(7) is False


@pytest.mark.parametrize(
    "failure",
    [requests.ConnectionError("refused"), requests.Timeout("slow")],
)
def test_loans_client_raises_loans_unavailable(monkeypatch, failure):
    def fake_get(url, params, timeout):
        raise failure

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(LoansUnavailable):
        loans_client.has_open_loan(7)


def test_loans_client_raises_on_a_server_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, params, timeout: FakeResponse({}, status=500))
    with pytest.raises(LoansUnavailable):
        loans_client.has_open_loan(7)
