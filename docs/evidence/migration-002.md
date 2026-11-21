# Evidence: migration 002 on Azure

Migration `services/catalog/migrations/002_add_retired_at.sql` was deployed by the pipeline to a catalog database that already held data. Nothing was run by hand.

| What | Value |
|---|---|
| Deployment run (CD on `main`, all jobs green) | https://github.com/velkosska/assignment-2-devops/actions/runs/44928998253 |
| Commit deployed | `09dabcca865197775e62f806124a20d8f446081b` (merge of pull request #12) |
| Data before | [migration-002-before.json](migration-002-before.json), saved from `GET /tools` on the live catalog service before the deployment, after the four tools had been created in the live UI (the last one at 2026-11-21 17:08:55 UTC) |
| Startup log line | below |
| Data after | [migration-002-after.json](migration-002-after.json), saved from the same endpoint after the deployment |
| Version reported by `/health` after the deployment | `09dabcca865197775e62f806124a20d8f446081b` |

Startup log line, from `az containerapp logs show -g rg-mvelkoska -n mvelkoska-catalog --type console --tail 300`:

```
{"TimeStamp": "2026-11-21T17:17:36.3875592+00:00", "Log": "2026-11-21 17:17:36,387 __main__ INFO applied migration 002_add_retired_at.sql"}
```

Only 002 is applied: 001 was already recorded in `schema_migrations` by the first deployment, so the runner skipped it.

## Before and after

| id | name | category | retired | retired_at before | retired_at after |
|---|---|---|---|---|---|
| 1 | Iphone 11 | Phones | false | (field did not exist) | null |
| 2 | Hammer | Hand tools | false | (field did not exist) | null |
| 3 | Samsung Galaxy J5 | Phones | true | (field did not exist) | 2026-11-21T17:08:42+00:00 |
| 4 | Iphone 18 Pro | Phones | false | (field did not exist) | null |

All four rows are still there with the same ids, names, categories, `created_at` and `retired` values. The tool that was already retired received its `created_at` as `retired_at`, which is the best date known: the real date was never stored (see ADR-7).
