# app/routers/ai_manager.py
"""AI Manager — grounded operational Q&A over stored records."""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..services import ai_manager

router = APIRouter(prefix="/ai/chat", tags=["AI Manager"])


class Ask(BaseModel):
    question: str


@router.post("")
def ask(payload: Ask, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Answer from stored records only.

    Returns `grounded: false` and refuses when the records do not support an
    answer, rather than producing a plausible one.
    """
    return ai_manager.ask(db, payload.question)


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
