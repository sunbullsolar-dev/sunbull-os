"""Confirmation system routes."""
from datetime import datetime, timedelta, date, time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, Appointment, User, ConfirmationAttempt
from app.auth import get_current_user, require_role
from services.audit import create_audit_log
from services.scoring import calculate_lead_score

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


class LeadCreateForConfirmation(BaseModel):
    """Create lead request model for confirmation team."""
    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    property_address: str
    city: str
    state: str
    zip_code: str
    average_monthly_bill: Optional[float] = None
    estimated_annual_kwh: Optional[float] = None
    notes: Optional[str] = None


class AppointmentCreateForConfirmation(BaseModel):
    """Create appointment request model for confirmation team."""
    lead_id: int
    appointment_date: date
    appointment_time: time
    scheduled_duration_minutes: Optional[int] = 90
    appointment_address: str
    notes: Optional[str] = None


class ConfirmationAttemptWithOutcome(BaseModel):
    """Enhanced confirmation attempt with detailed outcome."""
    outcome: str  # confirmed, no_answer, reschedule_requested, not_interested
    notes: Optional[str] = None
    next_callback_date: Optional[date] = None


class LeadResponse(BaseModel):
    """Lead response model."""
    id: int
    first_name: str
    last_name: str
    phone: str
    email: Optional[str]
    deal_status: str
    lead_quality_score: int
    created_at: datetime

    class Config:
        from_attributes = True


class AppointmentResponse(BaseModel):
    """Appointment response model."""
    id: int
    lead_id: int
    appointment_date: date
    appointment_time: time
    appointment_status: str
    confirmation_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class MyQueueItem(BaseModel):
    """Item in confirmation queue for current user."""
    appointment_id: int
    lead_id: int
    first_name: str
    last_name: str
    phone: str
    property_address: str
    city: str
    state: str
    appointment_date: date
    appointment_time: time
    confirmation_status: str
    confirmation_attempts_count: int


@router.get("/queue", response_model=List[ConfirmationQueueResponse])
def get_confirmation_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get appointments needing confirmation.

    Returns appointments with confirmation_status = confirming.
    Allowed for confirmation and admin roles.

    Args:
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        List of appointments pending confirmation

    Raises:
        HTTPException: 403 if user is not confirmation or admin
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can access this resource",
        )

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Log a confirmation attempt.

    Updates appointment.confirmation_status and increments confirmation_attempts_count.
    Allowed for confirmation and admin roles.

    Args:
        attempt_data: Confirmation attempt data
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        Updated appointment data

    Raises:
        HTTPException: 403 if not authorized, 404 if appointment not found
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can access this resource",
        )

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update confirmation status for an appointment.
    Allowed for confirmation and admin roles.

    Args:
        appointment_id: ID of the appointment
        status_data: New confirmation status
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        Updated appointment status

    Raises:
        HTTPException: 403 if not authorized, 404 if appointment not found
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can access this resource",
        )

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


