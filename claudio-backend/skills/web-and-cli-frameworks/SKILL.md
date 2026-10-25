---
name: web-and-cli-frameworks
description: Summary of all skills for building applications on typed Python web and
  CLI frameworks — FastAPI (ASGI web APIs, routing, validation, dependency injection)
  and Typer (typed command-line interfaces). Use when asked to build a REST/HTTP API
  in Python, create a CLI, define routes and request validation models, wire dependency
  injection, or design command and argument structure.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: third-party Python package
    name: fastapi
    license: unknown
  - owner: third-party Python package
    name: typer
    license: unknown
---

# Web & CLI Frameworks (FastAPI, Typer)

Both frameworks sell the same promise: **declare types once and let the
framework do the parsing, validation and help text.** Where they differ is
the surface — HTTP request/response vs argv — but the discipline is shared.

## Constituent skills (2)

| Skill | Origin | What it contributes |
|---|---|---|
| fastapi | third-party package (ships its own SKILL.md) | Typed ASGI web APIs: routing, Pydantic validation, dependency injection, async |
| typer | third-party package (ships its own SKILL.md) | Typed CLIs: commands, arguments, options, help generation |

## Consolidated guidance

1. **The type hints are the interface.** In both frameworks the annotation
   *is* the contract: FastAPI validates the request body against it, Typer
   parses argv against it. Vague types (`dict`, `Any`, `str` where an enum
   belongs) push validation into hand-written code.
2. **Define the schema first.** For FastAPI: request/response models before
   routes. For Typer: the command tree before the implementations. Design
   the surface, then fill it.
3. **Use the framework's DI, don't build your own.** FastAPI's
   `Depends` exists for shared resources (DB sessions, auth, config).
   Hand-rolled singletons alongside it create two competing mechanisms.
4. **Errors are part of the contract.** FastAPI: explicit status codes and
   error response models, not 500s from uncaught exceptions. Typer: clear
   exit codes and messages, never a traceback for a user mistake.
5. **Async only where it pays.** FastAPI: `async def` for I/O-bound work;
   blocking calls inside `async` handlers stall the event loop — run them
   in a threadpool or keep the handler `def`.
6. **CLI discoverability is a feature.** Help text, subcommand grouping and
   sensible defaults matter as much as the API itself; a CLI nobody can
   guess at is a CLI nobody uses.
7. **Version the API surface.** Both the HTTP API and the CLI are public
   interfaces; breaking changes need deprecation, not silent edits.

## When to use this skill

"Build an API for…", "add an endpoint", "make a CLI for this", "add a
subcommand", "validate this request body".

## Note

These two skills ship *inside third-party Python packages* rather than in a
Claude Code plugin. For the authoritative, version-matched guidance, read
the `SKILL.md` bundled with the installed package rather than relying on
this summary alone.

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| third-party Python package | fastapi | unknown |
| third-party Python package | typer | unknown |
