import pytest

from catalog import (
    CatalogError,
    ToolIsOut,
    ToolNotFound,
    add_tool,
    get_tool,
    list_tools,
    retire_tool,
)
from loans_client import LoansUnavailable
from migrate import migrate

NOW = "2026-10-06T12:00:00+00:00"


def no_open_loan(tool_id):
    return False


def open_loan(tool_id):
    return True


def loans_down(tool_id):
    raise LoansUnavailable("Loans service unavailable.")


def test_add_tool_rejects_blank_name_and_writes_nothing(db):
    with pytest.raises(CatalogError):
        add_tool(db, "  ", "hand")
    assert list_tools(db) == []


def test_add_tool_rejects_blank_category(db):
    with pytest.raises(CatalogError):
        add_tool(db, "Hammer", " ")
    assert list_tools(db) == []


def test_add_tool_stores_an_available_tool(db):
    tool_id = add_tool(db, " Hammer ", " hand ", now=NOW)
    assert list_tools(db) == [
        {"id": tool_id, "name": "Hammer", "category": "hand", "retired": False, "created_at": NOW}
    ]


def test_list_tools_orders_by_name(db):
    add_tool(db, "Saw", "cutting", now=NOW)
    add_tool(db, "Hammer", "hand", now=NOW)
    assert [tool["name"] for tool in list_tools(db)] == ["Hammer", "Saw"]


def test_get_tool_raises_for_an_unknown_id(db):
    with pytest.raises(ToolNotFound):
        get_tool(db, 999999)


def test_retire_tool_marks_an_idle_tool_retired(db):
    tool_id = add_tool(db, "Hammer", "hand", now=NOW)
    assert retire_tool(db, tool_id, no_open_loan)["retired"] is True
    assert get_tool(db, tool_id)["retired"] is True


def test_retire_tool_refuses_a_tool_with_an_open_loan(db):
    tool_id = add_tool(db, "Hammer", "hand", now=NOW)
    with pytest.raises(ToolIsOut):
        retire_tool(db, tool_id, open_loan)
    assert get_tool(db, tool_id)["retired"] is False


def test_retire_tool_fails_closed_when_loans_is_down(db):
    tool_id = add_tool(db, "Hammer", "hand", now=NOW)
    with pytest.raises(LoansUnavailable):
        retire_tool(db, tool_id, loans_down)
    assert get_tool(db, tool_id)["retired"] is False


def test_retire_tool_raises_for_an_unknown_id(db):
    with pytest.raises(ToolNotFound):
        retire_tool(db, 999999, no_open_loan)


def test_retire_tool_leaves_an_already_retired_tool_without_asking_loans(db):
    tool_id = add_tool(db, "Hammer", "hand", now=NOW)
    retire_tool(db, tool_id, no_open_loan)
    assert retire_tool(db, tool_id, loans_down)["retired"] is True


def test_a_second_migrate_applies_nothing(db):
    assert migrate(db) == []
