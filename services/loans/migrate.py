import logging
import re
import sys
from pathlib import Path

from db import connect

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BATCH_SEPARATOR = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


def split_batches(sql):
    return [batch.strip() for batch in BATCH_SEPARATOR.split(sql) if batch.strip()]


def migrate(connection, directory=MIGRATIONS_DIR):
    cursor = connection.cursor()
    cursor.execute(
        """
        IF OBJECT_ID('schema_migrations') IS NULL
            CREATE TABLE schema_migrations (
              version NVARCHAR(255) NOT NULL PRIMARY KEY,
              applied_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            )
        """
    )
    connection.commit()
    cursor.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cursor.fetchall()}
    pending = [
        path for path in sorted(Path(directory).glob("[0-9][0-9][0-9]_*.sql")) if path.name not in applied
    ]
    for path in pending:
        try:
            for batch in split_batches(path.read_text()):
                cursor.execute(batch)
            cursor.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (path.name,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            log.error("migration %s failed, rolled back", path.name)
            raise
        log.info("applied migration %s", path.name)
    if not pending:
        log.info("no pending migrations")
    return [path.name for path in pending]


def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    connection = connect()
    try:
        migrate(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
