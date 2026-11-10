import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  retired INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS loans (
  id INTEGER PRIMARY KEY,
  tool_id INTEGER NOT NULL REFERENCES tools(id),
  member_id INTEGER NOT NULL REFERENCES members(id),
  borrowed_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  returned_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_open_loan_per_tool
  ON loans(tool_id) WHERE returned_at IS NULL;
"""


def database_path():
    data_dir = os.environ.get("DATA_DIR", "data")
    return Path(data_dir) / "lend.db"


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    connection = connect()
    try:
        connection.executescript(SCHEMA)
    finally:
        connection.close()
