from datetime import datetime, timedelta, timezone

import pymssql

from db import fetch_all, fetch_one

LOAN_DAYS = 7


class LoanError(Exception):
    pass


class MemberNotFound(LoanError):
    pass


class ToolUnavailable(LoanError):
    pass


class LoanNotFound(LoanError):
    pass


class LoanStillOpen(LoanError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_member(connection, name, email):
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name or "@" not in email:
        raise LoanError("Name and email are required.")
    try:
        row = fetch_one(
            connection,
            "INSERT INTO members (name, email) OUTPUT INSERTED.id VALUES (%s, %s)",
            (name, email),
        )
        connection.commit()
    except pymssql.IntegrityError as exc:
        connection.rollback()
        raise LoanError("That email is already registered.") from exc
    return row["id"]


def list_members(connection):
    rows = fetch_all(connection, "SELECT id, name, email FROM members ORDER BY name, id")
    return [{"id": row["id"], "name": row["name"], "email": row["email"]} for row in rows]


def is_overdue(due_at, returned_at, now):
    if returned_at is not None:
        return False
    return datetime.fromisoformat(now) > datetime.fromisoformat(due_at)


def list_open_loans(connection, now=None, tool_id=None):
    if now is None:
        now = _now()
    sql = """
        SELECT loans.id, loans.tool_id, loans.member_id, loans.borrowed_at, loans.due_at,
               members.name AS member_name
        FROM loans
        JOIN members ON members.id = loans.member_id
        WHERE loans.returned_at IS NULL
    """
    params = None
    if tool_id is not None:
        sql += " AND loans.tool_id = %s"
        params = (tool_id,)
    rows = fetch_all(connection, sql + " ORDER BY loans.due_at, loans.id", params)
    return [
        {
            "id": row["id"],
            "tool_id": row["tool_id"],
            "member_id": row["member_id"],
            "member_name": row["member_name"],
            "borrowed_at": row["borrowed_at"],
            "due_at": row["due_at"],
            "overdue": is_overdue(row["due_at"], None, now),
        }
        for row in rows
    ]


def due_at_for(borrowed_at):
    moment = datetime.fromisoformat(borrowed_at)
    return (moment + timedelta(days=LOAN_DAYS)).isoformat(timespec="seconds")


def borrow_tool(connection, tool_id, member_id, get_tool, now=None):
    """get_tool asks the catalog service; it raises CatalogUnavailable when it cannot."""
    if now is None:
        now = _now()
    tool = get_tool(tool_id)
    if tool is None or tool["retired"]:
        raise ToolUnavailable("That tool is not available.")
    member = fetch_one(connection, "SELECT id FROM members WHERE id = %s", (member_id,))
    if member is None:
        raise MemberNotFound("That member does not exist.")
    open_loan = fetch_one(
        connection,
        "SELECT id FROM loans WHERE tool_id = %s AND returned_at IS NULL",
        (tool_id,),
    )
    if open_loan is not None:
        raise ToolUnavailable("That tool is already out.")
    try:
        row = fetch_one(
            connection,
            """
            INSERT INTO loans (tool_id, member_id, borrowed_at, due_at, returned_at)
            OUTPUT INSERTED.id
            VALUES (%s, %s, %s, %s, NULL)
            """,
            (tool_id, member_id, now, due_at_for(now)),
        )
        connection.commit()
    except pymssql.IntegrityError as exc:
        connection.rollback()
        raise ToolUnavailable("That tool is already out.") from exc
    return row["id"]


def return_tool(connection, loan_id, now=None):
    if now is None:
        now = _now()
    loan = fetch_one(
        connection,
        "SELECT id, returned_at FROM loans WHERE id = %s",
        (loan_id,),
    )
    if loan is None:
        raise LoanNotFound("That loan does not exist.")
    if loan["returned_at"] is not None:
        return
    connection.cursor().execute(
        "UPDATE loans SET returned_at = %s WHERE id = %s",
        (now, loan_id),
    )
    connection.commit()


def list_loan_history(connection):
    rows = fetch_all(
        connection,
        """
        SELECT loans.id, loans.tool_id, loans.member_id,
               loans.borrowed_at, loans.due_at, loans.returned_at,
               members.name AS member_name
        FROM loans
        JOIN members ON members.id = loans.member_id
        WHERE loans.returned_at IS NOT NULL
        ORDER BY loans.returned_at DESC, loans.id DESC
        """,
    )
    return [
        {
            "id": row["id"],
            "tool_id": row["tool_id"],
            "member_id": row["member_id"],
            "member_name": row["member_name"],
            "borrowed_at": row["borrowed_at"],
            "due_at": row["due_at"],
            "returned_at": row["returned_at"],
        }
        for row in rows
    ]


def repeat_loan(connection, loan_id, get_tool, now=None):
    loan = fetch_one(
        connection,
        "SELECT id, tool_id, member_id, returned_at FROM loans WHERE id = %s",
        (loan_id,),
    )
    if loan is None:
        raise LoanNotFound("That loan does not exist.")
    if loan["returned_at"] is None:
        raise LoanStillOpen("That loan is still open.")
    return borrow_tool(connection, loan["tool_id"], loan["member_id"], get_tool, now=now)
