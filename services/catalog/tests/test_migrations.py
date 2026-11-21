"""Migrations change a table that already holds data without losing it.

The other tests run on a database that is fully migrated. This one builds its own
database, applies only migration 001, fills it, and then lets the real runner apply
what is pending, the way a deploy does on startup.
"""

import shutil
from pathlib import Path

import pymssql
import pytest

from db import connection_settings, fetch_all
from migrate import MIGRATIONS_DIR, migrate

CREATED = "2026-10-06T12:00:00+00:00"


@pytest.fixture
def old_database(test_database_url, tmp_path):
    """A database at version 001 holding tools, plus the folder with only that migration."""
    settings = connection_settings(test_database_url)
    name = f"{settings['database']}_migration"
    master = pymssql.connect(**{**settings, "database": "master"}, autocommit=True)
    master.cursor().execute(f"IF DB_ID('{name}') IS NOT NULL DROP DATABASE [{name}]")
    master.cursor().execute(f"CREATE DATABASE [{name}]")
    first_only = tmp_path / "migrations"
    first_only.mkdir()
    shutil.copy(Path(MIGRATIONS_DIR) / "001_init.sql", first_only)
    connection = pymssql.connect(**{**settings, "database": name})
    try:
        assert migrate(connection, first_only) == ["001_init.sql"]
        cursor = connection.cursor()
        for tool_name, retired in (("Hammer", 0), ("Saw", 1), ("Drill", 0), ("Plane", 1)):
            cursor.execute(
                "INSERT INTO tools (name, category, retired, created_at) VALUES (%s, 'hand', %s, %s)",
                (tool_name, retired, CREATED),
            )
        connection.commit()
        yield connection
    finally:
        connection.close()
        master.cursor().execute(f"DROP DATABASE [{name}]")
        master.close()


def tools(connection):
    return {
        row["name"]: row
        for row in fetch_all(
            connection, "SELECT id, name, category, retired, created_at, retired_at FROM tools"
        )
    }


def test_pending_migrations_keep_every_existing_row(old_database):
    before = fetch_all(old_database, "SELECT id, name, category, retired, created_at FROM tools ORDER BY id")
    assert len(before) == 4

    applied = migrate(old_database)  # the real folder: everything after 001

    assert applied == ["002_add_retired_at.sql"]
    after = fetch_all(old_database, "SELECT id, name, category, retired, created_at FROM tools ORDER BY id")
    assert after == before  # same ids, same values, nothing dropped or recreated


def test_already_retired_tools_are_backfilled_and_the_others_stay_empty(old_database):
    migrate(old_database)

    by_name = tools(old_database)
    assert by_name["Saw"]["retired_at"] == CREATED
    assert by_name["Plane"]["retired_at"] == CREATED
    assert by_name["Hammer"]["retired_at"] is None
    assert by_name["Drill"]["retired_at"] is None


def test_each_migration_runs_once_and_is_recorded(old_database):
    migrate(old_database)

    assert migrate(old_database) == []
    recorded = [
        r["version"]
        for r in fetch_all(old_database, "SELECT version FROM schema_migrations ORDER BY version")
    ]
    assert recorded == ["001_init.sql", "002_add_retired_at.sql"]


def test_a_failing_migration_is_rolled_back_and_keeps_the_data(old_database, tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "001_init.sql").write_text("-- already applied\n")
    (broken / "002_broken.sql").write_text(
        "ALTER TABLE tools ADD half_done NVARCHAR(10) NULL;\nGO\nSELECT * FROM no_such_table;\n"
    )

    with pytest.raises(pymssql.Error):
        migrate(old_database, broken)

    columns = [
        r["name"]
        for r in fetch_all(old_database, "SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('tools')")
    ]
    assert "half_done" not in columns  # the first batch was undone with the failed one
    assert len(fetch_all(old_database, "SELECT id FROM tools")) == 4
    assert migrate(old_database) == ["002_add_retired_at.sql"]  # and the real one still applies
