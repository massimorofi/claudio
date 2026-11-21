"""Shared engine construction context (issue #57).

Every engine implementation is built with one typed :class:`EngineContext`
instead of having ad-hoc attributes bolted on after construction (the old
``engine.profile_health = ...``). The context carries the framework-neutral
services an adapter needs -- database, session store, model-health reporting,
the call accountant and a logger -- so adapters stay uniform and testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


@dataclass
class EngineContext:
    """The services handed to every engine at construction time.

    Fields are optional so lightweight/test engines can be built with just a
    config; production wiring (the supervisor) supplies the full set.
    """

    config: Any
    db: Any = None
    session_store: Any = None
    metrics: Any = None
    accountant: Any = None
    profile_health: Any = None
    logger: logging.Logger = logging.getLogger("claudio.engine")
