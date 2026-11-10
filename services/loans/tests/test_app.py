import pytest
import requests

import catalog_client
from app import create_app
from catalog_client import CatalogUnavailable


@pytest.fixture
def client(db, catalog):
    return create_app(get_tool=catalog.get_tool).test_client()


def add_member(client, email="ada@example.com"):
    return client.post("/members", json={"name": "Ada", "email": email}).get_json()


def test_health_reports_ok_with_the_version(db, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc123")
    response = create_app().test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "db": "ok", "version": "abc123"}


def test_health_is_503_when_the_database_is_unreachable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mssql://nobody:nothing@127.0.0.1:1/loans")
    response = create_app().test_client().get("/health")
    assert response.status_code == 503


def test_member_endpoints(client):
    member = add_member(client)
    assert member["email"] == "ada@example.com"
    assert client.get("/members").get_json() == [member]
    duplicate = client.post("/members", json={"name": "Ada", "email": "ada@example.com"})
    assert duplicate.status_code == 400


def test_borrow_return_and_repeat(client):
    member = add_member(client)
    created = client.post("/loans", json={"tool_id": 1, "member_id": member["id"]})
    assert created.status_code == 201
    loan_id = created.get_json()["id"]
    assert [loan["id"] for loan in client.get("/loans?tool_id=1").get_json()] == [loan_id]
    assert client.get("/loans?tool_id=3").get_json() == []

    assert client.post(f"/loans/{loan_id}/return").status_code == 200
    assert [loan["id"] for loan in client.get("/loans/history").get_json()] == [loan_id]

    repeated = client.post(f"/loans/{loan_id}/repeat")
    assert repeated.status_code == 201
    assert client.post(f"/loans/{loan_id}/repeat").status_code == 409


def test_borrow_status_codes(client):
    member = add_member(client)
    assert client.post("/loans", json={"tool_id": "x"}).status_code == 400
    assert client.post("/loans", json={"tool_id": 3, "member_id": member["id"]}).status_code == 409
    assert client.post("/loans", json={"tool_id": 1, "member_id": 999999}).status_code == 404
    assert client.post("/loans/999999/return").status_code == 404
    assert client.post("/loans/999999/repeat").status_code == 404


def test_borrow_answers_503_and_writes_nothing_when_catalog_is_down(client, catalog):
    member = add_member(client)
    catalog.down = True
    response = client.post("/loans", json={"tool_id": 1, "member_id": member["id"]})
    assert response.status_code == 503
    assert "Catalog service unavailable" in response.get_json()["error"]
    assert client.get("/loans").get_json() == []
    assert client.get("/members").status_code == 200


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


def test_catalog_client_returns_the_tool_with_a_timeout(monkeypatch):
    calls = {}

    def fake_get(url, timeout):
        calls.update(url=url, timeout=timeout)
        return FakeResponse({"id": 7, "retired": False})

    monkeypatch.setenv("CATALOG_URL", "http://catalog.test")
    monkeypatch.setenv("CATALOG_TIMEOUT_S", "2")
    monkeypatch.setattr(requests, "get", fake_get)
    assert catalog_client.get_tool(7) == {"id": 7, "retired": False}
    assert calls == {"url": "http://catalog.test/tools/7", "timeout": 2.0}


def test_catalog_client_returns_none_for_an_unknown_tool(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse({}, status=404))
    assert catalog_client.get_tool(7) is None


@pytest.mark.parametrize(
    "failure",
    [requests.ConnectionError("refused"), requests.Timeout("slow")],
)
def test_catalog_client_raises_catalog_unavailable(monkeypatch, failure):
    def fake_get(url, timeout):
        raise failure

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(CatalogUnavailable):
        catalog_client.get_tool(7)


def test_catalog_client_raises_on_a_server_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse({}, status=500))
    with pytest.raises(CatalogUnavailable):
        catalog_client.get_tool(7)
