from datetime import datetime, timezone


class CatalogError(Exception):
    pass


class ToolNotFound(CatalogError):
    pass


class ToolIsOut(CatalogError):
    pass


def add_tool(connection, name, category, now=None):
    name = name.strip()
    category = category.strip()
    if not name or not category:
        raise CatalogError("Name and category are required.")
    if now is None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = connection.execute(
        """
        INSERT INTO tools (name, category, retired, created_at)
        VALUES (?, ?, 0, ?)
        """,
        (name, category, now),
    )
    connection.commit()
    return cursor.lastrowid


def list_tools(connection):
    rows = connection.execute(
        """
        SELECT id, name, category, retired, created_at
        FROM tools
        ORDER BY name, id
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "retired": bool(row["retired"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def retire_tool(connection, tool_id):
    tool = connection.execute(
        "SELECT id, retired FROM tools WHERE id = ?",
        (tool_id,),
    ).fetchone()
    if tool is None:
        raise ToolNotFound("That tool does not exist.")
    if tool["retired"]:
        return
    open_loan = connection.execute(
        """
        SELECT id FROM loans
        WHERE tool_id = ? AND returned_at IS NULL
        """,
        (tool_id,),
    ).fetchone()
    if open_loan is not None:
        raise ToolIsOut("That tool is out on loan.")
    connection.execute(
        "UPDATE tools SET retired = 1 WHERE id = ?",
        (tool_id,),
    )
    connection.commit()
