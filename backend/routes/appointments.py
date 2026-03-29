"""Appointment management routes with strict rep execution flow."""
from datetime import datetime, timedelta, date, time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Appointment, Lead, User, FollowUp, RehashEntry
from app.auth import get_current_user
from services.audit import create_audit_log
from services.actions import log_action

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
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List appointments filtered by role/rep."""
    query = db.query(Appointment)

    if current_user.role == "rep":
        query = query.filter(Appointment.assigned_rep_id == current_user.id)
    elif assigned_rep_id:
        query = query.filter(Appointment.assigned_rep_id == assigned_rep_id)

    if appointment_status:
        query = query.filter(Appointment.appointment_status == appointment_status)

    return (
        query.order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc())
        .offset(skip).limit(limit).all()
    )


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

    calendar_data = []
    for appt in appointments:
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
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

    HELD outcomes (rep met homeowner):
      - closed → deal signed, lead done
      - proposal_presented → presented in person, follow-up required
      - proposal_sent → sent after visit, follow-up required
      - follow_up_required → conversation happened, no proposal yet
      - declined → homeowner declined, optional rehash

    NOT HELD outcomes (appointment did not occur):
      - no_show → homeowner not there, goes to rehash queue
      - not_home → missing decision maker, reschedule required
      - canceled → mark inactive or reassign
      - reschedule_requested → homeowner asked to reschedule, must create new appointment

    Guardrails:
      - proposal_presented, proposal_sent, follow_up_required, not_home, reschedule_requested
        REQUIRE follow_up_date and follow_up_note
      - no_show and declined auto-create rehash entry
      - notes always required
    """
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if current_user.id != appointment.assigned_rep_id:
        raise HTTPException(status_code=403, detail="Not your appointment")

    if appointment.appointment_status != "arrived":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete from '{appointment.appointment_status}'. Must be arrived.",
        )

    outcome = result_data.outcome
    if outcome not in ALL_OUTCOMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome '{outcome}'. Must be one of: {', '.join(sorted(ALL_OUTCOMES))}",
        )

    if not result_data.notes or not result_data.notes.strip():
        raise HTTPException(status_code=400, detail="Notes are required")

    # Enforce follow-up date for outcomes that require it
    if outcome in REQUIRES_FOLLOW_UP:
        if not result_data.follow_up_date:
            raise HTTPException(
                status_code=400,
                detail=f"follow_up_date is required for '{outcome}' outcome",
            )
        if not result_data.follow_up_note or not result_data.follow_up_note.strip():
            raise HTTPException(
                status_code=400,
                detail=f"follow_up_note is required for '{outcome}' outcome",
            )
        # Validate date format
        try:
            fu_date = datetime.strptime(result_data.follow_up_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="follow_up_date must be YYYY-MM-DD format")

    # Determine held vs not held
    is_held = outcome in HELD_OUTCOMES

    # ── Update Appointment ────────────────────────────────────
    appointment.actual_end_time = datetime.utcnow()
    appointment.rep_checked_out_at = datetime.utcnow()
    appointment.outcome = outcome
    appointment.notes = result_data.notes

    if outcome == "canceled":
        appointment.appointment_status = "cancelled"
    elif outcome == "no_show":
        appointment.appointment_status = "no_show"
    elif outcome == "not_home":
        appointment.appointment_status = "no_show"  # Not held
        appointment.reschedule_count = (appointment.reschedule_count or 0) + 1
    elif outcome == "reschedule_requested":
        appointment.appointment_status = "rescheduled"
        appointment.reschedule_count = (appointment.reschedule_count or 0) + 1
    else:
        appointment.appointment_status = "completed"

    # ── Update Lead ───────────────────────────────────────────
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if lead:
        lead.is_held = is_held
        lead.last_outcome = outcome

        # HELD outcome → lead status mapping
        if outcome == "closed":
            lead.deal_status = "closed_won"
            lead.follow_up_required = False
        elif outcome in ("proposal_presented", "proposal_sent"):
            lead.deal_status = "proposal_sent"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = result_data.follow_up_note
        elif outcome == "follow_up_required":
            lead.deal_status = "follow_up"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = result_data.follow_up_note

        # HELD: declined → optional rehash
        elif outcome == "declined":
            lead.deal_status = "declined"
            lead.follow_up_required = False

        # NOT HELD outcome → lead status mapping
        elif outcome == "no_show":
            lead.deal_status = "no_show"
            lead.follow_up_required = False
        elif outcome == "not_home":
            lead.deal_status = "reschedule"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = result_data.follow_up_note
        elif outcome == "canceled":
            lead.deal_status = "canceled"
            lead.follow_up_required = False
        elif outcome == "reschedule_requested":
            lead.deal_status = "reschedule"
            lead.follow_up_required = True
            lead.next_follow_up_date = fu_date
            lead.follow_up_note = result_data.follow_up_note

    db.flush()

    # ── Audit + Action Logging ────────────────────────────────
    held_label = "HELD" if is_held else "NOT HELD"

    create_audit_log(
        db=db, user_id=current_user.id, action="complete",
        entity_type="appointment", entity_id=appointment.id,
        new_value=outcome,
        details=f"{held_label}: {outcome}",
    )

    log_action(
        db=db, lead_id=appointment.lead_id, action_type=outcome,
        rep_id=current_user.id, appointment_id=appointment.id,
        note=f"[{held_label}] {result_data.notes}",
    )

    # ── Follow-Up Creation (for outcomes requiring follow-up) ────
    if outcome in REQUIRES_FOLLOW_UP and lead:
        reason_map = {
            "proposal_presented": "Follow up on proposal presented in person",
            "proposal_sent": "Follow up on proposal sent after visit",
            "follow_up_required": "Follow up — conversation but no proposal yet",
            "not_home": "Reschedule — decision maker not home",
            "reschedule_requested": "Homeowner requested reschedule",
        }
        follow_up = FollowUp(
            lead_id=lead.id,
            assigned_rep_id=appointment.assigned_rep_id,
            reason=reason_map.get(outcome, f"Follow up from appointment #{appointment.id}"),
            scheduled_date=fu_date,
            status="pending",
            notes=result_data.follow_up_note,
        )
        db.add(follow_up)

        log_action(
            db=db, lead_id=lead.id, action_type="follow_up_created",
            rep_id=current_user.id, appointment_id=appointment.id,
            note=f"Follow-up scheduled for {result_data.follow_up_date}: {result_data.follow_up_note}",
        )

    # ── Rehash Queue (for no_show + declined) ───────────────────
    if outcome in ("no_show", "declined") and lead:
        rehash_reason_map = {
            "no_show": f"No-show on appointment #{appointment.id}",
            "declined": f"Declined after held appointment #{appointment.id}",
        }
        rehash = RehashEntry(
            lead_id=lead.id,
            original_rep_id=appointment.assigned_rep_id,
            reason=rehash_reason_map.get(outcome, f"Rehash from appointment #{appointment.id}"),
            status="pending",
        )
        db.add(rehash)

        log_action(
            db=db, lead_id=lead.id, action_type="rehash_queued",
            rep_id=current_user.id, appointment_id=appointment.id,
            note=f"Added to rehash queue after {outcome}",
        )

    db.commit()
    db.refresh(appointment)

    return {
        "id": appointment.id,
        "appointment_status": appointment.appointment_status,
        "outcome": outcome,
        "is_held": is_held,
        "lead_status": lead.deal_status if lead else None,
        "follow_up_date": result_data.follow_up_date,
        "follow_up_note": result_data.follow_up_note,
    }


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
