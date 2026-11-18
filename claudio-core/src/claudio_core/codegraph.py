"""Code-graph readiness: does the Gortex index actually cover this workspace?

Why this exists
---------------
The Gortex policy block (``instructions.CODE_DISCOVERY_POLICY``) tells the agent
to use the code graph *first*. That instruction is only honest when the graph
actually indexes the repository being worked on. It was not: the daemon ran in a
container whose only mount was a scratch notes directory, so every graph query
about real source returned nothing, and the agent had no way to know. It planned
to "check Gortex", got an empty result, and silently fell back to file tools --
or worse, reported the emptiness as a finding about the code.

Two separate failures are fixed here:

1. **Invisible precondition.** Whether the index covers the workspace is a fact
   the deployment knows and the model does not. The gortex entrypoint writes a
   manifest at start-up; this module reads it and renders it into the prompt, so
   the agent is told up front instead of discovering by trial.
2. **Misread emptiness.** A graph query returning nothing has at least three
   causes -- not indexed, still warming, genuinely absent. They demand different
   responses. The rendered block names the distinction explicitly.

Everything here is fail-safe by design: a missing, unreadable or malformed
manifest degrades to "status unknown", never to an exception and never to a
confident "not indexed".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Written by docker/gortex/entrypoint.sh into the shared /workspace mount, which
# is the only path both the daemon container and the agent container are
# guaranteed to see at the same location.
DEFAULT_MANIFEST_PATH = "/workspace/.gortex-index.json"

# Statuses the rendered block can report.
STATUS_CONNECTED_UNVERIFIED = "unverified"
STATUS_COVERED = "covered"
STATUS_NOT_COVERED = "not_covered"
STATUS_NO_MANIFEST = "no_manifest"


@dataclass(frozen=True)
class IndexManifest:
    """What the gortex entrypoint recorded about what it managed to track."""

    tracked_repos: tuple[str, ...] = ()
    failed_repos: tuple[str, ...] = ()
    generated_at: str = ""
    source_path: str = ""

    @property
    def healthy(self) -> bool:
        """True when at least one repo is tracked and none failed."""
        return bool(self.tracked_repos) and not self.failed_repos


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v) for v in value if isinstance(v, (str, int, float)))


def read_manifest(path: str | Path | None = None) -> IndexManifest | None:
    """Read the index manifest. Returns ``None`` when it cannot be used.

    ``None`` means "unknown", not "nothing is indexed" -- the caller must not
    upgrade an unreadable manifest into a confident negative claim.
    """
    candidate = Path(path) if path else Path(DEFAULT_MANIFEST_PATH)
    try:
        raw = candidate.read_text()
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return IndexManifest(
        tracked_repos=_as_str_tuple(data.get("tracked_repos")),
        failed_repos=_as_str_tuple(data.get("failed_repos")),
        generated_at=str(data.get("generated_at", "")),
        source_path=str(candidate),
    )


def covered_repo(manifest: IndexManifest, workspace: Path) -> str | None:
    """Return the tracked repo that contains ``workspace``, if any.

    Longest match wins so a nested checkout reports its own repo rather than a
    parent mounted for convenience. Comparison is on resolved paths, matching how
    the file tools decide workspace membership.
    """
    try:
        ws = Path(workspace).resolve()
    except OSError:
        return None
    best: str | None = None
    for repo in manifest.tracked_repos:
        try:
            rp = Path(repo).resolve()
        except OSError:
            continue
        if ws == rp or ws.is_relative_to(rp):
            if best is None or len(str(rp)) > len(best):
                best = str(rp)
    return best


@dataclass
class GraphReadiness:
    """The verdict the prompt needs, with the evidence behind it."""

    status: str
    workspace: str
    repo: str | None = None
    tracked: tuple[str, ...] = field(default_factory=tuple)
    failed: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status == STATUS_COVERED


def assess(
    workspace: Path | str,
    manifest: IndexManifest | None = None,
    *,
    manifest_path: str | Path | None = None,
) -> GraphReadiness:
    """Decide whether the code graph can be trusted for ``workspace``."""
    ws = Path(workspace)
    if manifest is None:
        manifest = read_manifest(manifest_path)
    if manifest is None:
        return GraphReadiness(
            status=STATUS_NO_MANIFEST,
            workspace=str(ws),
            detail="no index manifest was found",
        )
    repo = covered_repo(manifest, ws)
    if repo is not None:
        return GraphReadiness(
            status=STATUS_COVERED,
            workspace=str(ws),
            repo=repo,
            tracked=manifest.tracked_repos,
            failed=manifest.failed_repos,
            detail="workspace is inside a tracked repository",
        )
    return GraphReadiness(
        status=STATUS_NOT_COVERED,
        workspace=str(ws),
        tracked=manifest.tracked_repos,
        failed=manifest.failed_repos,
        detail="workspace is not inside any tracked repository",
    )


def render_readiness_block(readiness: GraphReadiness) -> str:
    """Render the readiness verdict for the system prompt.

    Emitted only when a Gortex server is connected, so a deployment without one
    pays no tokens. The wording is deliberately operational: it tells the agent
    which route to take *and* what to disclose if it takes the fallback.
    """
    tracked = ", ".join(readiness.tracked) if readiness.tracked else "(none)"
    if readiness.status == STATUS_COVERED:
        return (
            "# Code graph status\n\n"
            f"The Gortex index covers your current workspace "
            f"(`{readiness.workspace}`) via tracked repository "
            f"`{readiness.repo}`. Graph results are trustworthy for navigation "
            "here; cite the query and paths they came from."
        )
    if readiness.status == STATUS_NOT_COVERED:
        failed = f" Failed entries: {', '.join(readiness.failed)}." if readiness.failed else ""
        return (
            "# Code graph status\n\n"
            f"**The Gortex index does NOT cover your current workspace "
            f"(`{readiness.workspace}`).** Tracked repositories: {tracked}.{failed}\n\n"
            "Consequences, in order:\n"
            "- A graph query that returns nothing here means *not indexed*, not "
            "*does not exist*. Never report an empty graph result as a finding "
            "about the code.\n"
            "- Use the file tools (`list_dir`, `read_file`, `run_command`) as "
            "your primary route for this workspace.\n"
            "- If you rely on the fallback, say so in your answer: name that "
            "Gortex did not cover the workspace and which claims are therefore "
            "unverified against the graph.\n"
            "- Do not claim you queried the graph unless a graph tool actually "
            "returned results."
        )
    return (
        "# Code graph status\n\n"
        "The Gortex index status for this workspace is **unknown** (no manifest "
        "was published by the indexer). Treat graph results as unverified: "
        "confirm any negative result with the file tools before reporting it, "
        "and note the uncertainty in your answer. Tracked repositories, if any: "
        f"{tracked}."
    )


def probe_index_health(
    base_url: str,
    *,
    token: str = "",
    timeout: float = 3.0,
    client: Any = None,
) -> dict[str, Any] | None:
    """Live check of the daemon's index health endpoint.

    Returns the decoded body, or ``None`` on any failure. Kept separate from the
    manifest path so prompt assembly never blocks on the network: the manifest is
    the fast, offline source of truth, and this probe is for an operator-facing
    check or a session-start gate that can afford a round trip.
    """
    url = base_url.rstrip("/") + "/v1/index/health"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        if client is not None:
            resp = client.get(url, headers=headers, timeout=timeout)
        else:
            import httpx

            resp = httpx.get(url, headers=headers, timeout=timeout)
    except Exception:  # noqa: BLE001 - any transport failure means "unknown"
        return None
    status = getattr(resp, "status_code", 0)
    if not 200 <= status < 300:
        return None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "IndexManifest",
    "GraphReadiness",
    "STATUS_COVERED",
    "STATUS_NOT_COVERED",
    "STATUS_NO_MANIFEST",
    "STATUS_CONNECTED_UNVERIFIED",
    "read_manifest",
    "covered_repo",
    "assess",
    "render_readiness_block",
    "probe_index_health",
]
