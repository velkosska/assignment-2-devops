import os

import pymssql
import pytest

from db import connect, connection_settings
from migrate import migrate


@pytest.fixture(scope="session")
def test_database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL, e.g. mssql://sa:<password>@localhost:1433/loans_test")
    settings = connection_settings(url)
    master = pymssql.connect(**{**settings, "database": "master"}, autocommit=True)
    try:
        name = settings["database"]
        master.cursor().execute(f"IF DB_ID('{name}') IS NULL CREATE DATABASE [{name}]")
    finally:
        master.close()
    connection = connect(url)
    try:
        migrate(connection)
    finally:
        connection.close()
    return url


@pytest.fixture
def db(test_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    connection = connect(test_database_url)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM loans")
    cursor.execute("DELETE FROM members")
    connection.commit()
    yield connection
    connection.close()


class FakeCatalog:
    """Stands in for the catalog service: tool ids it knows, and whether each is retired."""

    def __init__(self, tools=None, down=False):
        self.tools = tools if tools is not None else {}
        self.down = down

    def get_tool(self, tool_id):
        from catalog_client import CatalogUnavailable

        if self.down:
            raise CatalogUnavailable("Catalog service unavailable.")
        if tool_id not in self.tools:
            return None
        return {"id": tool_id, "name": f"Tool {tool_id}", "retired": self.tools[tool_id]}


@pytest.fixture
def catalog():
    return FakeCatalog({1: False, 2: False, 3: True})
