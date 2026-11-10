import pytest

from lend.catalog import (
    CatalogError,
    ToolIsOut,
    ToolNotFound,
    add_tool,
    list_tools,
    retire_tool,
)
from lend.db import connect, init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_db()
    connection = connect()
    yield connection
    connection.close()


def test_add_tool_rejects_blank_name_and_writes_nothing(db):
    with pytest.raises(CatalogError):
        add_tool(db, "  ", "hand")
    assert list_tools(db) == []


def test_add_tool_rejects_blank_category(db):
    with pytest.raises(CatalogError):
        add_tool(db, "Hammer", " ")
    assert list_tools(db) == []


def test_add_tool_stores_an_available_tool(db):
    add_tool(db, " Hammer ", " hand ", now="2026-10-06T12:00:00+00:00")
    tools = list_tools(db)
    assert tools == [
        {
            "id": 1,
            "name": "Hammer",
            "category": "hand",
            "retired": False,
            "created_at": "2026-10-06T12:00:00+00:00",
        }
    ]


def test_list_tools_orders_by_name(db):
    add_tool(db, "Saw", "cutting", now="2026-10-06T12:00:00+00:00")
    add_tool(db, "Hammer", "hand", now="2026-10-06T12:00:00+00:00")
    assert [tool["name"] for tool in list_tools(db)] == ["Hammer", "Saw"]


def test_retire_tool_marks_an_idle_tool_retired(db):
    add_tool(db, "Hammer", "hand", now="2026-10-06T12:00:00+00:00")
    retire_tool(db, 1)
    assert list_tools(db)[0]["retired"] is True


def test_retire_tool_refuses_a_tool_with_an_open_loan(db):
    add_tool(db, "Hammer", "hand", now="2026-10-06T12:00:00+00:00")
    db.execute(
        "INSERT INTO members (name, email) VALUES ('Ada', 'ada@example.com')"
    )
    db.execute(
        """
        INSERT INTO loans (tool_id, member_id, borrowed_at, due_at, returned_at)
        VALUES (1, 1, '2026-10-06T12:00:00+00:00', '2026-10-13T12:00:00+00:00', NULL)
        """
    )
    db.commit()
    with pytest.raises(ToolIsOut):
        retire_tool(db, 1)
    assert list_tools(db)[0]["retired"] is False


def test_retire_tool_raises_for_an_unknown_id(db):
    with pytest.raises(ToolNotFound):
        retire_tool(db, 99)


def test_retire_tool_leaves_an_already_retired_tool_retired(db):
    add_tool(db, "Hammer", "hand", now="2026-10-06T12:00:00+00:00")
    retire_tool(db, 1)
    retire_tool(db, 1)
    assert list_tools(db)[0]["retired"] is True
