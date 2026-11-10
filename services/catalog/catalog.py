from datetime import datetime, timezone

from db import fetch_all, fetch_one


class CatalogError(Exception):
    pass


class ToolNotFound(CatalogError):
    pass


class ToolIsOut(CatalogError):
    pass


def _tool(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "retired": bool(row["retired"]),
        "created_at": row["created_at"],
    }


def add_tool(connection, name, category, now=None):
    name = (name or "").strip()
    category = (category or "").strip()
    if not name or not category:
        raise CatalogError("Name and category are required.")
    if now is None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = fetch_one(
        connection,
        """
        INSERT INTO tools (name, category, retired, created_at)
        OUTPUT INSERTED.id
        VALUES (%s, %s, 0, %s)
        """,
        (name, category, now),
    )
    connection.commit()
    return row["id"]


def list_tools(connection):
    rows = fetch_all(
        connection,
        """
        SELECT id, name, category, retired, created_at
        FROM tools
        ORDER BY name, id
        """,
    )
    return [_tool(row) for row in rows]


def get_tool(connection, tool_id):
    row = fetch_one(
        connection,
        "SELECT id, name, category, retired, created_at FROM tools WHERE id = %s",
        (tool_id,),
    )
    if row is None:
        raise ToolNotFound("That tool does not exist.")
    return _tool(row)


def retire_tool(connection, tool_id, has_open_loan):
    """has_open_loan asks the loans service; it raises LoansUnavailable when it cannot."""
    tool = get_tool(connection, tool_id)
    if tool["retired"]:
        return tool
    if has_open_loan(tool_id):
        raise ToolIsOut("That tool is out on loan.")
    connection.cursor().execute(
        "UPDATE tools SET retired = 1 WHERE id = %s",
        (tool_id,),
    )
    connection.commit()
    return {**tool, "retired": True}
