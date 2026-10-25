"""One-time migration: bind existing sessions to a workspace and seed the registry.

This backs the per-session-workspace change (doc/workspace-per-session-plan.md,
§10). It is **idempotent** — safe to run more than once — but it writes to the
live database, so take a backup first.

Run order (see the plan):
  1. Stop the backend.
  2. Back up the database:  cp data/claudio.db data/claudio.db.bak
  3. Make sure ``agent.default_workspace_dir`` in config/config.yaml points at
     the folder you actually want new sessions to use (NOT the repo root).
  4. Run this script:       .venv/bin/python scripts/migrate_workspaces.py
  5. Remove the stray ``.claudio/`` from the repo root:  rm -rf ../.claudio
  6. Start the backend.

Usage:
    cd claudio-backend
    .venv/bin/python scripts/migrate_workspaces.py [--default PATH]

``--default PATH`` overrides the workspace used to backfill sessions that have
none; otherwise the configured ``agent.default_workspace_dir`` is used.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make the backend package importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudio_core.db import Database  # noqa: E402
from claudio_core.settings import load_config  # noqa: E402
from claudio_backend.paths import resolve_workspace_dir  # noqa: E402


async def migrate(default_override: str | None) -> None:
    base = Path(__file__).resolve().parents[1]
    config = load_config(base / "config" / "config.yaml")
    default_ws = default_override or str(resolve_workspace_dir(config))

    db = Database(base / "data" / "claudio.db")
    await db.connect()  # runs the schema migration (adds the columns if absent)

    # Step 3: backfill sessions that have no workspace bound.
    cur = await db.conn.execute(
        "UPDATE claudio_sessions "
        "SET workspace_dir = ?, workspace_origin = 'default' "
        "WHERE workspace_dir = '' OR workspace_dir IS NULL",
        (default_ws,),
    )
    await db.conn.commit()
    print(f"Backfilled {cur.rowcount} session(s) with workspace {default_ws}")

    # Step 4: seed the registry from every distinct workspace in use, plus the
    # default, so the picker's "recent" list is populated.
    rows = await db.conn.execute(
        "SELECT DISTINCT workspace_dir FROM claudio_sessions "
        "WHERE workspace_dir != ''"
    )
    data = await rows.fetchall()
    paths = {r[0] for r in data}
    paths.add(default_ws)
    for p in sorted(paths):
        await db.register_workspace(p)
    print(f"Seeded {len(paths)} workspace(s) into the registry")

    await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--default",
        default=None,
        help="Workspace to backfill sessions that have none (default: configured default).",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.default))


if __name__ == "__main__":
    main()