@router.get("/leads", response_model=List[LeadResponse])
def list_leads_for_confirmation(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List recent leads for confirmation team view."""
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can access this resource",
        )
    leads = (
        db.query(Lead)
        .order_by(Lead.created_at.desc())
        .limit(200)
        .all()
    )
    return leads


@router.post("/leads", response_model=LeadResponse)
def create_lead_for_confirmation(
    lead_data: LeadCreateForConfirmation,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a lead from the confirmation team (call center).
    Auto-sets source_type to call_center.
    Allowed for confirmation and admin roles.

    Args:
        lead_data: Lead creation data
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        Created lead

    Raises:
        HTTPException: 403 if not authorized
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can create leads via this endpoint",
        )

    # Calculate lead score
    score = calculate_lead_score(
        average_monthly_bill=lead_data.average_monthly_bill,
        city=lead_data.city,
        state=lead_data.state,
        confirmation_strength=0,
    )

    # Calculate cost per kwh if we have the data
    cost_per_kwh = None
    if lead_data.estimated_annual_kwh and lead_data.average_monthly_bill:
        cost_per_kwh = round(
            (lead_data.average_monthly_bill * 12) / lead_data.estimated_annual_kwh, 4
        )

    # Create lead
    lead = Lead(
        first_name=lead_data.first_name,
        last_name=lead_data.last_name,
        phone=lead_data.phone,
        email=lead_data.email,
        property_address=lead_data.property_address,
        city=lead_data.city,
        state=lead_data.state,
        zip_code=lead_data.zip_code,
        average_monthly_bill=lead_data.average_monthly_bill,
        estimated_annual_kwh=lead_data.estimated_annual_kwh,
        cost_per_kwh=cost_per_kwh,
        source_type="call_center",  # Auto-set
        deal_status="new",
        lead_quality_score=score,
        homeowner_status="owner",
        property_type="single_family",
        notes=lead_data.notes,
        setter_id=current_user.id,  # Confirmation team member is the setter
    )

    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="lead_created",
        entity_type="lead",
        entity_id=lead.id,
        details=f"Lead created by confirmation team: {lead.first_name} {lead.last_name}",
    )

    return lead


@router.post("/appointments", response_model=AppointmentResponse)
def create_appointment_for_confirmation(
    appt_data: AppointmentCreateForConfirmation,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create an appointment from the confirmation team.
    Allowed for confirmation and admin roles.

    Args:
        appt_data: Appointment creation data
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        Created appointment

    Raises:
        HTTPException: 403 if not authorized, 404 if lead not found
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can create appointments via this endpoint",
        )

    # Verify lead exists
    lead = db.query(Lead).filter(Lead.id == appt_data.lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    # Create appointment
    appointment = Appointment(
        lead_id=appt_data.lead_id,
        assigned_rep_id=lead.assigned_rep_id,  # Use lead's assigned rep if available
        appointment_date=appt_data.appointment_date,
        appointment_time=appt_data.appointment_time,
        scheduled_duration_minutes=appt_data.scheduled_duration_minutes or 90,
        appointment_status="scheduled",
        confirmation_status="pending",
        appointment_address=appt_data.appointment_address,
    )

    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="appointment_created",
        entity_type="appointment",
        entity_id=appointment.id,
        details=f"Appointment created by confirmation team for lead {appt_data.lead_id}",
    )

    return appointment


@router.get("/my-queue", response_model=List[MyQueueItem])
def get_my_confirmation_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get appointments assigned for confirmation by the current user.
    Allowed for confirmation and admin roles.

    Args:
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        List of appointments assigned to current user

    Raises:
        HTTPException: 403 if not authorized
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can access this resource",
        )

    # Get appointments assigned to current user
    appointments = (
        db.query(Appointment)
        .filter(Appointment.assigned_rep_id == current_user.id)
        .filter(Appointment.confirmation_status.in_(["pending", "confirming"]))
        .order_by(Appointment.appointment_date.asc())
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
                    "appointment_date": appt.appointment_date,
                    "appointment_time": appt.appointment_time,
                    "confirmation_status": appt.confirmation_status,
                    "confirmation_attempts_count": appt.confirmation_attempts_count or 0,
                }
            )

    return queue


@router.post("/{appointment_id}/attempt")
def log_confirmation_attempt_enhanced(
    appointment_id: int,
    attempt_data: ConfirmationAttemptWithOutcome,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Log an enhanced confirmation attempt with detailed outcome.
    Outcomes: confirmed, no_answer, reschedule_requested, not_interested.
    Allowed for confirmation and admin roles.

    Args:
        appointment_id: ID of the appointment
        attempt_data: Confirmation attempt with outcome
        current_user: Current authenticated user (confirmation or admin)
        db: Database session

    Returns:
        Confirmation attempt result

    Raises:
        HTTPException: 403 if not authorized, 404 if appointment not found
    """
    # Check role
    if current_user.role not in ["confirmation", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only confirmation and admin users can access this resource",
        )

    appointment = (
        db.query(Appointment).filter(Appointment.id == appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )

    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()

    # Increment attempt count
    appointment.confirmation_attempts_count = (appointment.confirmation_attempts_count or 0) + 1
    appointment.last_confirmation_attempt_at = datetime.utcnow()

    # Handle outcome
    if attempt_data.outcome == "confirmed":
        appointment.confirmation_status = "confirmed"
        appointment.confirmed_by_user_id = current_user.id
        if lead:
            lead.deal_status = "confirmed"
    elif attempt_data.outcome == "no_answer":
        appointment.confirmation_status = "confirming"
        # Auto-schedule reschedule request if too many attempts
        if appointment.confirmation_attempts_count >= 3:
            appointment.confirmation_status = "reschedule_requested"
    elif attempt_data.outcome == "reschedule_requested":
        appointment.confirmation_status = "reschedule_requested"
        # Store reschedule date in appointment_date if provided
        if attempt_data.next_callback_date:
            appointment.appointment_date = attempt_data.next_callback_date
    elif attempt_data.outcome == "not_interested":
        appointment.confirmation_status = "unconfirmed"
        if lead:
            lead.deal_status = "closed_lost"

    db.add(appointment)
    if lead:
        db.add(lead)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="confirm_attempt",
        entity_type="appointment",
        entity_id=appointment.id,
        new_value=attempt_data.outcome,
        details=f"Confirmation attempt #{appointment.confirmation_attempts_count}: {attempt_data.outcome}",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "appointment_id": appointment.id,
        "lead_id": appointment.lead_id,
        "outcome": attempt_data.outcome,
        "confirmation_attempts_count": appointment.confirmation_attempts_count,
        "confirmation_status": appointment.confirmation_status,
        "last_confirmation_attempt_at": appointment.last_confirmation_attempt_at,
    }
