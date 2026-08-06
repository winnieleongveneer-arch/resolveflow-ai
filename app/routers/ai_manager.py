# app/routers/ai_manager.py
"""
AI Manager — grounded operational Q&A over stored records.

The template's command palette (Cmd/Ctrl+J) already posts to /api/ai/chat with
{message, history, context} and renders `response` as markdown. Rather than
change a working component, this endpoint speaks that dialect as well as its
own: it accepts `message` or `question`, and returns `response` (markdown, for
the panel) alongside the structured `answer` / `evidence` / `links` fields for
programmatic callers.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..services import ai_manager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/chat", tags=["AI Manager"])


class Ask(BaseModel):
    # The panel sends `message`; direct callers may send `question`.
    message: Optional[str] = None
    question: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None
    context: Optional[Dict[str, Any]] = None


def _to_markdown(result: Dict[str, Any]) -> str:
    """Render the grounded answer for the chat panel."""
    lines = [result["answer"]]

    evidence = result.get("evidence") or []
    if evidence:
        lines.append("")
        lines.append("**Evidence**")
        for item in evidence[:8]:
            kind = str(item.get("type", "record")).replace("_", " ")
            bits = [f"{k}: {v}" for k, v in item.items()
                    if k != "type" and v not in (None, "", [], {})]
            lines.append(f"- _{kind}_ — " + ", ".join(str(b) for b in bits[:6]))
        if len(evidence) > 8:
            lines.append(f"- …and {len(evidence) - 8} more")

    links = result.get("links") or []
    if links:
        lines.append("")
        lines.append("**Go to**")
        for link in links:
            lines.append(f"- [{link['label']}]({link['href']})")

    if not result.get("grounded", True):
        lines.append("")
        lines.append(
            "_No supporting records, so no operational answer was produced._"
        )
    return "\n".join(lines)


@router.post("")
def ask(payload: Ask, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Answer from stored records only.

    Returns `grounded: false` and declines when the records do not support an
    answer, rather than producing a plausible one.
    """
    question = (payload.message or payload.question or "").strip()
    result = ai_manager.ask(db, question)
    log.info("AI Manager: %r -> grounded=%s", question[:80], result["grounded"])
    return {
        # For the Cmd+J panel.
        "response": _to_markdown(result),
        "tool_calls": [],
        # Structured form.
        **result,
    }


@router.get("/examples")
def examples():
    return {"examples": [
        "Why is ITSM-2211 waiting for a human?",
        "Which policy prevented this action for ITSM-2199?",
        "Show current major-incident candidates.",
        "Which tickets are likely to breach?",
        "What are the active policies?",
        "Show the policy verdict distribution.",
    ]}
