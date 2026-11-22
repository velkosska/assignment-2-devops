# Evidence: partial failure across the seam

Captured on 2026-11-22 against the local compose stack (`docker compose up --build`). Each case stops one service with `docker compose stop`, then calls the cross-seam endpoint on the other. The other service keeps serving its own domain, and the cross-seam call answers `503` with a clear message and writes nothing.

## Catalog down: borrowing fails closed

Command: `docker compose stop catalog`, then `POST /loans`.

| Check | Result |
|---|---|
| Status | `503` |
| Body | `{"error":"Catalog service unavailable, so the tool cannot be checked. Try again shortly."}` |
| Loans `/health` | `200`, `db: ok` (own domain still works) |
| Open loans afterwards | `[]` (nothing was written) |

Full capture: [`partial-failure-borrow.json`](partial-failure-borrow.json).

## Loans down: retiring fails closed

Command: `docker compose stop loans`, then `POST /tools/{id}/retire`.

| Check | Result |
|---|---|
| Status | `503` |
| Body | `{"error":"Loans service unavailable, so the tool cannot be confirmed as not out. Try again shortly."}` |
| Catalog `/health` | `200`, `db: ok` (own domain still works) |
| Tool afterwards | still `"retired": false` (nothing was written) |

Full capture: [`partial-failure-retire.json`](partial-failure-retire.json).

The same behaviour is covered automatically by `tests/integration/test_seam.py`, which stops each service and checks the 503 and the empty write within 10 seconds.
