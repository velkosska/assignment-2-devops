import logging
import os
import sys
import time

import pymssql
from flask import Flask, g, jsonify, request
from flask_cors import CORS

import catalog_client
from catalog_client import CatalogUnavailable
from db import check_database, open_connection
from loans import (
    LoanError,
    LoanNotFound,
    LoanStillOpen,
    MemberNotFound,
    ToolUnavailable,
    add_member,
    borrow_tool,
    list_loan_history,
    list_members,
    list_open_loans,
    repeat_loan,
    return_tool,
)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("loans")


def error(message, status):
    return jsonify({"error": message}), status


def loan_error_status(exc):
    if isinstance(exc, CatalogUnavailable):
        return 503
    if isinstance(exc, (MemberNotFound, LoanNotFound)):
        return 404
    if isinstance(exc, (ToolUnavailable, LoanStillOpen)):
        return 409
    return 400


def create_app(get_tool=None):
    if get_tool is None:
        get_tool = catalog_client.get_tool
    app = Flask(__name__)
    CORS(app, origins=os.environ.get("ALLOWED_ORIGIN", "http://localhost:8080"))

    @app.before_request
    def start_timer():
        g.started = time.perf_counter()

    @app.after_request
    def log_request(response):
        started = g.get("started", time.perf_counter())
        log.info(
            "%s %s %s %.1fms",
            request.method,
            request.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.errorhandler(pymssql.Error)
    def database_unavailable(exc):
        log.error("database error: %s", exc)
        return error("Loans database unavailable. Try again shortly.", 503)

    @app.get("/health")
    def health():
        version = os.environ.get("GIT_SHA", "dev")
        try:
            with open_connection(attempts=1) as connection:
                check_database(connection)
        except Exception as exc:
            log.warning("health check failed: %s", exc)
            return jsonify({"status": "error", "db": "unavailable", "version": version}), 503
        return jsonify({"status": "ok", "db": "ok", "version": version})

    @app.get("/members")
    def members():
        with open_connection() as connection:
            return jsonify(list_members(connection))

    @app.post("/members")
    def create_member():
        body = request.get_json(silent=True) or {}
        with open_connection() as connection:
            try:
                member_id = add_member(connection, body.get("name"), body.get("email"))
            except LoanError as exc:
                return error(str(exc), 400)
            member = next(m for m in list_members(connection) if m["id"] == member_id)
            return jsonify(member), 201

    @app.get("/loans")
    def loans():
        tool_id = request.args.get("tool_id", type=int)
        with open_connection() as connection:
            return jsonify(list_open_loans(connection, tool_id=tool_id))

    @app.post("/loans")
    def create_loan():
        body = request.get_json(silent=True) or {}
        try:
            tool_id = int(body.get("tool_id"))
            member_id = int(body.get("member_id"))
        except (TypeError, ValueError):
            return error("Choose a member and an available tool.", 400)
        with open_connection() as connection:
            try:
                loan_id = borrow_tool(connection, tool_id, member_id, get_tool)
            except (LoanError, CatalogUnavailable) as exc:
                return error(str(exc), loan_error_status(exc))
            return jsonify({"id": loan_id}), 201

    @app.post("/loans/<int:loan_id>/return")
    def return_loan(loan_id):
        with open_connection() as connection:
            try:
                return_tool(connection, loan_id)
            except LoanNotFound as exc:
                return error(str(exc), 404)
            return jsonify({"id": loan_id})

    @app.get("/loans/history")
    def loan_history():
        with open_connection() as connection:
            return jsonify(list_loan_history(connection))

    @app.post("/loans/<int:loan_id>/repeat")
    def repeat(loan_id):
        with open_connection() as connection:
            try:
                new_id = repeat_loan(connection, loan_id, get_tool)
            except (LoanError, CatalogUnavailable) as exc:
                return error(str(exc), loan_error_status(exc))
            return jsonify({"id": new_id}), 201

    return app


app = create_app()


def main():
    from migrate import main as run_migrations

    run_migrations()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), use_reloader=False)


if __name__ == "__main__":
    main()
