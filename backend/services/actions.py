"""Action timeline service - logs every operational step on a lead."""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models import Action


def log_action(
    db: Session,
    lead_id: int,
    action_type: str,
    rep_id: Optional[int] = None,
    appointment_id: Optional[int] = None,
    note: Optional[str] = None,
) -> Action:
    """Log an action to the lead timeline.

    Args:
        db: Database session
        lead_id: ID of the lead
        action_type: One of: assigned, en_route, arrived, completed,
            closed, not_closed, rescheduled, no_show, status_change, created
        rep_id: ID of the rep (if applicable)
        appointment_id: ID of the appointment (if applicable)
        note: Additional context

    Returns:
        Created Action entry
    """
    entry = Action(
        lead_id=lead_id,
        rep_id=rep_id,
        appointment_id=appointment_id,
        action_type=action_type,
        note=note,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    return entry
