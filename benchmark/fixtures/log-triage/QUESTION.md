# Read-only root-cause read: the 09:20 incident

Read `logs/service.txt` (10 lines), `notes/context.md` and `app/worker.py`.
**Do not modify those files.** Write your answer to a file named `findings.txt`
in the sandbox root.

`findings.txt` must contain exactly these four lines, in this order, with no
extra text and no markdown fences:

```
root_cause: <one of db-pool-exhausted | api-timeout | disk-full>
error_count: <number of lines whose level is exactly ERROR>
first_warning_component: <the component field of the earliest WARN or ERROR line>
last_error_line: <1-based line number of the last ERROR line>
```

The component is the token that follows the level on each log line
(`worker`, `db`, `api`).
