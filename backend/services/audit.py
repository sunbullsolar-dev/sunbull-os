"""Audit logging service for tracking all entity changes."""
from datetime import datetime
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models import AuditLog


def create_audit_log(
    db: Session,
    user_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    previous_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
    details: Optional[str] = None,
    # Legacy alias support
    action_type: Optional[str] = None,
) -> AuditLog:
    """Create an immutable audit log entry.

    Args:
        db: Database session
        user_id: ID of user performing the action
        action: Action name (create, update, delete, etc.)
        entity_type: Entity type (lead, appointment, deal, rule, etc.)
        entity_id: ID of the entity
        previous_value: Previous value before change
        new_value: New value after change
        details: Additional details
        action_type: Legacy alias for action (deprecated)

    Returns:
        Created AuditLog entry
    """
    # Support legacy action_type parameter
    resolved_action = action or action_type or "unknown"

    entry = AuditLog(
        user_id=user_id,
        action=resolved_action,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_value=str(previous_value) if previous_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        details=details,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    return entry


def get_audit_trail(
    db: Session,
    entity_type: str = None,
    entity_id: int = None,
    limit: int = 100,
):
    """Get audit trail entries."""
    query = db.query(AuditLog)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id)
    return query.order_by(AuditLog.created_at.desc()).limit(limit).all()
