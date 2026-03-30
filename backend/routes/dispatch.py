"""Auto-dispatch engine routes."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models import Lead, User, Appointment
from app.auth import get_current_user, require_role
from services.audit import create_audit_log
from services.actions import log_action
from datetime import datetime, date, timedelta

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


class DispatchScore(BaseModel):
    """Dispatch score for a rep."""

    rep_id: int
    rep_name: str
    close_rate_score: float  # 40% weight
    availability_score: float  # 30% weight
    quality_match_score: float  # 20% weight
    location_proximity_score: float  # 10% weight
    total_score: float


def _calculate_rep_scores(
    db: Session,
    lead: Lead,
) -> List[dict]:
    """
    Calculate dispatch scores for all reps.

    Scoring breakdown:
    - Rep close rate (weighted 40%)
    - Rep availability / current load (weighted 30%)
    - Lead quality match (weighted 20%)
    - Location proximity - basic zip code matching (weighted 10%)

    Args:
        db: Database session
        lead: Lead to dispatch

    Returns:
        List of rep scores sorted by total score descending
    """
    reps = db.query(User).filter(User.role == "rep").all()
    scores = []

    for rep in reps:
        # Close rate score (40%)
        close_rate = rep.close_rate / 100.0  # Convert percentage to decimal
        close_rate_score = close_rate * 40

        # Availability/load score (30%)
        # Count current appointments
        appointments = (
            db.query(Appointment)
            .filter(Appointment.assigned_rep_id == rep.id)
            .count()
        )
        # Assume max capacity is 10 appointments per day
        availability_score = max(0, (1 - (appointments / 10)) * 30)

        # Quality match score (20%)
        # Higher quality leads should go to higher performers
        lead_quality = lead.lead_quality_score / 100.0
        quality_match_score = (close_rate * lead_quality) * 20

        # Location proximity score (10%)
        # Simple zip code prefix matching
        location_proximity_score = 0
        if rep.territory:
            # Assume territory contains zip code prefixes
            if lead.zip_code and lead.zip_code.startswith(rep.territory[:3]):
                location_proximity_score = 10
            else:
                location_proximity_score = 2  # Partial credit

        # Calculate total
        total_score = (
            close_rate_score
            + availability_score
            + quality_match_score
            + location_proximity_score
        )

        scores.append(
            {
                "rep_id": rep.id,
                "rep_name": rep.full_name,
                "close_rate_score": round(close_rate_score, 2),
                "availability_score": round(availability_score, 2),
                "quality_match_score": round(quality_match_score, 2),
                "location_proximity_score": round(location_proximity_score, 2),
                "total_score": round(total_score, 2),
            }
        )

    # Sort by total score descending
    scores.sort(key=lambda x: x["total_score"], reverse=True)
    return scores


@router.post("/auto-assign/{lead_id}")
def auto_assign_lead(
    lead_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Auto-assign a lead based on:
    - Rep close rate (weighted 40%)
    - Rep availability / current load (weighted 30%)
    - Lead quality match (weighted 20%)
    - Location proximity - basic zip code matching (weighted 10%)

    Args:
        lead_id: ID of the lead to assign
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Assignment result with selected rep and scoring breakdown

    Raises:
        HTTPException: 404 if lead not found, 400 if no reps available
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Calculate scores for all reps
    scores = _calculate_rep_scores(db, lead)

    if not scores:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No reps available for assignment",
        )

    # Select highest scoring rep
    best_rep = scores[0]
    lead.assigned_rep_id = best_rep["rep_id"]

    db.add(lead)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="auto_assign",
        entity_type="lead",
        entity_id=lead.id,
        new_value=best_rep["rep_id"],
        details=f"Auto-assigned to {best_rep['rep_name']} with score {best_rep['total_score']}",
    )

    db.commit()
    db.refresh(lead)

    return {
        "lead_id": lead_id,
        "assigned_rep_id": best_rep["rep_id"],
        "assigned_rep_name": best_rep["rep_name"],
        "assignment_score": best_rep["total_score"],
        "all_scores": scores,
    }


@router.get("/rep-scores/{lead_id}")
def get_dispatch_scores(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    View dispatch scoring for all reps.

    Args:
        lead_id: ID of the lead
        current_user: Current authenticated user
        db: Database session

    Returns:
        Scores for all reps for this lead

    Raises:
        HTTPException: 404 if lead not found
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Calculate scores
    scores = _calculate_rep_scores(db, lead)

    return {
        "lead_id": lead_id,
        "lead_name": f"{lead.first_name} {lead.last_name}",
        "lead_quality_score": lead.lead_quality_score,
        "lead_location": f"{lead.city}, {lead.state}",
        "rep_scores": scores,
    }


# ============================================================================
# DISPATCH QUEUE - Confirmed appointments ready for dispatch
# ============================================================================

@router.get("/queue")
def get_dispatch_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get confirmed appointments ready for dispatch."""
    if current_user.role not in ["admin"]:
        raise HTTPException(status_code=403, detail="Admin only")

    # Get appointments that are confirmed but not yet dispatched/completed
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.confirmation_status == "confirmed",
            Appointment.appointment_status.in_(["scheduled", "confirmed"]),
        )
        .order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc())
        .all()
    )

    queue = []
    for appt in appointments:
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
        rep = db.query(User).filter(User.id == appt.assigned_rep_id).first() if appt.assigned_rep_id else None
        backup = db.query(User).filter(User.id == appt.backup_rep_id).first() if getattr(appt, "backup_rep_id", None) else None

        # Check for timing conflicts
        conflicts = []
        if rep and appt.appointment_date:
            same_day = (
                db.query(Appointment)
                .filter(
                    Appointment.assigned_rep_id == rep.id,
                    Appointment.appointment_date == appt.appointment_date,
                    Appointment.id != appt.id,
                    Appointment.appointment_status.notin_(["cancelled", "no_show"]),
                )
                .all()
            )
            for c in same_day:
                if c.appointment_time and appt.appointment_time:
                    # Simple overlap check: within 90 min
                    c_mins = c.appointment_time.hour * 60 + c.appointment_time.minute
                    a_mins = appt.appointment_time.hour * 60 + appt.appointment_time.minute
                    if abs(c_mins - a_mins) < 90:
                        cl = db.query(Lead).filter(Lead.id == c.lead_id).first()
                        conflicts.append({
                            "appointment_id": c.id,
                            "time": str(c.appointment_time),
                            "lead_name": f"{cl.first_name} {cl.last_name}" if cl else "Unknown",
                        })

        queue.append({
            "appointment_id": appt.id,
            "lead_id": lead.id if lead else None,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
            "phone": lead.phone if lead else None,
            "address": lead.property_address if lead else appt.appointment_address,
            "city": lead.city if lead else None,
            "state": lead.state if lead else None,
            "monthly_bill": lead.average_monthly_bill if lead else None,
            "appointment_date": str(appt.appointment_date),
            "appointment_time": str(appt.appointment_time) if appt.appointment_time else None,
            "appointment_type": getattr(appt, "appointment_type", "in_person"),
            "dispatch_status": getattr(appt, "dispatch_status", "pending"),
            "assigned_rep_id": appt.assigned_rep_id,
            "assigned_rep_name": rep.full_name if rep else None,
            "backup_rep_id": getattr(appt, "backup_rep_id", None),
            "backup_rep_name": backup.full_name if backup else None,
            "rep_acknowledged": bool(getattr(appt, "rep_acknowledged_at", None)),
            "conflicts": conflicts,
            "telemarketing_summary": getattr(appt, "telemarketing_summary", None),
            "notes": appt.notes,
        })

    return queue


