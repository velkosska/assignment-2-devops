import sqlite3

import pytest

from lend.db import connect, init_db
from lend.loans import (
    LoanError,
    LoanNotFound,
    MemberNotFound,
    ToolUnavailable,
    add_member,
    borrow_tool,
    due_at_for,
    is_overdue,
    list_available_tools,
    list_loan_history,
    list_members,
    list_open_loans,
    repeat_loan,
    return_tool,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_db()
    connection = connect()
    yield connection
    connection.close()


def insert_tool(connection, name="Hammer", retired=0):
    cursor = connection.execute(
        """
        INSERT INTO tools (name, category, retired, created_at)
        VALUES (?, 'hand', ?, '2026-10-06T12:00:00+00:00')
        """,
        (name, retired),
    )
    connection.commit()
    return cursor.lastrowid


def test_add_member_stores_a_lowercase_email(db):
    add_member(db, " Ada ", "Ada@Example.com")
    assert list_members(db) == [{"id": 1, "name": "Ada", "email": "ada@example.com"}]


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


def test_borrow_tool_sets_due_at_seven_days_later(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-06T12:00:00+00:00")
    loans = list_open_loans(db, now="2026-10-06T12:00:00+00:00")
    assert loans[0]["due_at"] == "2026-10-13T12:00:00+00:00"
    assert loans[0]["overdue"] is False
    assert list_available_tools(db) == []


def test_borrow_tool_refuses_a_retired_tool(db):
    tool_id = insert_tool(db, retired=1)
    member_id = add_member(db, "Ada", "ada@example.com")
    with pytest.raises(ToolUnavailable):
        borrow_tool(db, tool_id, member_id, now="2026-10-06T12:00:00+00:00")
    assert list_open_loans(db) == []


def test_borrow_tool_refuses_a_missing_member(db):
    tool_id = insert_tool(db)
    with pytest.raises(MemberNotFound):
        borrow_tool(db, tool_id, 99, now="2026-10-06T12:00:00+00:00")


def test_open_loan_index_rejects_a_second_open_row(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrowed = "2026-10-06T12:00:00+00:00"
    due = "2026-10-13T12:00:00+00:00"

    def insert_loan(returned_at):
        db.execute(
            """
            INSERT INTO loans (tool_id, member_id, borrowed_at, due_at, returned_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tool_id, member_id, borrowed, due, returned_at),
        )

    insert_loan(None)
    db.commit()
    insert_loan("2026-10-07T12:00:00+00:00")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        insert_loan(None)
    db.rollback()
    open_rows = db.execute(
        "SELECT id FROM loans WHERE tool_id = ? AND returned_at IS NULL",
        (tool_id,),
    ).fetchall()
    assert len(open_rows) == 1


def test_borrow_tool_refuses_a_second_open_loan(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-06T12:00:00+00:00")
    with pytest.raises(ToolUnavailable):
        borrow_tool(db, tool_id, member_id, now="2026-10-06T13:00:00+00:00")
    assert len(list_open_loans(db)) == 1


def test_return_tool_closes_the_loan_and_frees_the_tool(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-06T12:00:00+00:00")
    return_tool(db, 1, now="2026-10-08T12:00:00+00:00")
    assert list_open_loans(db) == []
    assert list_available_tools(db) == [{"id": tool_id, "name": "Hammer"}]
    retired = db.execute("SELECT retired FROM tools WHERE id = ?", (tool_id,)).fetchone()
    assert retired["retired"] == 0
    returned_at = db.execute("SELECT returned_at FROM loans WHERE id = 1").fetchone()
    assert returned_at["returned_at"] == "2026-10-08T12:00:00+00:00"


def test_return_tool_raises_for_an_unknown_loan(db):
    with pytest.raises(LoanNotFound):
        return_tool(db, 99)


def test_return_tool_leaves_an_already_returned_loan_unchanged(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-06T12:00:00+00:00")
    return_tool(db, 1, now="2026-10-08T12:00:00+00:00")
    return_tool(db, 1, now="2026-10-09T12:00:00+00:00")
    returned_at = db.execute("SELECT returned_at FROM loans WHERE id = 1").fetchone()
    assert returned_at["returned_at"] == "2026-10-08T12:00:00+00:00"


def test_is_overdue_is_true_only_for_an_open_loan_past_due():
    assert is_overdue("2026-10-01T00:00:00+00:00", None, "2026-10-06T00:00:00+00:00") is True
    assert is_overdue("2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00", "2026-10-06T00:00:00+00:00") is False
    assert is_overdue("2026-10-07T00:00:00+00:00", None, "2026-10-06T00:00:00+00:00") is False


def test_list_open_loans_marks_a_past_due_loan(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-09-01T00:00:00+00:00")
    loans = list_open_loans(db, now="2026-10-06T00:00:00+00:00")
    assert loans[0]["overdue"] is True
    assert loans[0]["tool_id"] == tool_id


def test_list_loan_history_includes_only_returned_loans(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-01T12:00:00+00:00")
    assert list_loan_history(db) == []
    return_tool(db, 1, now="2026-10-08T12:00:00+00:00")
    history = list_loan_history(db)
    assert history[0]["returned_at"] == "2026-10-08T12:00:00+00:00"
    assert history[0]["tool_name"] == "Hammer"


def test_repeat_loan_inserts_a_new_seven_day_loan(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-01T12:00:00+00:00")
    return_tool(db, 1, now="2026-10-08T12:00:00+00:00")
    repeat_loan(db, 1, now="2026-10-10T12:00:00+00:00")
    original = db.execute("SELECT returned_at FROM loans WHERE id = 1").fetchone()
    assert original["returned_at"] == "2026-10-08T12:00:00+00:00"
    open_loans = list_open_loans(db, now="2026-10-10T12:00:00+00:00")
    assert len(open_loans) == 1
    assert open_loans[0]["due_at"] == "2026-10-17T12:00:00+00:00"
    assert len(list_loan_history(db)) == 1


def test_repeat_loan_refuses_a_tool_that_is_already_out(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-01T12:00:00+00:00")
    return_tool(db, 1, now="2026-10-08T12:00:00+00:00")
    repeat_loan(db, 1, now="2026-10-10T12:00:00+00:00")
    with pytest.raises(ToolUnavailable):
        repeat_loan(db, 1, now="2026-10-11T12:00:00+00:00")


def test_repeat_loan_rejects_an_open_loan_and_an_unknown_id(db):
    tool_id = insert_tool(db)
    member_id = add_member(db, "Ada", "ada@example.com")
    borrow_tool(db, tool_id, member_id, now="2026-10-01T12:00:00+00:00")
    with pytest.raises(LoanError):
        repeat_loan(db, 1)
    with pytest.raises(LoanNotFound):
        repeat_loan(db, 99)
