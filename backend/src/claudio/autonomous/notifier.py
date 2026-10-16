"""Outbound notification channel (Phase 1: Watch).

Without this, monitoring is logs nobody reads. The daemon pushes a message on
every WARNING / CRITICAL verdict (and on failures). Two transports:

- **Email (SMTP)** — works headless; STARTTLS optional; auth optional.
- **Generic webhook** — a JSON POST for custom routing.

Both are best-effort: a failed notification is reported but never raises into
the caller, so a broken SMTP config cannot take a health check down.
"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from ..db import utcnow

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, config) -> None:
        self.config = config

    @property
    def _notif(self):
        return self.config.autonomous.notification

    def enabled_channels(self) -> list[str]:
        n = self._notif
        channels: list[str] = []
        if n.enabled and n.email.enabled:
            channels.append("email")
        if n.enabled and n.webhook.enabled:
            channels.append("webhook")
        return channels

    async def notify(self, verdict: str, summary: str, details: list[dict[str, Any]] | None = None) -> None:
        """Send a verdict notification on every enabled channel. Never raises."""
        if not self._notif.enabled:
            return
        results = await self._dispatch(verdict, summary, details or [])
        for channel, ok, err in results:
            if not ok:
                logger.warning("notification via %s failed: %s", channel, err)

    async def _dispatch(
        self, verdict: str, summary: str, details: list[dict[str, Any]]
    ) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        n = self._notif
        if n.enabled and n.email.enabled:
            try:
                await asyncio.to_thread(self._send_email_sync, verdict, summary, details)
                results.append(("email", True, ""))
            except Exception as exc:  # noqa: BLE001
                results.append(("email", False, str(exc)))
        if n.enabled and n.webhook.enabled:
            try:
                await self._send_webhook(verdict, summary, details)
                results.append(("webhook", True, ""))
            except Exception as exc:  # noqa: BLE001
                results.append(("webhook", False, str(exc)))
        return results

    # ---- email ----------------------------------------------------------

    def _send_email_sync(self, verdict: str, summary: str, details: list[dict[str, Any]]) -> None:
        ec = self._notif.email
        if not ec.smtp_host or not ec.from_addr or not ec.to_addr:
            raise ValueError("email notification missing host/from/to")

        msg = EmailMessage()
        msg["Subject"] = f"[Claudio {verdict.upper()}] {summary[:120]}"
        msg["From"] = ec.from_addr
        msg["To"] = ec.to_addr
        msg.set_content(self._render_email(verdict, summary, details))

        with smtplib.SMTP(ec.smtp_host, ec.smtp_port, timeout=30) as server:
            if ec.use_tls:
                server.starttls()
            if ec.username:
                server.login(ec.username, ec.password)
            server.send_message(msg)

    def _render_email(self, verdict: str, summary: str, details: list[dict[str, Any]]) -> str:
        lines = [
            f"Claudio health check: {verdict.upper()}",
            f"Time: {utcnow()}",
            "",
            f"Summary: {summary}",
            "",
            "Details:",
        ]
        for d in details:
            status = d.get("status", "?")
            name = d.get("name", "?")
            message = d.get("message", "")
            lines.append(f"  [{status.upper()}] {name}: {message}")
            detail = d.get("detail")
            if detail:
                for dl in str(detail).splitlines():
                    lines.append(f"      {dl}")
        lines.append("")
        lines.append("-- Claudio autonomous agent")
        return "\n".join(lines)

    # ---- webhook --------------------------------------------------------

    async def _send_webhook(self, verdict: str, summary: str, details: list[dict[str, Any]]) -> None:
        url = self._notif.webhook.url
        if not url:
            raise ValueError("webhook notification missing url")
        payload = {
            "verdict": verdict,
            "summary": summary,
            "timestamp": utcnow(),
            "details": details,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()

    # ---- test -----------------------------------------------------------

    async def test(self) -> dict[str, Any]:
        """Send a test message through every enabled channel; report per-channel."""
        results = await self._dispatch(
            "test",
            "This is a test notification from Claudio's autonomous monitor.",
            [{"name": "test", "status": "ok", "message": "notification channel test"}],
        )
        channels = {ch: {"ok": ok, "error": err} for ch, ok, err in results}
        all_ok = all(v["ok"] for v in channels.values()) if channels else False
        return {
            "enabled": self._notif.enabled,
            "channels": channels,
            "ok": all_ok,
            "message": (
                "No notification channels enabled"
                if not channels
                else ("All enabled channels delivered the test" if all_ok else "One or more channels failed")
            ),
        }