class DispatchAction(BaseModel):
    appointment_id: int
    rep_id: int
    backup_rep_id: Optional[int] = None


@router.post("/assign")
def dispatch_assign_rep(
    data: DispatchAction,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Assign a rep to a confirmed appointment and mark as dispatched."""
    appt = db.query(Appointment).filter(Appointment.id == data.appointment_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    rep = db.query(User).filter(User.id == data.rep_id).first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    appt.assigned_rep_id = data.rep_id
    appt.assigned_at = datetime.utcnow()
    appt.dispatch_status = "assigned"
    if data.backup_rep_id:
        appt.backup_rep_id = data.backup_rep_id

    # Update lead status
    lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
    if lead:
        lead.assigned_rep_id = data.rep_id
        lead.deal_status = "assigned"

    log_action(db=db, lead_id=appt.lead_id, action_type="assigned",
               rep_id=current_user.id,
               note=f"Dispatched to {rep.full_name}")

    create_audit_log(
        db=db, user_id=current_user.id, action="dispatch",
        entity_type="appointment", entity_id=appt.id,
        new_value=str(data.rep_id),
        details=f"Dispatched appointment to {rep.full_name}",
    )

    db.commit()
    return {"ok": True, "message": f"Dispatched to {rep.full_name}"}


@router.post("/acknowledge/{appointment_id}")
def acknowledge_dispatch(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rep acknowledges dispatch assignment."""
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appt.assigned_rep_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your appointment")

    appt.rep_acknowledged_at = datetime.utcnow()
    appt.dispatch_status = "acknowledged"

    log_action(db=db, lead_id=appt.lead_id, action_type="status_change",
               rep_id=current_user.id, note="Rep acknowledged dispatch")

    db.commit()
    return {"ok": True, "message": "Dispatch acknowledged"}
