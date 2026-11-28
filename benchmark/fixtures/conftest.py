"""Keep the benchmark fixtures out of a repository-wide pytest run.

Every fixture is a deliberately broken seed: its tests fail until the task is
solved. They are executed only inside a copied sandbox by the task harness (see
benchmark/tests/test_tasks_manifest.py and benchmark/README.md), never from the
repo root.
"""

collect_ignore_glob = ["*/tests/*", "*/app/*", "*/pkg/*"]
