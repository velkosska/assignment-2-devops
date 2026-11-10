import logging
import os
import sys
import time

import pymssql
from flask import Flask, g, jsonify, request
from flask_cors import CORS

import loans_client
from catalog import CatalogError, ToolIsOut, ToolNotFound, add_tool, get_tool, list_tools, retire_tool
from db import check_database, open_connection
from loans_client import LoansUnavailable

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("catalog")


def error(message, status):
    return jsonify({"error": message}), status


def create_app(has_open_loan=None):
    if has_open_loan is None:
        has_open_loan = loans_client.has_open_loan
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
        return error("Catalog database unavailable. Try again shortly.", 503)

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

    @app.get("/tools")
    def tools():
        with open_connection() as connection:
            return jsonify(list_tools(connection))

    @app.post("/tools")
    def create_tool():
        body = request.get_json(silent=True) or {}
        with open_connection() as connection:
            try:
                tool_id = add_tool(connection, body.get("name"), body.get("category"))
            except CatalogError as exc:
                return error(str(exc), 400)
            return jsonify(get_tool(connection, tool_id)), 201

    @app.get("/tools/<int:tool_id>")
    def tool(tool_id):
        with open_connection() as connection:
            try:
                return jsonify(get_tool(connection, tool_id))
            except ToolNotFound as exc:
                return error(str(exc), 404)

    @app.post("/tools/<int:tool_id>/retire")
    def retire(tool_id):
        with open_connection() as connection:
            try:
                return jsonify(retire_tool(connection, tool_id, has_open_loan))
            except ToolNotFound as exc:
                return error(str(exc), 404)
            except ToolIsOut as exc:
                return error(str(exc), 409)
            except LoansUnavailable as exc:
                return error(str(exc), 503)

    return app


app = create_app()


def main():
    from migrate import main as run_migrations

    run_migrations()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), use_reloader=False)


if __name__ == "__main__":
    main()
