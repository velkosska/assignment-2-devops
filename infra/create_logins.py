"""Create one SQL login per service on the Azure SQL server, once.

infra/setup.sh runs this file INSIDE the lend-catalog image (`python -`, script on
stdin), so it uses the same pymssql the services use against Azure and nothing has
to be installed on the laptop. Everything arrives as environment variables:

  SQL_FQDN, SQL_ADMIN_USER, SQL_ADMIN_PASSWORD   the server admin (never printed)
  SERVICES      "catalog:mvelkoska-catalog-db loans:mvelkoska-loans-db"
  PASSWORD_<SERVICE>   password to use for a login that has to be created
  ROTATE        "1" to replace the password of a login that already exists

Passwords are never printed. The script prints only machine-readable status lines
that setup.sh reads:

  RESULT <service> created|rotated|exists
  LOGIN_FORM <service> plain|at-server
  ROLES <service> db_datareader,db_datawriter,db_ddladmin
  VERIFIED <service>
"""

import os
import re
import sys
import time

import pymssql

SAFE = re.compile(r"^[A-Za-z0-9_-]+$")  # goes into DDL, which cannot be parameterised
RETRIES = 24  # about 5 minutes: a database resuming, or a new firewall rule spreading
# 40613: database not available (paused, resuming). 40615: this IP is not allowed by
# the firewall yet. Both pass by themselves, so wait instead of giving up.
RETRY_CODES = {40613, 40615}
RETRY_WORDS = ("not currently available", "timeout", "timed out")
# What a service needs in its own database, and nothing more: change its schema
# (migrations at startup), read and write its data. Not db_owner, which could also manage
# users and role members and do anything else in the database.
ROLES = ("db_ddladmin", "db_datareader", "db_datawriter")
HINTS = {
    40615: "the server firewall does not allow this IP yet (or the rule is for another IP)",
    40613: "the database is paused or still resuming",
}


def log(message):
    print(message, file=sys.stderr, flush=True)


def error_code(error):
    """pymssql wraps the server's error as one tuple: ((code, message),)."""
    first = error.args[0] if error.args else None
    return first[0] if isinstance(first, tuple) and first else first


def connect(user, password, database, forms=None, retries=RETRIES):
    """Connect, retrying while the database resumes. Returns (connection, form).

    Azure SQL sometimes wants the login as user@server. Try the plain name first.
    """
    server = os.environ["SQL_FQDN"]
    short = server.split(".")[0]
    candidates = {"plain": user, "at-server": f"{user}@{short}"}
    order = forms or ["plain", "at-server"]
    delay = 2.0
    last_error = None
    reason = ""
    for attempt in range(1, retries + 1):
        resuming = False
        for form in order:
            try:
                connection = pymssql.connect(
                    server=server,
                    user=candidates[form],
                    password=password,
                    database=database,
                    login_timeout=30,
                    autocommit=True,
                )
                return connection, form
            except pymssql.Error as error:
                last_error = error
                code = error_code(error)
                if code in RETRY_CODES:
                    resuming = True
                    reason = HINTS[code]
                elif any(word in str(error).lower() for word in RETRY_WORDS):
                    resuming = True
                    reason = "no answer yet"
        if not resuming or attempt == retries:
            break  # a real failure such as a wrong password, or out of patience
        log(f"  waiting for {database}, {reason} (attempt {attempt}/{retries})...")
        time.sleep(delay)
        delay = min(delay * 1.5, 15)
    code = error_code(last_error)
    hint = {18456: "wrong user or password", **HINTS}.get(code, "see the error")
    raise RuntimeError(
        f"could not connect to {database} as {user} ({hint}): {last_error}"
    )


