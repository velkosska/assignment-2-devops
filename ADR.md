## [1]. Backend language and framework
Date: 2026-09-30
Status: Decided
Context: Lend is one process with server-rendered pages and a single SQLite file. Startup has to be one command, bound to 0.0.0.0, configured only through PORT and DATA_DIR. Every layer of this app has to be explainable on paper.
Decision: Use Python with Flask, Jinja templates, and the standard-library sqlite3 module.
Alternatives considered: Django, rejected because its ORM, admin, and auth would sit unused on two small domains. FastAPI, rejected because the interface is HTML forms, so Flask's built-in template rendering is the smaller fit.
Consequences: Runtime dependencies stay at Flask. SQL stays visible in lend/db.py, and there is no second frontend process to deploy in Assignment 2.

## [2]. Catalog and loans stay separate domains
Date: 2026-09-30
Status: Decided
Context: Lend needs two feature domains that could become separate services later. Both use the same SQLite file, so the boundary has to live in the code.
Decision: Catalog owns writes to the tools table through lend/catalog.py. Loans will own members and loans. retire_tool may read loans only to refuse retiring a tool that is still out.
Alternatives considered: One module for tools and loans, rejected because Assignment 2 needs a seam to split. A status column on tools updated by both domains, rejected because a return and a catalog edit could disagree about whether the tool is available.
Consequences: lend/catalog.py does not create loans. Loans reads tools.retired through the shared database. It does not write the tools table.

## [3]. An open loan is returned_at IS NULL
Date: 2026-10-05
Status: Decided
Context: Catalog and loans share one SQLite file. A tool can be borrowed only once at a time, and a volunteer needs to see which open loans are past due without a second process.
Decision: loans.tool_id references tools.id and loans.member_id references members.id. The partial unique index one_open_loan_per_tool allows one row per tool where returned_at is null. is_overdue compares due_at with the current time when the loan list is read.
Alternatives considered: A status or overdue column updated from both domains, rejected because a return and a catalog edit could disagree. A scheduled job that marks loans overdue, rejected because that needs a second process.
Consequences: return_tool updates loans only. A tool is available again when its open loan row has returned_at set. Overdue is derived, not stored.

## [4]. Tests cover domain rules, not routes
Date: 2026-10-06
Status: Decided
Context: The assignment asks for at least 70% coverage of the core business logic in both domains. The route functions open a connection, call one domain function, and render a template.
Decision: tests/test_catalog.py and tests/test_loans.py call add_tool, retire_tool, borrow_tool, return_tool, and is_overdue on a temporary SQLite file. Coverage is measured with pytest --cov=lend.catalog --cov=lend.loans --cov-report=term-missing, which reported 96%.
Alternatives considered: Coverage of the whole lend package, including app.py, rejected because untested route functions would make the percentage describe templates more than rules. Tests through the Flask client, rejected because they would repeat the same rules through form parsing.
Consequences: A change to a borrow, return, retire, or overdue rule fails a test. A broken template can still pass this command.

## [5]. No reminder to the member
Date: 2026-10-08
Status: Decided
Context: A member who still has a tool after due_at needs to hear about it from the volunteer at the desk. Sending that reminder from the app would need a mailer and a schedule beside the one web process.
Decision: Do not email the member. The volunteer sees Overdue on the loans page, because list_open_loans calls is_overdue while it reads loans whose returned_at is null.
Alternatives considered: Emailing the member when due_at passes, rejected because it needs a mailer outside this process. A notifications table drained by a second worker, rejected because this assignment allows one process only.
Consequences: Nothing is sent when a loan becomes overdue. The loans page is the only place it shows, and only while that page is open. Whether overdue is stored is ADR-3, not this decision.