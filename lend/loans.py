import sqlite3
from datetime import datetime, timedelta, timezone

LOAN_DAYS = 7


class LoanError(Exception):
    pass


class MemberNotFound(LoanError):
    pass


class ToolUnavailable(LoanError):
    pass


class LoanNotFound(LoanError):
    pass


def add_member(connection, name, email):
    name = name.strip()
    email = email.strip().lower()
    if not name or "@" not in email:
        raise LoanError("Name and email are required.")
    try:
        cursor = connection.execute(
            "INSERT INTO members (name, email) VALUES (?, ?)",
            (name, email),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        raise LoanError("That email is already registered.")
    return cursor.lastrowid


def list_members(connection):
    rows = connection.execute(
        "SELECT id, name, email FROM members ORDER BY name, id"
    ).fetchall()
    return [{"id": row["id"], "name": row["name"], "email": row["email"]} for row in rows]


def list_available_tools(connection):
    rows = connection.execute(
        """
        SELECT tools.id, tools.name
        FROM tools
        WHERE tools.retired = 0
          AND NOT EXISTS (
            SELECT 1 FROM loans
            WHERE loans.tool_id = tools.id AND loans.returned_at IS NULL
          )
        ORDER BY tools.name, tools.id
        """
    ).fetchall()
    return [{"id": row["id"], "name": row["name"]} for row in rows]


def is_overdue(due_at, returned_at, now):
    if returned_at is not None:
        return False
    return datetime.fromisoformat(now) > datetime.fromisoformat(due_at)


def list_open_loans(connection, now=None):
    if now is None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = connection.execute(
        """
        SELECT loans.id, loans.tool_id, loans.borrowed_at, loans.due_at,
               tools.name AS tool_name, members.name AS member_name
        FROM loans
        JOIN tools ON tools.id = loans.tool_id
        JOIN members ON members.id = loans.member_id
        WHERE loans.returned_at IS NULL
        ORDER BY loans.due_at, loans.id
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "tool_id": row["tool_id"],
            "tool_name": row["tool_name"],
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


def borrow_tool(connection, tool_id, member_id, now=None):
    if now is None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection.execute("BEGIN IMMEDIATE")
    try:
        tool = connection.execute(
            "SELECT id, retired FROM tools WHERE id = ?",
            (tool_id,),
        ).fetchone()
        if tool is None or tool["retired"]:
            raise ToolUnavailable("That tool is not available.")
        member = connection.execute(
            "SELECT id FROM members WHERE id = ?",
            (member_id,),
        ).fetchone()
        if member is None:
            raise MemberNotFound("That member does not exist.")
        open_loan = connection.execute(
            """
            SELECT id FROM loans
            WHERE tool_id = ? AND returned_at IS NULL
            """,
            (tool_id,),
        ).fetchone()
        if open_loan is not None:
            raise ToolUnavailable("That tool is already out.")
        connection.execute(
            """
            INSERT INTO loans (tool_id, member_id, borrowed_at, due_at, returned_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (tool_id, member_id, now, due_at_for(now)),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        raise ToolUnavailable("That tool is already out.")
    except LoanError:
        connection.rollback()
        raise


def return_tool(connection, loan_id, now=None):
    if now is None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    loan = connection.execute(
        "SELECT id, returned_at FROM loans WHERE id = ?",
        (loan_id,),
    ).fetchone()
    if loan is None:
        raise LoanNotFound("That loan does not exist.")
    if loan["returned_at"] is not None:
        return
    connection.execute(
        "UPDATE loans SET returned_at = ? WHERE id = ?",
        (now, loan_id),
    )
    connection.commit()


def list_loan_history(connection):
    rows = connection.execute(
        """
        SELECT loans.id, loans.tool_id, loans.member_id,
               loans.borrowed_at, loans.due_at, loans.returned_at,
               tools.name AS tool_name, members.name AS member_name
        FROM loans
        JOIN tools ON tools.id = loans.tool_id
        JOIN members ON members.id = loans.member_id
        WHERE loans.returned_at IS NOT NULL
        ORDER BY loans.returned_at DESC, loans.id DESC
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "tool_id": row["tool_id"],
            "member_id": row["member_id"],
            "tool_name": row["tool_name"],
            "member_name": row["member_name"],
            "borrowed_at": row["borrowed_at"],
            "due_at": row["due_at"],
            "returned_at": row["returned_at"],
        }
        for row in rows
    ]


def repeat_loan(connection, loan_id, now=None):
    loan = connection.execute(
        "SELECT id, tool_id, member_id, returned_at FROM loans WHERE id = ?",
        (loan_id,),
    ).fetchone()
    if loan is None:
        raise LoanNotFound("That loan does not exist.")
    if loan["returned_at"] is None:
        raise LoanError("That loan is still open.")
    borrow_tool(connection, loan["tool_id"], loan["member_id"], now=now)
