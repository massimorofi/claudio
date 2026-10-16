"""Autonomous server-management subsystem (Phase 1: Watch).

Read-only health monitoring: deterministic diagnostics produce an
OK / WARNING / CRITICAL verdict, which is stored, surfaced in the cockpit,
and pushed to the operator by the notification channel when it is not OK.

This package holds the *watch* layer only. Maintenance and recovery
(Phases 2-3) build on the same shape but add scoped, audited mutations.
"""
