"""Appointment management routes."""
from datetime import datetime, timedelta, date, time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Appointment, Lead, User, FollowUp
from app.auth import get_current_user
from services.audit import create_audit_log

router = APIRouter(prefix="/api/appointments", tags=["appointments"])


class AppointmentCreate(BaseModel):
    """Create appointment request model."""

    lead_id: int
    assigned_rep_id: int
    appointment_date: str  # "YYYY-MM-DD"
    appointment_time: str  # "HH:MM"
    scheduled_duration_minutes: int = 90
    appointment_address: Optional[str] = None
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    """Update appointment request model."""

    appointment_date: Optional[str] = None  # "YYYY-MM-DD"
    appointment_time: Optional[str] = None  # "HH:MM"
    scheduled_duration_minutes: Optional[int] = None
    notes: Optional[str] = None


class AppointmentResult(BaseModel):
    """Appointment result submission model."""

    outcome: str  # closed_won, closed_lost, follow_up, no_show
    notes: str
    photo_proof_url: Optional[str] = None
    voice_memo_url: Optional[str] = None


class AppointmentResponse(BaseModel):
    """Appointment response model."""

    id: int
    lead_id: int
    assigned_rep_id: int
    appointment_date: date
    appointment_time: time
    scheduled_duration_minutes: int
    appointment_status: str
    outcome: Optional[str]
    notes: Optional[str]
    photo_proof_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


def _check_appointment_spacing(
    db: Session, rep_id: int, appointment_date: date, appointment_time: time, duration_minutes: int
) -> bool:
    """
    Check if appointment respects 90-minute spacing from other appointments.

    Args:
        db: Database session
        rep_id: Rep ID
        appointment_date: Proposed appointment date
        appointment_time: Proposed appointment start time
        duration_minutes: Duration of appointment

    Returns:
        True if appointment spacing is valid, False otherwise
    """
    # Find all appointments for this rep on the same date
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_rep_id == rep_id,
            Appointment.appointment_date == appointment_date,
            Appointment.appointment_status != "cancelled",
        )
        .all()
    )

    # Convert appointment_time to minutes for comparison
    proposed_start_minutes = appointment_time.hour * 60 + appointment_time.minute
    proposed_end_minutes = proposed_start_minutes + duration_minutes

    for existing in appointments:
        existing_start_minutes = existing.appointment_time.hour * 60 + existing.appointment_time.minute
        existing_end_minutes = existing_start_minutes + existing.scheduled_duration_minutes

        # Check if proposed appointment overlaps or is too close (less than 90 min gap)
        # Need 90 min gap between end of one and start of next
        if proposed_start_minutes < existing_end_minutes + 90:
            if proposed_end_minutes > existing_start_minutes - 90:
                return False

    return True


