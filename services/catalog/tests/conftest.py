import os

import pymssql
import pytest

from db import connect, connection_settings
from migrate import migrate


@pytest.fixture(scope="session")
def test_database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL, e.g. mssql://sa:<password>@localhost:1433/catalog_test")
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
    connection.cursor().execute("DELETE FROM tools")
    connection.commit()
    yield connection
    connection.close()
