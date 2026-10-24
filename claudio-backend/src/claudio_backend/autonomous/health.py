"""Read-only host health diagnostics (Phase 1: Watch).

Every check here is **read-only** — disk usage, memory pressure, load, failed
systemd units, recent journal errors, backup freshness. No check mutates the
host. Each returns a :class:`CheckResult` with a status; the overall verdict is
the worst status seen (``ok`` < ``warning`` < ``critical``).

The verdict is computed **deterministically** from thresholds, not by the LLM.
That is deliberate: a monitor that fires every few minutes must be reliable and
testable, and a read-only check has nothing to gain from model improvisation.
The LLM's role (later phases) is to *interpret* these findings, not to decide
them.

Subprocess-based checks (``df``, ``systemctl``, ``journalctl``) take an
injectable ``runner`` so they can be unit-tested without a real host. Pure
Python is used wherever possible (``/proc/meminfo``, ``os.getloadavg``).
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

# A runner executes a command and returns (returncode, stdout, stderr).
CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]

# Matches a systemd unit name (foo.service, snap.x.y.service, foo@bar.timer ...).
_UNIT_NAME_RE = re.compile(
    r"\b([\w@.\-]+\.(?:service|socket|timer|mount|target|slice|path|device))\b"
)


async def _default_runner(cmd: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout.decode(errors="replace") if stdout else "",
        stderr.decode(errors="replace") if stderr else "",
    )


@dataclass
class CheckResult:
    name: str
    status: str  # ok | warning | critical | skipped
    message: str
    value: float | None = None
    threshold: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Severity ordering: higher number is worse. ``skipped`` never escalates.
_VERDICT_ORDER = {"ok": 0, "skipped": 0, "warning": 1, "critical": 2}


def overall_verdict(results: list[CheckResult]) -> str:
    worst = "ok"
    for r in results:
        if r.status not in _VERDICT_ORDER:
            continue
        if _VERDICT_ORDER[r.status] > _VERDICT_ORDER[worst]:
            worst = r.status
    return worst


def build_summary(results: list[CheckResult]) -> str:
    """One-line human summary: the non-OK findings, or an all-clear."""
    bad = [r for r in results if r.status in ("warning", "critical")]
    if not bad:
        return "All checks passed"
    parts = [f"{r.name}: {r.message}" for r in bad]
    return "; ".join(parts)


# Filesystem types that are volatile pseudo-mounts — never worth a disk alert.
_SKIP_FSTYPES = {
    "tmpfs", "devtmpfs", "squashfs", "proc", "sysfs", "devpts",
    "securityfs", "pstore", "bpf", "tracefs", "debugfs", "mqueue",
    "hugetlbfs", "fusectl", "configfs", "autofs", "binfmt_misc",
}
_SKIP_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/run")


class HealthChecker:
    """Runs the read-only diagnostic suite against the current host."""

    def __init__(self, config, runner: CommandRunner | None = None) -> None:
        self.config = config
        self._runner = runner or _default_runner

    # ---- individual checks ---------------------------------------------

    async def check_disk(self) -> CheckResult:
        hc = self.config.autonomous.health_check
        try:
            rc, out, _err = await self._runner(["df", "-P"])
        except (OSError, asyncio.TimeoutError) as exc:
            return CheckResult("disk", "skipped", f"df unavailable: {exc}")
        if rc != 0:
            return CheckResult("disk", "skipped", "df returned an error")

        worst = "ok"
        offenders: list[str] = []
        max_pct = 0.0
        for line in out.splitlines()[1:]:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            fstype, capacity, mount = parts[0], parts[4], parts[5]
            if fstype in _SKIP_FSTYPES:
                continue
            if any(mount == p or mount.startswith(p + "/") for p in _SKIP_MOUNT_PREFIXES):
                continue
            try:
                pct = float(capacity.rstrip("%"))
            except ValueError:
                continue
            max_pct = max(max_pct, pct)
            if pct >= hc.disk_crit_percent:
                worst = "critical"
                offenders.append(f"{mount} at {pct:.0f}%")
            elif pct >= hc.disk_warn_percent:
                if worst != "critical":
                    worst = "warning"
                offenders.append(f"{mount} at {pct:.0f}%")

        if worst == "ok":
            return CheckResult("disk", "ok", f"all filesystems below {hc.disk_warn_percent:.0f}%", value=max_pct, threshold=hc.disk_warn_percent)
        return CheckResult(
            "disk", worst,
            f"high disk usage: {', '.join(offenders)}",
            value=max_pct, threshold=hc.disk_crit_percent,
            detail="\n".join(offenders),
        )

    async def check_memory(self) -> CheckResult:
        hc = self.config.autonomous.health_check
        try:
            info = _read_meminfo()
        except OSError as exc:
            return CheckResult("memory", "skipped", f"cannot read /proc/meminfo: {exc}")

        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", 0)
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        if total <= 0:
            return CheckResult("memory", "skipped", "MemTotal unavailable")

        mem_used_pct = (total - avail) / total * 100.0
        swap_used_pct = ((swap_total - swap_free) / swap_total * 100.0) if swap_total > 0 else 0.0

        status = "ok"
        message = f"mem {mem_used_pct:.0f}% used"
        if swap_total > 0 and swap_used_pct >= hc.swap_warn_percent:
            status = "warning"
            message = f"swap {swap_used_pct:.0f}% used, mem {mem_used_pct:.0f}% used"
        if mem_used_pct >= hc.mem_crit_percent:
            status = "critical"
            message = f"memory {mem_used_pct:.0f}% used"
        return CheckResult("memory", status, message, value=round(mem_used_pct, 1), threshold=hc.mem_crit_percent)

    async def check_load(self) -> CheckResult:
        hc = self.config.autonomous.health_check
        try:
            load1, load5, load15 = os.getloadavg()
        except OSError as exc:
            return CheckResult("load", "skipped", f"loadavg unavailable: {exc}")
        cores = os.cpu_count() or 1
        status = "ok"
        if load1 > cores * hc.load_crit_factor:
            status = "critical"
        elif load1 > cores * hc.load_warn_factor:
            status = "warning"
        msg = f"load {load1:.2f} / {load5:.2f} / {load15:.2f} on {cores} core(s)"
        return CheckResult("load", status, msg, value=round(load1, 2), threshold=round(cores * hc.load_warn_factor, 2))

    async def check_failed_units(self) -> CheckResult:
        try:
            rc, out, err = await self._runner(
                ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"]
            )
        except (FileNotFoundError, OSError, asyncio.TimeoutError) as exc:
            return CheckResult("failed_units", "skipped", f"systemctl unavailable: {exc}")
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if rc != 0 and not lines:
            return CheckResult("failed_units", "skipped", f"systemctl --failed failed: {err.strip()[:120]}")
        if not lines:
            return CheckResult("failed_units", "ok", "no failed units")
        # Extract the unit NAME by pattern, not by column index. ``systemctl
        # --failed`` prefixes each row with a status glyph (●), so ``split()[0]``
        # returned "●" and the issue title read "2 failed unit(s): ●, ●" — the
        # monitor never actually knew which unit had failed, and neither did the
        # model it was fed.
        names: list[str] = []
        for ln in lines:
            m = _UNIT_NAME_RE.search(ln)
            names.append(m.group(1) if m else ln.split()[0])
        return CheckResult(
            "failed_units", "warning",
            f"{len(names)} failed unit(s): {', '.join(names[:10])}",
            value=float(len(names)),
            detail="\n".join(lines),
        )

    async def check_journal_errors(self) -> CheckResult:
        # A TIME window, never a line count. ``-n 200`` reads the last 200
        # error lines regardless of when they happened: on this host that window
        # spanned nine days, so errors from before any fix kept re-raising the
        # same issue forever and no fix could ever clear it.
        hc = self.config.autonomous.health_check
        lookback = int(getattr(hc, "journal_lookback_minutes", 15) or 15)
        try:
            rc, out, err = await self._runner(
                [
                    "journalctl",
                    "-p", "err",
                    "--since", f"{lookback} min ago",
                    "--no-pager",
                ]
            )
        except (FileNotFoundError, OSError, asyncio.TimeoutError) as exc:
            return CheckResult("journal_errors", "skipped", f"journalctl unavailable: {exc}")
        if rc != 0:
            return CheckResult("journal_errors", "skipped", f"journalctl failed: {err.strip()[:120]}")
        # Drop journalctl's own banner lines ("-- No entries --", boot markers).
        lines = [
            ln for ln in out.splitlines()
            if ln.strip() and not ln.startswith("--")
        ]
        count = len(lines)
        if count == 0:
            return CheckResult(
                "journal_errors", "ok",
                f"no error-level log entries in the last {lookback} min",
            )
        return CheckResult(
            "journal_errors", "warning",
            f"{count} error-level entr{'y' if count == 1 else 'ies'} "
            f"in the last {lookback} min",
            value=float(count),
            detail="\n".join(lines[-20:]),
        )

    async def check_backup_freshness(self) -> CheckResult:
        hc = self.config.autonomous.health_check
        backup_path = (hc.backup_path or "").strip()
        if not backup_path:
            return CheckResult("backup", "skipped", "no backup_path configured")
        root = Path(backup_path)
        if not root.exists() or not root.is_dir():
            return CheckResult("backup", "critical", f"backup path {backup_path} does not exist")
        newest: datetime | None = None
        newest_file: str = ""
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                fp = Path(dirpath) / fn
                try:
                    mtime = fp.stat().st_mtime
                except OSError:
                    continue
                mt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                if newest is None or mt > newest:
                    newest = mt
                    newest_file = str(fp)
        if newest is None:
            return CheckResult("backup", "critical", f"no backup files found under {backup_path}")
        age_hours = (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0
        if age_hours > hc.backup_max_age_hours:
            return CheckResult(
                "backup", "warning",
                f"newest backup is {age_hours:.1f}h old (max {hc.backup_max_age_hours:.0f}h)",
                value=round(age_hours, 1), threshold=hc.backup_max_age_hours,
                detail=newest_file,
            )
        return CheckResult("backup", "ok", f"newest backup is {age_hours:.1f}h old", value=round(age_hours, 1))

    # ---- aggregate ------------------------------------------------------

    async def run_all(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for check in (
            self.check_disk,
            self.check_memory,
            self.check_load,
            self.check_failed_units,
            self.check_journal_errors,
            self.check_backup_freshness,
        ):
            try:
                results.append(await check())
            except Exception as exc:  # noqa: BLE001 — a check must never crash the suite
                results.append(CheckResult(check.__name__, "skipped", f"check error: {exc}"))
        return results


def _read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dict of kB values (name -> kB)."""
    info: dict[str, int] = {}
    with open("/proc/meminfo") as f:
        for line in f:
            if ":" not in line:
                continue
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                try:
                    info[key.strip()] = int(parts[0])
                except ValueError:
                    pass
    return info


async def run_health_checks(config, runner: CommandRunner | None = None) -> dict[str, Any]:
    """Run the full suite and return a serializable result dict."""
    import time

    checker = HealthChecker(config, runner=runner)
    start = time.time()
    results = await checker.run_all()
    duration_ms = int((time.time() - start) * 1000)
    verdict = overall_verdict(results)
    return {
        "verdict": verdict,
        "summary": build_summary(results),
        "duration_ms": duration_ms,
        "checks": [r.to_dict() for r in results],
    }