@router.post("", response_model=AppointmentResponse)
def create_appointment(
    appointment_data: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new appointment.

    ENFORCES 90-minute spacing between appointments for same rep.
    Creates an audit log entry.

    Args:
        appointment_data: Appointment creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created Appointment record

    Raises:
        HTTPException: 400 if spacing constraint violated, 404 if lead not found
    """
    # Verify lead exists
    lead = db.query(Lead).filter(Lead.id == appointment_data.lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Parse date and time strings
    try:
        appt_date = datetime.strptime(appointment_data.appointment_date, "%Y-%m-%d").date()
        appt_time = datetime.strptime(appointment_data.appointment_time, "%H:%M").time()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date (YYYY-MM-DD) or time (HH:MM) format",
        )

    # Check appointment spacing
    if not _check_appointment_spacing(
        db,
        appointment_data.assigned_rep_id,
        appt_date,
        appt_time,
        appointment_data.scheduled_duration_minutes,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment violates 90-minute spacing requirement",
        )

    appointment = Appointment(
        lead_id=appointment_data.lead_id,
        assigned_rep_id=appointment_data.assigned_rep_id,
        appointment_date=appt_date,
        appointment_time=appt_time,
        scheduled_duration_minutes=appointment_data.scheduled_duration_minutes,
        appointment_status="scheduled",
        appointment_address=appointment_data.appointment_address or lead.property_address,
        notes=appointment_data.notes,
    )

    db.add(appointment)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="create",
        entity_type="appointment",
        entity_id=appointment.id,
        new_value=f"Appointment for lead {appointment.lead_id}",
        details=f"Scheduled for {appointment.appointment_date} at {appointment.appointment_time}",
    )

    db.commit()
    db.refresh(appointment)
    return appointment


@router.get("", response_model=List[AppointmentResponse])
def list_appointments(
    assigned_rep_id: Optional[int] = Query(None),
    appointment_status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List appointments filtered by role/rep.

    Reps only see their own appointments. Admins see all appointments.

    Args:
        assigned_rep_id: Filter by assigned rep ID
        appointment_status: Filter by appointment status
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of Appointment records
    """
    query = db.query(Appointment)

    # Filter by role
    if current_user.role == "rep":
        query = query.filter(Appointment.assigned_rep_id == current_user.id)
    elif assigned_rep_id:
        query = query.filter(Appointment.assigned_rep_id == assigned_rep_id)

    # Filter by appointment_status
    if appointment_status:
        query = query.filter(Appointment.appointment_status == appointment_status)

    return (
        query.order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/calendar/{rep_id}")
def get_rep_calendar(
    rep_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get rep's calendar view data.

    Args:
        rep_id: ID of the rep
        current_user: Current authenticated user
        db: Database session

    Returns:
        Calendar data with appointments

    Raises:
        HTTPException: 403 if unauthorized, 404 if rep not found
    """
    # Check authorization
    if current_user.role == "rep" and current_user.id != rep_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this rep's calendar",
        )

    # Verify rep exists
    rep = db.query(User).filter(User.id == rep_id).first()
    if not rep:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rep not found")

    # Get next 30 days of appointments
    start_date = datetime.utcnow().date()
    end_date = start_date + timedelta(days=30)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_rep_id == rep_id,
            Appointment.appointment_date >= start_date,
            Appointment.appointment_date <= end_date,
        )
        .order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc())
        .all()
    )

    calendar_data = []
    for appt in appointments:
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
        calendar_data.append(
            {
                "id": appt.id,
                "lead_id": appt.lead_id,
                "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
                "appointment_date": appt.appointment_date,
                "appointment_time": appt.appointment_time,
                "scheduled_duration_minutes": appt.scheduled_duration_minutes,
                "appointment_status": appt.appointment_status,
                "notes": appt.notes,
            }
        )

    return {
        "rep_id": rep_id,
        "rep_name": rep.full_name,
        "start_date": start_date,
        "end_date": end_date,
        "appointments": calendar_data,
    }


@router.put("/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: int,
    appointment_data: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update/reschedule an appointment.

    Increments reschedule_count when rescheduling.

    Args:
        appointment_id: ID of the appointment
        appointment_data: Updated appointment data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated Appointment record

    Raises:
        HTTPException: 404 if not found, 403 if unauthorized, 400 if spacing violated
    """
    appointment = (
        db.query(Appointment).filter(Appointment.id == appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )

    # Check authorization
    if (
        current_user.role == "rep"
        and appointment.assigned_rep_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this appointment",
        )

    # If rescheduling, check spacing
    if appointment_data.appointment_date or appointment_data.appointment_time:
        appt_date = appointment.appointment_date
        appt_time = appointment.appointment_time
        duration = appointment.scheduled_duration_minutes

        if appointment_data.appointment_date:
            try:
                appt_date = datetime.strptime(appointment_data.appointment_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid date format (YYYY-MM-DD)",
                )

        if appointment_data.appointment_time:
            try:
                appt_time = datetime.strptime(appointment_data.appointment_time, "%H:%M").time()
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid time format (HH:MM)",
                )

        if appointment_data.scheduled_duration_minutes:
            duration = appointment_data.scheduled_duration_minutes

        if not _check_appointment_spacing(
            db, appointment.assigned_rep_id, appt_date, appt_time, duration
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New appointment time violates 90-minute spacing requirement",
            )

        # Increment reschedule count
        appointment.reschedule_count = (appointment.reschedule_count or 0) + 1
        appointment.appointment_date = appt_date
        appointment.appointment_time = appt_time

    # Update fields
    if appointment_data.scheduled_duration_minutes:
        appointment.scheduled_duration_minutes = appointment_data.scheduled_duration_minutes

    if appointment_data.notes:
        appointment.notes = appointment_data.notes

    db.add(appointment)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="update",
        entity_type="appointment",
        entity_id=appointment.id,
        details=f"Appointment rescheduled (reschedule_count: {appointment.reschedule_count})",
    )

    db.commit()
    db.refresh(appointment)
    return appointment


@router.post("/{appointment_id}/result", response_model=AppointmentResponse)
def submit_appointment_result(
    appointment_id: int,
    result_data: AppointmentResult,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Submit appointment result.

    MANDATORY: outcome, notes, and photo_proof_url required.
    If outcome = follow_up, auto-creates FollowUp record and sets lead.follow_up_required=True.
    Creates an audit log entry.

    Args:
        appointment_id: ID of the appointment
        result_data: Appointment result data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated Appointment record

    Raises:
        HTTPException: 404 if not found, 422 if required fields missing
    """
    appointment = (
        db.query(Appointment).filter(Appointment.id == appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )

    # Validate required fields
    if not result_data.outcome or not result_data.notes or not result_data.photo_proof_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="outcome, notes, and photo_proof_url are required",
        )

    # Get lead for follow-up handling
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()

    # Update appointment
    appointment.appointment_status = "completed"
    appointment.outcome = result_data.outcome
    appointment.notes = result_data.notes
    appointment.photo_proof_url = result_data.photo_proof_url
    appointment.voice_memo_url = result_data.voice_memo_url

    db.add(appointment)
    db.flush()

    # If outcome is follow_up, create FollowUp record and set lead flag
    if result_data.outcome == "follow_up" and lead:
        # Create FollowUp record
        follow_up = FollowUp(
            lead_id=appointment.lead_id,
            assigned_rep_id=appointment.assigned_rep_id,
            reason=f"Follow-up from appointment #{appointment.id}",
            scheduled_date=datetime.utcnow() + timedelta(days=3),
            status="pending",
            notes=result_data.notes,
        )
        db.add(follow_up)

        # Update lead
        lead.follow_up_required = True
        lead.next_follow_up_date = datetime.utcnow() + timedelta(days=3)
        db.add(lead)

    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="complete",
        entity_type="appointment",
        entity_id=appointment.id,
        new_value=result_data.outcome,
        details=f"Appointment completed with outcome: {result_data.outcome}",
    )

    db.commit()
    db.refresh(appointment)
    return appointment


@router.post("/{appointment_id}/checkin")
def check_in_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Rep check-in for appointment.

    Updates appointment_status to en_route and sets rep_checked_in_at timestamp.

    Args:
        appointment_id: ID of the appointment
        current_user: Current authenticated user (rep)
        db: Database session

    Returns:
        Updated appointment data

    Raises:
        HTTPException: 404 if not found, 403 if unauthorized
    """
    appointment = (
        db.query(Appointment).filter(Appointment.id == appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )

    # Check authorization
    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to check in this appointment",
        )

    appointment.appointment_status = "en_route"
    appointment.rep_checked_in_at = datetime.utcnow()

    db.add(appointment)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="check_in",
        entity_type="appointment",
        entity_id=appointment.id,
        details="Rep checked in for appointment",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "id": appointment.id,
        "lead_id": appointment.lead_id,
        "appointment_status": appointment.appointment_status,
        "rep_checked_in_at": appointment.rep_checked_in_at,
    }


@router.post("/{appointment_id}/checkout")
def check_out_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Rep check-out for appointment.

    Sets rep_checked_out_at timestamp.

    Args:
        appointment_id: ID of the appointment
        current_user: Current authenticated user (rep)
        db: Database session

    Returns:
        Updated appointment data

    Raises:
        HTTPException: 404 if not found, 403 if unauthorized
    """
    appointment = (
        db.query(Appointment).filter(Appointment.id == appointment_id).first()
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )

    # Check authorization
    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to check out this appointment",
        )

    appointment.rep_checked_out_at = datetime.utcnow()

    db.add(appointment)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="check_out",
        entity_type="appointment",
        entity_id=appointment.id,
        details="Rep checked out from appointment",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "id": appointment.id,
        "lead_id": appointment.lead_id,
        "appointment_status": appointment.appointment_status,
        "rep_checked_out_at": appointment.rep_checked_out_at,
    }
