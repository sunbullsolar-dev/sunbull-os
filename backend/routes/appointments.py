"""Appointment management routes with strict rep execution flow."""
from datetime import datetime, timedelta, date, time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Appointment, Lead, User, FollowUp, RehashEntry, Task
from app.auth import get_current_user
from services.audit import create_audit_log
from services.actions import log_action
from services.outcome_engine import process_outcome, OutcomeError, check_open_tasks

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

# ---- Valid status transitions ----
VALID_APPOINTMENT_STATUSES = {
    "scheduled", "confirmed", "en_route", "arrived",
    "completed", "rescheduled", "no_show", "cancelled",
}

VALID_LEAD_STATUSES = {
    "new", "confirmed", "dispatched", "on_the_way", "arrived",
    "held", "closed", "not_closed", "no_show", "declined",
    # Legacy statuses kept for existing data
    "qualifying", "confirming", "unconfirmed", "reschedule",
    "appointed", "closed_won", "closed_lost", "follow_up", "rehash",
    "canceled", "proposal_sent", "follow_up_required",
}

# Strict rep flow: each step can only go forward
REP_FLOW_ORDER = ["scheduled", "confirmed", "en_route", "arrived", "completed"]


class AppointmentCreate(BaseModel):
    lead_id: int
    assigned_rep_id: int
    appointment_date: str
    appointment_time: str
    scheduled_duration_minutes: int = 90
    appointment_address: Optional[str] = None
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    scheduled_duration_minutes: Optional[int] = None
    notes: Optional[str] = None


class AppointmentResult(BaseModel):
    outcome: str  # HELD: closed, proposal_presented, proposal_sent, follow_up_required, declined
                  # NOT HELD: no_show, not_home, canceled, reschedule_requested
    notes: str
    follow_up_date: Optional[str] = None  # YYYY-MM-DD, required for certain outcomes
    follow_up_note: Optional[str] = None  # Required when follow_up_date is set
    # Structured execution fields (required for held appointments)
    homeowner_present: Optional[bool] = None
    decision_maker_present: Optional[bool] = None
    pitch_delivered: Optional[bool] = None
    proposal_sent: Optional[bool] = None


class AppointmentResponse(BaseModel):
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
    lead_name: Optional[str] = None
    appointment_address: Optional[str] = None

    class Config:
        from_attributes = True


def _check_appointment_spacing(
    db: Session, rep_id: int, appointment_date: date,
    appointment_time: time, duration_minutes: int
) -> bool:
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_rep_id == rep_id,
            Appointment.appointment_date == appointment_date,
            Appointment.appointment_status != "cancelled",
        )
        .all()
    )

    proposed_start = appointment_time.hour * 60 + appointment_time.minute
    proposed_end = proposed_start + duration_minutes

    for existing in appointments:
        ex_start = existing.appointment_time.hour * 60 + existing.appointment_time.minute
        ex_end = ex_start + existing.scheduled_duration_minutes
        if proposed_start < ex_end + 90 and proposed_end > ex_start - 90:
            return False
    return True


