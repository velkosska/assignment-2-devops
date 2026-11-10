import pymssql
import pytest

from catalog_client import CatalogUnavailable
from loans import (
    LoanError,
    LoanNotFound,
    LoanStillOpen,
    MemberNotFound,
    ToolUnavailable,
    add_member,
    borrow_tool,
    due_at_for,
    is_overdue,
    list_loan_history,
    list_members,
    list_open_loans,
    repeat_loan,
    return_tool,
)
from migrate import migrate

HAMMER = 1
SAW = 2
RETIRED = 3


def test_add_member_stores_a_lowercase_email(db):
    member_id = add_member(db, " Ada ", "Ada@Example.com")
    assert list_members(db) == [{"id": member_id, "name": "Ada", "email": "ada@example.com"}]


def test_add_member_rejects_a_duplicate_email(db):
    add_member(db, "Ada", "ada@example.com")
    with pytest.raises(LoanError):
        add_member(db, "Ada Again", "ada@example.com")
    assert len(list_members(db)) == 1


def test_add_member_rejects_a_blank_name(db):
    with pytest.raises(LoanError):
        add_member(db, "  ", "ada@example.com")
    assert list_members(db) == []


def test_due_at_for_adds_seven_days():
    assert due_at_for("2026-10-06T12:00:00+00:00") == "2026-10-13T12:00:00+00:00"


def test_borrow_tool_sets_due_at_seven_days_later(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-06T12:00:00+00:00")
    loans = list_open_loans(db, now="2026-10-06T12:00:00+00:00")
    assert loans[0]["due_at"] == "2026-10-13T12:00:00+00:00"
    assert loans[0]["overdue"] is False
    assert loans[0]["member_name"] == "Ada"


def test_borrow_tool_refuses_a_retired_tool(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    with pytest.raises(ToolUnavailable):
        borrow_tool(db, RETIRED, member_id, catalog.get_tool)
    assert list_open_loans(db) == []


def test_borrow_tool_refuses_a_tool_the_catalog_does_not_know(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    with pytest.raises(ToolUnavailable):
        borrow_tool(db, 999, member_id, catalog.get_tool)


def test_borrow_tool_refuses_a_missing_member(db, catalog):
    with pytest.raises(MemberNotFound):
        borrow_tool(db, HAMMER, 999999, catalog.get_tool)


def test_borrow_tool_writes_nothing_when_catalog_is_down(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    catalog.down = True
    with pytest.raises(CatalogUnavailable):
        borrow_tool(db, HAMMER, member_id, catalog.get_tool)
    assert list_open_loans(db) == []


def test_open_loan_index_rejects_a_second_open_row(db):
    member_id = add_member(db, "Ada", "ada@example.com")
    cursor = db.cursor()

    def insert_loan(returned_at):
        cursor.execute(
            """
            INSERT INTO loans (tool_id, member_id, borrowed_at, due_at, returned_at)
            VALUES (%s, %s, '2026-10-06T12:00:00+00:00', '2026-10-13T12:00:00+00:00', %s)
            """,
            (HAMMER, member_id, returned_at),
        )

    insert_loan(None)
    insert_loan("2026-10-07T12:00:00+00:00")
    db.commit()
    with pytest.raises(pymssql.IntegrityError):
        insert_loan(None)
    db.rollback()
    assert len(list_open_loans(db, tool_id=HAMMER)) == 1


def test_borrow_tool_refuses_a_second_open_loan(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-06T12:00:00+00:00")
    with pytest.raises(ToolUnavailable):
        borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-06T13:00:00+00:00")
    assert len(list_open_loans(db)) == 1


def test_list_open_loans_filters_by_tool(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, HAMMER, member_id, catalog.get_tool)
    borrow_tool(db, SAW, member_id, catalog.get_tool)
    assert [loan["tool_id"] for loan in list_open_loans(db, tool_id=SAW)] == [SAW]


def test_return_tool_closes_the_loan(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    loan_id = borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-06T12:00:00+00:00")
    return_tool(db, loan_id, now="2026-10-08T12:00:00+00:00")
    assert list_open_loans(db) == []
    assert list_loan_history(db)[0]["returned_at"] == "2026-10-08T12:00:00+00:00"


def test_return_tool_raises_for_an_unknown_loan(db):
    with pytest.raises(LoanNotFound):
        return_tool(db, 999999)


def test_return_tool_leaves_an_already_returned_loan_unchanged(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    loan_id = borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-06T12:00:00+00:00")
    return_tool(db, loan_id, now="2026-10-08T12:00:00+00:00")
    return_tool(db, loan_id, now="2026-10-09T12:00:00+00:00")
    assert list_loan_history(db)[0]["returned_at"] == "2026-10-08T12:00:00+00:00"


def test_is_overdue_is_true_only_for_an_open_loan_past_due():
    assert is_overdue("2026-10-01T00:00:00+00:00", None, "2026-10-06T00:00:00+00:00") is True
    assert (
        is_overdue("2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00", "2026-10-06T00:00:00+00:00")
        is False
    )
    assert is_overdue("2026-10-07T00:00:00+00:00", None, "2026-10-06T00:00:00+00:00") is False


def test_list_open_loans_marks_a_past_due_loan(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-09-01T00:00:00+00:00")
    loans = list_open_loans(db, now="2026-10-06T00:00:00+00:00")
    assert loans[0]["overdue"] is True
    assert loans[0]["tool_id"] == HAMMER


def test_list_loan_history_includes_only_returned_loans(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    loan_id = borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-01T12:00:00+00:00")
    assert list_loan_history(db) == []
    return_tool(db, loan_id, now="2026-10-08T12:00:00+00:00")
    history = list_loan_history(db)
    assert [loan["id"] for loan in history] == [loan_id]
    assert history[0]["member_name"] == "Ada"


def test_repeat_loan_inserts_a_new_seven_day_loan(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    loan_id = borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-01T12:00:00+00:00")
    return_tool(db, loan_id, now="2026-10-08T12:00:00+00:00")
    new_id = repeat_loan(db, loan_id, catalog.get_tool, now="2026-10-10T12:00:00+00:00")
    assert new_id != loan_id
    assert list_loan_history(db)[0]["returned_at"] == "2026-10-08T12:00:00+00:00"
    open_loans = list_open_loans(db, now="2026-10-10T12:00:00+00:00")
    assert [loan["due_at"] for loan in open_loans] == ["2026-10-17T12:00:00+00:00"]


def test_repeat_loan_refuses_a_tool_that_is_already_out(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    loan_id = borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-01T12:00:00+00:00")
    return_tool(db, loan_id, now="2026-10-08T12:00:00+00:00")
    repeat_loan(db, loan_id, catalog.get_tool, now="2026-10-10T12:00:00+00:00")
    with pytest.raises(ToolUnavailable):
        repeat_loan(db, loan_id, catalog.get_tool, now="2026-10-11T12:00:00+00:00")


def test_repeat_loan_rejects_an_open_loan_and_an_unknown_id(db, catalog):
    member_id = add_member(db, "Ada", "ada@example.com")
    loan_id = borrow_tool(db, HAMMER, member_id, catalog.get_tool, now="2026-10-01T12:00:00+00:00")
    with pytest.raises(LoanStillOpen):
        repeat_loan(db, loan_id, catalog.get_tool)
    with pytest.raises(LoanNotFound):
        repeat_loan(db, 999999, catalog.get_tool)


def test_a_second_migrate_applies_nothing(db):
    assert migrate(db) == []
