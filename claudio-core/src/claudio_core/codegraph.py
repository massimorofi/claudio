"""Code-graph readiness: does the connected index actually cover this workspace?

Why this exists
---------------
The code-navigation policy (``instructions.CODE_DISCOVERY_POLICY``) tells the
agent to use the code graph *first*. That instruction is only honest when the
graph actually indexes the repository being worked on. It was not: the indexer
ran somewhere that only tracked a scratch directory, so every graph query about
real source returned nothing, and the agent had no way to know. It planned to
"check the graph", got an empty result, and either silently fell back to the file
tools or -- worse -- reported the emptiness as a finding about the code.

Two separate failures are fixed here:

1. **Invisible precondition.** Whether the index covers the workspace is a fact
   the *deployment* knows and the model does not. It now arrives in-band: the
   code-graph MCP server reports its coverage in its own catalog entry
   (``entry["status"]``) and this module turns that dict into a verdict that gets
   rendered into the prompt. There is no side channel -- no file the indexer
   writes and the agent reads, no shared mount, no path to configure. If the
   server does not say it, we do not guess it.
2. **Misread emptiness.** A graph query returning nothing has at least three
   causes -- not indexed, still warming, genuinely absent. They demand different
   responses, so the rendered block names the distinction explicitly for each
   status.

Contract
--------
Standard library only (``dataclasses`` / ``typing``). No filesystem access, no
network, no third-party imports, and no vendor names: the module describes a
capability ("the code-graph server"), not a product.

Fail-safe rule, enforced throughout: anything missing, malformed or unreported
degrades to :data:`STATUS_UNKNOWN`. An unknown verdict is never rendered as a
confident "not indexed", and it never raises.

Public API
----------
``GraphReadiness``            the verdict plus the evidence behind it
``STATUS_COVERED`` /          "covered" / "not_covered" / "unknown"
``STATUS_UNKNOWN``
``readiness_from_status``     build a verdict from the server's status dict
``status_unknown``            build an explicit unknown verdict
``render_readiness_block``    render the verdict for the system prompt
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "STATUS_COVERED",
    "STATUS_NOT_COVERED",
    "STATUS_UNKNOWN",
    "GraphReadiness",
    "readiness_from_status",
    "status_unknown",
    "render_readiness_block",
]

#: The index covers the workspace: graph results can be trusted as evidence.
STATUS_COVERED = "covered"
#: The server reported its coverage and this workspace is outside it: an empty
#: graph result means *not indexed*, nothing more.
STATUS_NOT_COVERED = "not_covered"
#: Coverage was not reported (no status, wrong shape, unusable values). The
#: agent must verify with a query before trusting an empty result.
STATUS_UNKNOWN = "unknown"


@dataclass
class GraphReadiness:
    """The verdict the prompt needs, with the evidence behind it.

    ``tracked`` / ``failed`` / ``generated_at`` are echoed straight from the
    server's own status report so the prompt can show *why* a verdict was made
    rather than asserting it bare.
    """

    status: str
    workspace: str
    repo: str | None = None
    tracked: tuple[str, ...] = field(default_factory=tuple)
    failed: tuple[str, ...] = field(default_factory=tuple)
    generated_at: str = ""
    detail: str = ""

    @property
    def usable(self) -> bool:
        """True only when the graph is known to cover the workspace."""
        return self.status == STATUS_COVERED


def status_unknown(workspace: Any = "", detail: str = "") -> GraphReadiness:
    """Build the fail-safe verdict.

    Used for every path where coverage is simply not known. It carries no claim
    about what is or is not indexed.
    """
    return GraphReadiness(
        status=STATUS_UNKNOWN,
        workspace=str(workspace) if workspace is not None else "",
        detail=detail or "the code-graph server did not report index coverage",
    )


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a reported list to a tuple of strings; anything else is empty."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v) for v in value if isinstance(v, (str, int, float)))


def _normalise(path: Any) -> str:
    """Lexical path normalisation -- no filesystem access, no symlink resolution.

    Purely string work on ``/``-separated segments (backslashes are treated as
    separators too) so the module stays free of any I/O.
    """
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return ""
    absolute = text.startswith("/")
    segments = [s for s in text.split("/") if s and s != "."]
    return ("/" if absolute else "") + "/".join(segments)


def _covering_repo(workspace: str, tracked: tuple[str, ...]) -> str | None:
    """The tracked repository that contains ``workspace``, if any.

    Longest match wins, so a nested checkout reports its own repository rather
    than a parent listed for convenience.
    """
    ws = _normalise(workspace)
    if not ws:
        return None
    best: str | None = None
    for repo in tracked:
        rp = _normalise(repo)
        if not rp:
            continue
        if ws == rp or ws.startswith(rp + "/"):
            if best is None or len(rp) > len(best):
                best = rp
    return best


def readiness_from_status(workspace: Any, status: Any) -> GraphReadiness:
    """Derive readiness from the status dict the MCP server itself reported.

    ``status`` is the plain dict taken off the server's catalog entry::

        {"tracked_repos": [...], "failed_repos": [...], "generated_at": str}

    The decision table is deliberately narrow:

    * ``status`` is not a dict (``None``, a string, a list, an int) -> **unknown**
    * a dict without a usable ``tracked_repos`` key, or whose value is not a
      list -> **unknown** (the server did not report coverage; that is not
      evidence of absence)
    * the workspace sits inside a reported repository -> **covered**
    * the server reported repositories and none contains the workspace ->
      **not_covered**

    Never raises, and never turns a missing report into a confident negative.
    """
    ws = str(workspace) if workspace is not None else ""
    if not isinstance(status, dict):
        return status_unknown(
            ws,
            "the code-graph server reported no index status, so coverage is unknown",
        )

    if "tracked_repos" not in status:
        return status_unknown(
            ws,
            "the code-graph server's status carries no tracked-repository list",
        )

    raw_tracked = status.get("tracked_repos")
    if not isinstance(raw_tracked, (list, tuple)):
        # Present but unusable is still "not reported properly", which is not
        # evidence that the workspace sits outside the index.
        return status_unknown(
            ws, "the code-graph server reported its tracked repositories in an unusable form"
        )

    tracked = _as_str_tuple(raw_tracked)
    failed = _as_str_tuple(status.get("failed_repos"))
    generated_at = str(status.get("generated_at", "")) if status.get("generated_at") is not None else ""

    if not tracked:
        # An explicitly empty list is a report, not an absence of one: the
        # server says it tracks nothing.
        return GraphReadiness(
            status=STATUS_NOT_COVERED,
            workspace=ws,
            tracked=(),
            failed=failed,
            generated_at=generated_at,
            detail="the code-graph server reports no tracked repositories",
        )

    repo = _covering_repo(ws, tracked)
    if repo is not None:
        return GraphReadiness(
            status=STATUS_COVERED,
            workspace=ws,
            repo=repo,
            tracked=tracked,
            failed=failed,
            generated_at=generated_at,
            detail="the workspace is inside a repository the server reports as tracked",
        )
    return GraphReadiness(
        status=STATUS_NOT_COVERED,
        workspace=ws,
        tracked=tracked,
        failed=failed,
        generated_at=generated_at,
        detail="the workspace is not inside any repository the server reports as tracked",
    )


def render_readiness_block(readiness: GraphReadiness) -> str:
    """Render the readiness verdict for the system prompt.

    Emitted only when a code-graph server is connected, so a deployment without
    one pays no tokens. The wording is operational: it tells the agent which
    route to take, what an empty result actually means, and what to disclose if
    it takes the fallback.
    """
    tracked = ", ".join(readiness.tracked) if readiness.tracked else "(none reported)"
    age = f" (reported at {readiness.generated_at})" if readiness.generated_at else ""

    if readiness.status == STATUS_COVERED:
        return (
            "# Code graph status\n\n"
            f"The connected code graph covers your current workspace "
            f"(`{readiness.workspace}`) via tracked repository `{readiness.repo}`"
            f"{age}. Graph results are trustworthy evidence here: use them for "
            "navigation and cite the query and the paths the results came from."
        )

    if readiness.status == STATUS_NOT_COVERED:
        failed = f" Reported failures: {', '.join(readiness.failed)}." if readiness.failed else ""
        return (
            "# Code graph status\n\n"
            f"**The connected code graph does NOT cover your current workspace "
            f"(`{readiness.workspace}`).**{failed}\n\n"
            f"Repositories it reports as tracked: {tracked}.{age}\n\n"
            "Consequences, in order:\n"
            "- A graph query that returns nothing here means *not indexed*, not "
            "*does not exist*. Never report an empty graph result as a finding "
            "about the code.\n"
            "- Use the file tools (`list_dir`, `read_file`, `run_command`) as "
            "your primary route for this workspace.\n"
            "- If you rely on that fallback, say so: name that the code graph "
            "did not cover the workspace and which claims are therefore "
            "unverified against the graph.\n"
            "- Do not claim you queried the graph unless a graph tool actually "
            "returned results."
        )

    # STATUS_UNKNOWN (and any unrecognised status): say nothing stronger than
    # "we were not told". Never a confident negative, never a confident positive.
    return (
        "# Code graph status\n\n"
        "Index coverage for this workspace is **unknown**: the connected "
        f"code-graph server did not report what it indexes (`{readiness.workspace}`)."
        f"{age}\n\n"
        f"Repositories it mentioned, if any: {tracked}.\n\n"
        "- Treat graph results as unverified. Before trusting an empty result, "
        "run a query you expect to return something (a file or symbol you can "
        "see with the file tools); if that comes back empty too, the workspace "
        "is almost certainly not indexed.\n"
        "- Do not report an empty graph result as a finding about the code, and "
        "do not claim the graph is absent or broken - only that coverage was not "
        "reported.\n"
        "- Note the uncertainty in your answer and name which claims were "
        "confirmed by other means."
    )
