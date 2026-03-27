"""Confirmation system routes."""
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, Appointment, User
from app.auth import get_current_user, require_role
from services.audit import create_audit_log

router = APIRouter(prefix="/api/confirmation", tags=["confirmation"])


class ConfirmationAttemptCreate(BaseModel):
    """Create confirmation attempt request model."""

    appointment_id: int
    confirmed: bool
    notes: Optional[str] = None


class ConfirmationStatusUpdate(BaseModel):
    """Update confirmation status request model."""

    confirmation_status: str  # pending, confirming, confirmed, unconfirmed
    notes: Optional[str] = None


class ConfirmationQueueResponse(BaseModel):
    """Confirmation queue item response model."""

    appointment_id: int
    lead_id: int
    first_name: str
    last_name: str
    phone: str
    property_address: str
    city: str
    state: str
    average_monthly_bill: Optional[float]
    lead_quality_score: int
    confirmation_attempts_count: int
    last_confirmation_attempt_at: Optional[datetime]


@router.get("/queue", response_model=List[ConfirmationQueueResponse])
def get_confirmation_queue(
    current_user: User = Depends(require_role("confirmation")),
    db: Session = Depends(get_db),
):
    """
    Get appointments needing confirmation.

    Returns appointments with confirmation_status = confirming.

    Args:
        current_user: Current authenticated user (confirmation role)
        db: Database session

    Returns:
        List of appointments pending confirmation
    """
    # Get appointments in confirming status
    appointments = (
        db.query(Appointment)
        .filter(Appointment.confirmation_status == "confirming")
        .order_by(Appointment.created_at.asc())
        .all()
    )

    queue = []
    for appt in appointments:
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
        if lead:
            queue.append(
                {
                    "appointment_id": appt.id,
                    "lead_id": lead.id,
                    "first_name": lead.first_name,
                    "last_name": lead.last_name,
                    "phone": lead.phone,
                    "property_address": lead.property_address,
                    "city": lead.city,
                    "state": lead.state,
                    "average_monthly_bill": lead.average_monthly_bill,
                    "lead_quality_score": lead.lead_quality_score,
                    "confirmation_attempts_count": appt.confirmation_attempts_count or 0,
                    "last_confirmation_attempt_at": appt.last_confirmation_attempt_at,
                }
            )

    return queue


@router.post("/attempt")
def log_confirmation_attempt(
    attempt_data: ConfirmationAttemptCreate,
    current_user: User = Depends(require_role("confirmation")),
    db: Session = Depends(get_db),
):
    """
    Log a confirmation attempt.

    Updates appointment.confirmation_status and increments confirmation_attempts_count.

    Args:
        attempt_data: Confirmation attempt data
        current_user: Current authenticated user (confirmation)
        db: Database session

    Returns:
        Updated appointment data

    Raises:
        HTTPException: 404 if appointment not found
    """
    appointment = (
        db.query(Appointment).filter(Appointment.id == attempt_data.appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )

    # Increment attempt count
    appointment.confirmation_attempts_count = (appointment.confirmation_attempts_count or 0) + 1
    appointment.last_confirmation_attempt_at = datetime.utcnow()

    # Update confirmation status based on outcome
    if attempt_data.confirmed:
        appointment.confirmation_status = "confirmed"
        appointment.confirmed_by_user_id = current_user.id
    else:
        # Still confirming if not confirmed
        appointment.confirmation_status = "confirming"

    db.add(appointment)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="confirm_attempt",
        entity_type="appointment",
        entity_id=appointment.id,
        new_value="confirmed" if attempt_data.confirmed else "unconfirmed",
        details=f"Confirmation attempt #{appointment.confirmation_attempts_count}: {'confirmed' if attempt_data.confirmed else 'unconfirmed'}",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "appointment_id": appointment.id,
        "lead_id": appointment.lead_id,
        "confirmation_attempts_count": appointment.confirmation_attempts_count,
        "confirmation_status": appointment.confirmation_status,
        "last_confirmation_attempt_at": appointment.last_confirmation_attempt_at,
    }


@router.put("/{appointment_id}/status")
def update_confirmation_status(
    appointment_id: int,
    status_data: ConfirmationStatusUpdate,
    current_user: User = Depends(require_role("confirmation")),
    db: Session = Depends(get_db),
):
    """
    Update confirmation status for an appointment.

    Args:
        appointment_id: ID of the appointment
        status_data: New confirmation status
        current_user: Current authenticated user (confirmation)
        db: Database session

    Returns:
        Updated appointment status

    Raises:
        HTTPException: 404 if appointment not found
    """
    appointment = (
        db.query(Appointment).filter(Appointment.id == appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )

    old_status = appointment.confirmation_status

    # Update confirmation status
    appointment.confirmation_status = status_data.confirmation_status

    if status_data.confirmation_status == "confirmed":
        appointment.confirmed_by_user_id = current_user.id

    db.add(appointment)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="update",
        entity_type="appointment",
        entity_id=appointment.id,
        previous_value=old_status,
        new_value=appointment.confirmation_status,
        details=f"Confirmation status updated to {status_data.confirmation_status}",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "appointment_id": appointment.id,
        "confirmation_status": appointment.confirmation_status,
        "updated_at": datetime.utcnow(),
    }