@router.post("", response_model=AppointmentResponse)
def create_appointment(
    appointment_data: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new appointment. Logs action to timeline."""
    lead = db.query(Lead).filter(Lead.id == appointment_data.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        appt_date = datetime.strptime(appointment_data.appointment_date, "%Y-%m-%d").date()
        appt_time = datetime.strptime(appointment_data.appointment_time, "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date/time format")

    if not _check_appointment_spacing(
        db, appointment_data.assigned_rep_id, appt_date, appt_time,
        appointment_data.scheduled_duration_minutes,
    ):
        raise HTTPException(status_code=400, detail="Appointment violates 90-minute spacing")

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

    create_audit_log(
        db=db, user_id=current_user.id, action="create",
        entity_type="appointment", entity_id=appointment.id,
        new_value=f"Appointment for lead {appointment.lead_id}",
        details=f"Scheduled for {appt_date} at {appt_time}",
    )

    log_action(
        db=db, lead_id=lead.id, action_type="created",
        rep_id=appointment.assigned_rep_id,
        appointment_id=appointment.id,
        note=f"Appointment scheduled for {appt_date} at {appt_time}",
    )

    db.commit()
    db.refresh(appointment)
    return appointment


@router.get("", response_model=List[AppointmentResponse])
def list_appointments(
    assigned_rep_id: Optional[int] = Query(None),
    appointment_status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List appointments filtered by role/rep, enriched with lead name."""
    query = db.query(Appointment)

    if current_user.role == "rep":
        query = query.filter(Appointment.assigned_rep_id == current_user.id)
    elif current_user.role == "admin" and assigned_rep_id:
        query = query.filter(Appointment.assigned_rep_id == assigned_rep_id)

    if appointment_status:
        query = query.filter(Appointment.appointment_status == appointment_status)

    appointments = (
        query.order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc())
        .offset(skip).limit(limit).all()
    )

    # Enrich with lead names
    lead_ids = list({a.lead_id for a in appointments if a.lead_id})
    leads_map = {}
    if lead_ids:
        leads = db.query(Lead).filter(Lead.id.in_(lead_ids)).all()
        leads_map = {l.id: l for l in leads}

    result = []
    for a in appointments:
        data = AppointmentResponse.model_validate(a).model_dump()
        lead = leads_map.get(a.lead_id)
        if lead:
            data["lead_name"] = f"{lead.first_name} {lead.last_name}"
            data["appointment_address"] = getattr(lead, "property_address", None) or f"{lead.city}, {lead.state}" if lead.city else None
        result.append(data)

    return result


