# Lend

A one-desk tool library for a volunteer workshop. One Python process stores tools, members, and loans in a single SQLite file.

## Run on a laptop

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

The app reads two environment variables.

| Variable | Default | SQLite path |
|---|---|---|
| `PORT` | `5000` | |
| `DATA_DIR` | `data` | `$DATA_DIR/lend.db`, so `data/lend.db` |

```bash
PORT=8000 DATA_DIR=/tmp/lend python app.py
```

The home page shows how many tools are free, out, and overdue, plus the three steps for adding a tool, adding a member, and lending for 7 days. `/tools` adds a tool, lists tools as available, out, or retired, and retires a tool when it has no open loan. `/loans` registers a member, lends an available tool for 7 days, marks a return, and shows Overdue when an open loan is past its due date. `/loans/history` lists returned loans and can repeat one for another 7 days. The process binds to `0.0.0.0`.

## Run in the container

`Dockerfile` is the provided template. It sets `PORT=8000` and `DATA_DIR=/data`, copies `app.py`, `lend/`, `templates/`, and `static/`, and starts with `python app.py`.

```bash
docker build -t lend .
docker run -p 8000:8000 -v lend-data:/data lend
```

Open http://127.0.0.1:8000

Inside that container the SQLite file is `/data/lend.db` on the Docker volume `lend-data`. The laptop file `data/lend.db` is a different database. Use the same `-v lend-data:/data` the next time you start the container if you want those rows again.

## Tests

Core catalog and loan rules are covered at 96%.

```bash
pytest --cov=lend.catalog --cov=lend.loans --cov-report=term-missing
```

The command covers `lend/catalog.py` and `lend/loans.py`. Route functions in `app.py` are outside that measurement.

## Container contract

`container/run.sh` reported:

```
=== SDD Assignment 1 contract check ===
Repository: /Users/marijavelkoska/Desktop/assignment_1

==> Repository shape
  PASS  one Dockerfile, one manifest (requirements.txt)

==> Build from a clean context, no build args
  PASS  image built
  PASS  image size 53 MB

==> Start on PORT=8000 and reach it from the host
  PASS  HTTP 200 from http://localhost:8000/

==> SQLite file under DATA_DIR
  PASS  found in /data: lend.db

==> Data persists, and a second boot does not re-seed
  PASS  volume at /data persists
  PASS  row counts unchanged across restart: loans=0 members=0 tools=0

==> PORT override is honoured (not hardcoded)
  PASS  HTTP 200 from http://localhost:9123/

=== ALL CHECKS PASSED ===
```
# assignment-2-devops