def check_privileges(cursor, login):
    """Prove what the user can and cannot do, by acting as it from the admin connection.

    Works whether the login is new or already existed, because it needs no password.
    """
    cursor.execute(
        "SELECT r.name FROM sys.database_role_members m "
        "JOIN sys.database_principals r ON r.principal_id = m.role_principal_id "
        "JOIN sys.database_principals u ON u.principal_id = m.member_principal_id "
        "WHERE u.name = %s",
        (login,),
    )
    roles = sorted(row[0] for row in cursor.fetchall())
    if roles != sorted(ROLES):
        raise SystemExit(f"{login} has the roles {roles}, expected {sorted(ROLES)}")

    probe = "dbo.privilege_probe"
    drop = f"IF OBJECT_ID('{probe}') IS NOT NULL DROP TABLE {probe}"
    cursor.execute(drop)  # left over from an interrupted run
    can_create_user = False
    cursor.execute(f"EXECUTE AS USER = '{login}'")
    try:
        cursor.execute(f"CREATE TABLE {probe} (id INT)")  # db_ddladmin
        cursor.execute(f"INSERT INTO {probe} VALUES (1)")  # db_datawriter
        cursor.execute(f"SELECT id FROM {probe}")  # db_datareader
        cursor.fetchall()
        try:
            cursor.execute("CREATE USER privilege_probe WITHOUT LOGIN")
            can_create_user = True
        except pymssql.Error:
            pass  # expected: managing users is the admin's job, not the service's
    finally:
        cursor.execute("REVERT")
        cursor.execute(drop)
    if can_create_user:
        cursor.execute("DROP USER privilege_probe")
        raise SystemExit(f"{login} can create users: it has too many rights")
    return roles


def main():
    admin_user = os.environ["SQL_ADMIN_USER"]
    admin_password = os.environ["SQL_ADMIN_PASSWORD"]
    rotate = os.environ.get("ROTATE") == "1"

    services = []
    for item in os.environ["SERVICES"].split():
        name, database = item.split(":")
        login = f"{name}_user"
        for value in (name, database, login):
            if not SAFE.match(value):
                raise SystemExit(f"unsafe name: {value!r}")
        services.append((name, database, login))

    log("connecting to master as the server admin...")
    master, admin_form = connect(admin_user, admin_password, "master")
    cursor = master.cursor()
    changed = {}
    for name, _database, login in services:
        password = os.environ[f"PASSWORD_{name.upper()}"]
        if not SAFE.match(password):
            raise SystemExit("generated password is not URL-safe")
        cursor.execute("SELECT 1 FROM sys.sql_logins WHERE name = %s", (login,))
        if cursor.fetchone() is None:
            cursor.execute(f"CREATE LOGIN [{login}] WITH PASSWORD = '{password}'")
            changed[name] = "created"
        elif rotate:
            cursor.execute(f"ALTER LOGIN [{login}] WITH PASSWORD = '{password}'")
            changed[name] = "rotated"
        else:
            changed[name] = "exists"
        print(f"RESULT {name} {changed[name]}", flush=True)
    master.close()

    # A user only in its own database: the login cannot open the other one.
    for name, database, login in services:
        log(f"giving {login} access to {database} only...")
        connection, _ = connect(admin_user, admin_password, database, [admin_form])
        cursor = connection.cursor()
        cursor.execute(
            f"IF USER_ID('{login}') IS NULL CREATE USER [{login}] FOR LOGIN [{login}]"
        )
        for role in ROLES:
            cursor.execute(f"ALTER ROLE {role} ADD MEMBER [{login}]")
        # An earlier version of this script made the user db_owner: take that back.
        cursor.execute(
            f"IF IS_ROLEMEMBER('db_owner', '{login}') = 1 "
            f"ALTER ROLE db_owner DROP MEMBER [{login}]"
        )
        roles = check_privileges(cursor, login)
        print(f"ROLES {name} {','.join(roles)}", flush=True)
        connection.close()

    # Prove it with the real login, but only when we know its password.
    names = [name for name, _d, _l in services]
    for name, database, login in services:
        if changed[name] == "exists":
            continue
        password = os.environ[f"PASSWORD_{name.upper()}"]
        connection, form = connect(login, password, database)
        connection.close()
        print(f"LOGIN_FORM {name} {form}", flush=True)
        for other_name, other_database, _l in services:
            if other_name == name:
                continue
            try:
                other, _ = connect(login, password, other_database, [form], retries=1)
            except RuntimeError:
                continue  # expected: this login has no user in the other database
            other.close()
            raise SystemExit(f"{login} can open {other_database}: isolation is broken")
        print(f"VERIFIED {name}", flush=True)
    log(f"done for {', '.join(names)}")


if __name__ == "__main__":
    main()
