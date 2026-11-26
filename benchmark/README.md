# Benchmark task suite

`tasks.json` is the **locked** manifest of benchmark tasks. Each entry describes
one self-contained coding task, the fixture that seeds it, and the shell command
that decides whether the agent solved it.

## Layout

```
benchmark/
  tasks.json                      # the manifest (locked; bump "version" to change)
  tests/test_tasks_manifest.py    # schema + coverage + "seed really fails" checks
  fixtures/<task-id>/             # 2-4 tiny python/markdown/text files, copied
                                  # verbatim into the sandbox root
```

## Manifest schema

| key | meaning |
| --- | --- |
| `id` | unique kebab-case id, also the fixture folder name |
| `category` | `bug_fix` \| `feature` \| `refactor_debug` \| `codebase_qa` |
| `difficulty` | `easy` \| `medium` \| `hard` |
| `prompt` | the instruction handed to the agent |
| `fixture_dir` | always `benchmark/fixtures/<id>` |
| `setup` | shell run before the agent starts (may be `''`) |
| `verification` | shell run with the **sandbox root** as cwd; exits 0 only when solved |
| `seed_must_fail` | `true` = the untouched fixture must fail `verification` |
| `max_seconds` | wall-clock budget for the task |
| `rubric` | 1-5 anchor strings; `rubric[0]` is the 5-point anchor |

## Tasks (8)

| id | category | difficulty | what it exercises |
| --- | --- | --- | --- |
| `rate-limiter-offbyone` | bug_fix | easy | off-by-one in a token bucket + wrong rate maths |
| `csv-normalizer-dedup` | bug_fix | medium | blank rows, key normalisation, `Decimal` half-up rounding |
| `retry-helper-skeleton` | feature | medium | implement a spec'd retry helper (DI'd `sleep`) |
| `markdown-toc-feature` | feature | medium | markdown parsing, anchors, fence handling, dedupe |
| `split-legacy-report` | refactor_debug | hard | split a monolith into a package **and** fix `ZeroDivisionError` |
| `dedupe-discount-logic` | refactor_debug | medium | collapse a stale copy-paste tier ladder (lost discount cap) |
| `inventory-qa` | codebase_qa | easy | read-only package audit -> `findings.txt` |
| `log-triage` | codebase_qa | medium | read-only log root-cause read -> `findings.txt` |

## Constraints honoured by every verification

- Only `python3`, `pytest`, `git` and `grep`/`test` shell builtins.
- No network, no package installs, no repository dependencies, no absolute paths.
- Relative paths only, resolved from the sandbox root (the fixture contents are
  copied to the root, so `app/...`, `pkg/...`, `tests/...`, `logs/...` are the
  roots the prompts refer to).
- `codebase_qa` tasks are read-only: verification greps an answer file
  (`findings.txt`) that the agent must write.

## Running the checks

```bash
PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_tasks_manifest.py -q
python3 -c "import json;t=json.load(open('benchmark/tasks.json'));ts=t['tasks'] if isinstance(t,dict) else t;assert len(ts)>=6;cats={x['category'] for x in ts};assert {'bug_fix','feature','refactor_debug','codebase_qa'}<=cats;assert len({x['id'] for x in ts})==len(ts);assert sum(1 for x in ts if x.get('seed_must_fail'))>=4;print('SUITE_OK',len(ts))"
```

The manifest test copies every `seed_must_fail` fixture into a `tmp_path` and
asserts the verification command exits **non-zero** there, which proves the
tasks are real rather than trivially passing. All 8 tasks are flagged
`seed_must_fail`.
