"""sample_repo: a tiny synthetic package used as the workspace under test.

The call graph is deliberately small and unambiguous so an eval can assert that a
code-graph server pointed at this directory answers structural questions about
it:

    app.py -> routes.py -> service.py -> repository.py
      \\________________________/

Dependency-free by design: standard library only, no install step, nothing that
could make an eval flaky.
"""

__all__ = ["app", "routes", "service", "repository", "models", "config"]