@router.get("/calendar/{rep_id}")
def get_rep_calendar(
    rep_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get rep's calendar view data."""
    if current_user.role == "rep" and current_user.id != rep_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    rep = db.query(User).filter(User.id == rep_id).first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

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

    # Batch load leads to avoid N+1
    lead_ids = list({a.lead_id for a in appointments if a.lead_id})
    leads_map = {}
    if lead_ids:
        leads_list = db.query(Lead).filter(Lead.id.in_(lead_ids)).all()
        leads_map = {l.id: l for l in leads_list}

    calendar_data = []
    for appt in appointments:
        lead = leads_map.get(appt.lead_id)
        calendar_data.append({
            "id": appt.id,
            "lead_id": appt.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
            "appointment_date": appt.appointment_date,
            "appointment_time": appt.appointment_time,
            "scheduled_duration_minutes": appt.scheduled_duration_minutes,
            "appointment_status": appt.appointment_status,
            "notes": appt.notes,
        })

    return {
        "rep_id": rep_id, "rep_name": rep.full_name,
        "start_date": start_date, "end_date": end_date,
        "appointments": calendar_data,
    }


@router.put("/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: int,
    appointment_data: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update/reschedule an appointment."""
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if current_user.role == "rep" and appointment.assigned_rep_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if appointment_data.appointment_date or appointment_data.appointment_time:
        appt_date = appointment.appointment_date
        appt_time = appointment.appointment_time
        duration = appointment.scheduled_duration_minutes

        if appointment_data.appointment_date:
            try:
                appt_date = datetime.strptime(appointment_data.appointment_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format")

        if appointment_data.appointment_time:
            try:
                appt_time = datetime.strptime(appointment_data.appointment_time, "%H:%M").time()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid time format")

        if appointment_data.scheduled_duration_minutes:
            duration = appointment_data.scheduled_duration_minutes

        if not _check_appointment_spacing(db, appointment.assigned_rep_id, appt_date, appt_time, duration):
            raise HTTPException(status_code=400, detail="Violates 90-minute spacing")

        appointment.reschedule_count = (appointment.reschedule_count or 0) + 1
        appointment.appointment_date = appt_date
        appointment.appointment_time = appt_time

    if appointment_data.scheduled_duration_minutes:
        appointment.scheduled_duration_minutes = appointment_data.scheduled_duration_minutes
    if appointment_data.notes:
        appointment.notes = appointment_data.notes

    db.add(appointment)
    db.flush()

    create_audit_log(
        db=db, user_id=current_user.id, action="update",
        entity_type="appointment", entity_id=appointment.id,
        details=f"Appointment rescheduled (count: {appointment.reschedule_count})",
    )

    db.commit()
    db.refresh(appointment)
    return appointment


# =================================================================
# QUICK STATUS UPDATE (used by RepApptCard and DispatchPage)
# =================================================================

class StatusUpdate(BaseModel):
    """Simple status update model."""
    appointment_status: str


@router.put("/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int,
    data: StatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Quick status update for appointments (confirm, en_route, arrived, etc.)."""
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if current_user.role == "rep" and appointment.assigned_rep_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    new_status = data.appointment_status
    if new_status not in VALID_APPOINTMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    old_status = appointment.appointment_status

    # Prevent backward status transitions — only forward moves allowed
    # Terminal states cannot be changed by reps
    terminal = {"completed", "no_show", "cancelled", "rescheduled"}
    if old_status in terminal and current_user.role != "admin":
        raise HTTPException(status_code=400, detail=f"Cannot change status from '{old_status}' — appointment is finalized")

    # Enforce forward-only flow for reps
    if current_user.role == "rep" and old_status in REP_FLOW_ORDER and new_status in REP_FLOW_ORDER:
        old_idx = REP_FLOW_ORDER.index(old_status)
        new_idx = REP_FLOW_ORDER.index(new_status)
        if new_idx < old_idx:
            raise HTTPException(status_code=400, detail=f"Cannot move backward from '{old_status}' to '{new_status}'")

    appointment.appointment_status = new_status

    # Update lead status to mirror appointment status where appropriate
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if lead:
        status_map = {
            "confirmed": "confirmed",
            "en_route": "dispatched",
            "arrived": "arrived",
        }
        if new_status in status_map:
            lead.deal_status = status_map[new_status]

    create_audit_log(
        db=db, user_id=current_user.id, action="status_change",
        entity_type="appointment", entity_id=appointment.id,
        details=f"Status: {old_status} -> {new_status}",
    )

    db.commit()
    db.refresh(appointment)
    return {"ok": True, "appointment_status": appointment.appointment_status}


# =================================================================
# REP EXECUTION FLOW: En Route → Arrived → Complete
# =================================================================

@router.post("/{appointment_id}/en-route")
def mark_en_route(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Step 1: Rep marks themselves en route.
    Only allowed from 'scheduled' or 'confirmed' status.
    Updates appointment + lead status. Logs action.
    """
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(status_code=403, detail="Not your appointment")

    if appointment.appointment_status not in ("scheduled", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot go en_route from '{appointment.appointment_status}'. Must be scheduled or confirmed.",
        )

    # Update appointment
    appointment.appointment_status = "en_route"
    appointment.rep_checked_in_at = datetime.utcnow()

    # Update lead status
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if lead:
        lead.deal_status = "on_the_way"

    db.flush()

    create_audit_log(
        db=db, user_id=current_user.id, action="en_route",
        entity_type="appointment", entity_id=appointment.id,
        details="Rep en route",
    )

    log_action(
        db=db, lead_id=appointment.lead_id, action_type="en_route",
        rep_id=current_user.id, appointment_id=appointment.id,
        note=f"{current_user.full_name} is en route",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "id": appointment.id,
        "appointment_status": appointment.appointment_status,
        "lead_status": lead.deal_status if lead else None,
    }


@router.post("/{appointment_id}/arrived")
def mark_arrived(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Step 2: Rep marks themselves arrived.
    Only allowed from 'en_route' status.
    Updates appointment + lead status. Logs action.
    """
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(status_code=403, detail="Not your appointment")

    if appointment.appointment_status != "en_route":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot arrive from '{appointment.appointment_status}'. Must be en_route.",
        )

    appointment.appointment_status = "arrived"
    appointment.actual_start_time = datetime.utcnow()

    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if lead:
        lead.deal_status = "arrived"

    db.flush()

    create_audit_log(
        db=db, user_id=current_user.id, action="arrived",
        entity_type="appointment", entity_id=appointment.id,
        details="Rep arrived at location",
    )

    log_action(
        db=db, lead_id=appointment.lead_id, action_type="arrived",
        rep_id=current_user.id, appointment_id=appointment.id,
        note=f"{current_user.full_name} arrived at location",
    )

    db.commit()
    db.refresh(appointment)

    return {
        "id": appointment.id,
        "appointment_status": appointment.appointment_status,
        "lead_status": lead.deal_status if lead else None,
    }


# Outcome classification
HELD_OUTCOMES = {"closed", "proposal_presented", "proposal_sent", "follow_up_required", "declined"}
NOT_HELD_OUTCOMES = {"no_show", "not_home", "canceled", "reschedule_requested"}
ALL_OUTCOMES = HELD_OUTCOMES | NOT_HELD_OUTCOMES

# Outcomes that REQUIRE a follow-up date + note
REQUIRES_FOLLOW_UP = {"proposal_presented", "proposal_sent", "follow_up_required", "not_home", "reschedule_requested"}


@router.post("/{appointment_id}/complete")
def complete_appointment(
    appointment_id: int,
    result_data: AppointmentResult,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Step 3: Rep completes the appointment with held/not-held outcome.

    NOW POWERED BY THE OUTCOME ENGINE — process_outcome() handles:
    - Validation (status checks, notes, follow-up requirements)
    - Lead status updates
    - Automatic task creation (follow-ups, rehash, reschedule, project)
    - Ownership tracking (runner, closer)
    - Audit + action logging
    """
    # Validate structured execution fields for held appointments
    held_outcomes = {"closed", "proposal_presented", "proposal_sent", "follow_up_required"}
    if result_data.outcome in held_outcomes:
        if result_data.homeowner_present is None:
            raise HTTPException(status_code=400, detail="homeowner_present is required for held appointments")
        if result_data.decision_maker_present is None:
            raise HTTPException(status_code=400, detail="decision_maker_present is required for held appointments")
        if result_data.pitch_delivered is None:
            raise HTTPException(status_code=400, detail="pitch_delivered is required for held appointments")

    try:
        result = process_outcome(
            db=db,
            appointment_id=appointment_id,
            outcome=result_data.outcome,
            notes=result_data.notes,
            current_user=current_user,
            follow_up_date=result_data.follow_up_date,
            follow_up_note=result_data.follow_up_note,
        )

        # Save structured fields to appointment
        appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
        if appt:
            if result_data.homeowner_present is not None:
                appt.homeowner_present = result_data.homeowner_present
            if result_data.decision_maker_present is not None:
                appt.decision_maker_present = result_data.decision_maker_present
            if result_data.pitch_delivered is not None:
                appt.pitch_delivered = result_data.pitch_delivered
            if result_data.proposal_sent is not None:
                appt.proposal_sent = result_data.proposal_sent
            db.commit()

        return result
    except OutcomeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# =================================================================
# Legacy endpoints (kept for backwards compatibility)
# =================================================================

@router.post("/{appointment_id}/result", response_model=AppointmentResponse)
def submit_appointment_result(
    appointment_id: int,
    result_data: AppointmentResult,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legacy: Submit appointment result. Use /complete instead for strict flow."""
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()

    appointment.appointment_status = "completed"
    appointment.outcome = result_data.outcome
    appointment.notes = result_data.notes

    db.add(appointment)
    db.flush()

    log_action(
        db=db, lead_id=appointment.lead_id,
        action_type="completed",
        rep_id=current_user.id,
        appointment_id=appointment.id,
        note=f"Legacy result: {result_data.outcome} - {result_data.notes}",
    )

    create_audit_log(
        db=db, user_id=current_user.id, action="complete",
        entity_type="appointment", entity_id=appointment.id,
        new_value=result_data.outcome,
        details=f"Completed with outcome: {result_data.outcome}",
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
    """Legacy check-in. Use /en-route instead."""
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    appointment.appointment_status = "en_route"
    appointment.rep_checked_in_at = datetime.utcnow()
    db.flush()

    log_action(
        db=db, lead_id=appointment.lead_id, action_type="en_route",
        rep_id=current_user.id, appointment_id=appointment.id,
        note="Legacy check-in",
    )

    db.commit()
    db.refresh(appointment)
    return {"id": appointment.id, "appointment_status": appointment.appointment_status}


@router.post("/{appointment_id}/checkout")
def check_out_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legacy check-out."""
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    appointment.rep_checked_out_at = datetime.utcnow()
    db.flush()
    db.commit()
    db.refresh(appointment)
    return {"id": appointment.id, "rep_checked_out_at": appointment.rep_checked_out_at}
