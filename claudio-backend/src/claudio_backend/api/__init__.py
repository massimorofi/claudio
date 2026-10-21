"""HTTP routes.

Placeholder. The routes are specified in ``doc/backend.md`` and have not been
ported yet.

This package exists so that the import-linter contract in ``pyproject.toml``
resolves. import-linter fails the whole run with
``Module 'claudio_backend.api' does not exist`` when a contract names a module
that is absent, which means the contract silently stops protecting anything.
With an empty package in place the contract is live: as soon as route code is
added here it is checked against ADK imports.
"""
