# app/services/notifications.py
"""
Slack escalation — the human loop's doorbell.

Sends a real message to a real Slack channel when the agent must not act
alone, and records the outcome against integration_health so the Data Manager
reflects something that actually happened.

Two rules:
  * Internal comments never leave the building. Slack gets the diagnosis,
    the proposed action, the risk and a Workbench link — never raw internal
    ticket threads (guide 10, communication safety).
  * A failed send is recorded as a failure. It is never swallowed, and the
    Workbench item is still created, because losing the notification must not
    lose the exception.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session

from . import integrations

log = logging.getLogger(__name__)

SLACK_TIMEOUT = float(os.getenv("SLACK_TIMEOUT", "8"))


def _workbench_url(item_id: str) -> str:
    base = os.getenv("FRONTEND_URL", "http://localhost:3001").rstrip("/")
    return f"{base}/workbench?item={item_id}"


def build_escalation_blocks(
    *,
    item_id: str,
    issue_key: str,
    request_type: str,
    summary: str,
    reason: str,
    proposed_action: str,
    risk: str,
    recommendation: str,
) -> Dict[str, Any]:
    """Slack Block Kit payload. Requester-safe: no internal comment text."""
    link = _workbench_url(item_id)
    return {
        "text": f"[{issue_key}] Human review required — {request_type}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Human review required — {issue_key}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Ticket*\n{issue_key}"},
                    {"type": "mrkdwn", "text": f"*Type*\n{request_type}"},
                    {"type": "mrkdwn", "text": f"*Risk*\n{risk}"},
                    {"type": "mrkdwn", "text": f"*Summary*\n{summary}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Why this needs a human*\n{reason}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Proposed action*\n{proposed_action}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Agent recommendation*\n{recommendation}"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open in Workbench"},
                        "url": link,
                        "style": "primary",
                    }
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"ResolveFlow AI · Workbench item `{item_id}` · "
                            "no action is taken until a human decides"
                        ),
                    }
                ],
            },
        ],
    }


def send_slack(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post to the Slack incoming webhook.

    Returns {"delivered": bool, "detail": str}. Never raises — the caller has
    already created the Workbench item and must not lose it because Slack
    was unreachable.
    """
    hook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not hook:
        detail = "SLACK_WEBHOOK_URL is not configured; no notification was sent."
        log.warning(detail)
        integrations.record_failure(db, "slack", detail)
        return {"delivered": False, "detail": detail}

    try:
        r = httpx.post(hook, json=payload, timeout=SLACK_TIMEOUT)
        if r.status_code == 200 and r.text.strip() == "ok":
            integrations.record_write(db, "slack", 1)
            log.info("Slack escalation delivered.")
            return {"delivered": True, "detail": "Delivered to Slack."}
        detail = f"Slack returned HTTP {r.status_code}: {r.text[:200]}"
        integrations.record_failure(db, "slack", detail)
        return {"delivered": False, "detail": detail}
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        log.exception("Slack delivery failed.")
        integrations.record_failure(db, "slack", detail)
        return {"delivered": False, "detail": detail}


def notify_workbench_item(
    db: Session,
    *,
    item_id: str,
    issue_key: str,
    request_type: str,
    summary: str,
    reason: str,
    proposed_action: str,
    risk: str,
    recommendation: str,
) -> Dict[str, Any]:
    payload = build_escalation_blocks(
        item_id=item_id,
        issue_key=issue_key,
        request_type=request_type,
        summary=summary,
        reason=reason,
        proposed_action=proposed_action,
        risk=risk,
        recommendation=recommendation,
    )
    return send_slack(db, payload)
