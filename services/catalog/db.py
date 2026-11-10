import logging
import os
import time
import uuid
from contextlib import contextmanager
from urllib.parse import unquote, urlparse

import pymssql

log = logging.getLogger(__name__)


def connection_settings(url=None):
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    parts = urlparse(url)
    if parts.scheme not in ("mssql", "mssql+pymssql"):
        raise RuntimeError("DATABASE_URL must start with mssql://")
    return {
        "server": parts.hostname,
        "port": parts.port or 1433,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "database": parts.path.lstrip("/"),
    }


def connect(url=None, attempts=None):
    settings = connection_settings(url)
    if attempts is None:
        attempts = int(os.environ.get("DB_CONNECT_ATTEMPTS", "10"))
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return pymssql.connect(**settings, login_timeout=10)
        except pymssql.OperationalError as error:
            if attempt == attempts:
                raise
            log.warning(
                "database connect failed (attempt %d/%d), retrying in %.0fs: %s",
                attempt,
                attempts,
                delay,
                error,
            )
            time.sleep(delay)
            delay = min(delay * 2, 16)


@contextmanager
def open_connection(attempts=None):
    connection = connect(attempts=attempts)
    try:
        yield connection
    finally:
        connection.close()


def fetch_all(connection, sql, params=None):
    cursor = connection.cursor(as_dict=True)
    cursor.execute(sql, params)
    return cursor.fetchall()


def fetch_one(connection, sql, params=None):
    cursor = connection.cursor(as_dict=True)
    cursor.execute(sql, params)
    return cursor.fetchone()


def check_database(connection):
    """Read and write this service's own database, then undo the write."""
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM schema_migrations")
    cursor.fetchone()
    cursor.execute(
        "INSERT INTO schema_migrations (version) VALUES (%s)",
        (f"health-{uuid.uuid4()}",),
    )
    connection.rollback()
