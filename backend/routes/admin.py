"""Admin dashboard and management routes."""
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import (
    Lead, User, Appointment, Deal, Commission, AuditLog,
    AccountabilityFlag, InstallerProfile,
)
from app.auth import get_current_user, require_role, hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/dashboard")
def get_admin_dashboard(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get admin dashboard with key metrics.

    Returns:
        - leads_per_day: Average new leads per day (last 30 days)
        - close_rate: Overall close rate (closed_won / total)
        - revenue_per_rep: Average revenue per rep
        - held_rate: Percentage of leads requiring follow-up

    Args:
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Dashboard metrics
    """
    # Leads per day (last 30 days)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    leads_last_30 = (
        db.query(func.count(Lead.id))
        .filter(Lead.created_at >= thirty_days_ago)
        .scalar()
    )
    leads_per_day = leads_last_30 / 30 if leads_last_30 else 0

    # Close rate (closed_won / total)
    total_leads = db.query(func.count(Lead.id)).scalar() or 1
    closed_won_leads = (
        db.query(func.count(Lead.id))
        .filter(Lead.deal_status == "closed_won")
        .scalar() or 0
    )
    close_rate = (closed_won_leads / total_leads * 100) if total_leads > 0 else 0

    # Revenue per rep (total deal value / number of reps)
    reps = db.query(User).filter(User.role == "rep").all()
    total_revenue = (
        db.query(func.sum(Deal.deal_value)).scalar() or 0
    )
    revenue_per_rep = (total_revenue / len(reps)) if reps else 0

    # Held rate (leads with follow_up_required=True)
    held_leads = (
        db.query(func.count(Lead.id))
        .filter(Lead.follow_up_required == True)
        .scalar() or 0
    )
    held_rate = (held_leads / total_leads * 100) if total_leads > 0 else 0

    return {
        "leads_per_day": round(leads_per_day, 2),
        "close_rate": round(close_rate, 2),
        "revenue_per_rep": round(revenue_per_rep, 2),
        "held_rate": round(held_rate, 2),
        "total_leads": total_leads,
        "closed_won_leads": closed_won_leads,
        "total_revenue": round(total_revenue, 2),
        "total_reps": len(reps),
    }


@router.get("/reps")
def get_rep_performance(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get rep performance table.

    Args:
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        List of rep performance metrics
    """
    reps = (
        db.query(User)
        .filter(User.role == "rep")
        .offset(skip)
        .limit(limit)
        .all()
    )

    rep_data = []
    for rep in reps:
        # Get leads assigned to this rep
        leads = db.query(Lead).filter(Lead.assigned_rep_id == rep.id).all()

        # Get closed won leads (deals)
        closed_won = [l for l in leads if l.deal_status == "closed_won"]

        # Get total deal value
        deals = db.query(Deal).filter(
            Deal.lead_id.in_([l.id for l in closed_won])
        ).all()
        total_value = sum(d.deal_value for d in deals) if deals else 0

        # Get appointments
        appointments = (
            db.query(Appointment).filter(Appointment.assigned_rep_id == rep.id).all()
        )

        # Calculate metrics
        close_rate = (
            (len(closed_won) / len(leads) * 100) if leads else 0
        )

        rep_data.append(
            {
                "id": rep.id,
                "name": rep.full_name,
                "email": rep.email,
                "territory": rep.territory,
                "total_leads": len(leads),
                "closed_won": len(closed_won),
                "close_rate": round(close_rate, 2),
                "total_value": round(total_value, 2),
                "avg_deal_size": round((total_value / len(closed_won)), 2) if closed_won else 0,
                "total_appointments": len(appointments),
                "total_deals": rep.total_deals,
                "close_rate_stored": rep.close_rate,
            }
        )

    return {"reps": rep_data}


@router.get("/installers")
def get_installer_rankings(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get installer rankings from InstallerProfile.

    Args:
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        List of installers ranked by performance
    """
    installer_profiles = db.query(InstallerProfile).all()

    installer_data = []
    for profile in installer_profiles:
        installer = db.query(User).filter(User.id == profile.user_id).first()

        completion_rate = (
            (profile.jobs_completed / profile.jobs_assigned * 100)
            if profile.jobs_assigned and profile.jobs_assigned > 0
            else 0.0
        )

        installer_data.append(
            {
                "id": profile.user_id,
                "name": installer.full_name if installer else "Unknown",
                "email": installer.email if installer else "Unknown",
                "company_name": profile.company_name,
                "jobs_assigned": profile.jobs_assigned or 0,
                "jobs_completed": profile.jobs_completed or 0,
                "completion_rate": round(completion_rate, 2),
                "avg_install_days": profile.avg_install_days or 0.0,
                "performance_score": profile.performance_score or 0.0,
                "tier": profile.tier or "bronze",
                "is_active": profile.is_active,
            }
        )

    # Sort by performance_score descending
    installer_data.sort(key=lambda x: x["performance_score"], reverse=True)

    return {"installers": installer_data}


@router.get("/audit-log")
def get_audit_log(
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get full audit trail (paginated).

    Args:
        entity_type: Filter by entity type
        entity_id: Filter by entity ID
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Paginated audit log entries
    """
    query = db.query(AuditLog)

    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)

    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id)

    total = query.count()

    entries = (
        query.order_by(AuditLog.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    audit_data = []
    for entry in entries:
        user = db.query(User).filter(User.id == entry.user_id).first()
        audit_data.append(
            {
                "id": entry.id,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "user_id": entry.user_id,
                "user_name": user.full_name if user else "Unknown",
                "action": entry.action,
                "previous_value": entry.previous_value,
                "new_value": entry.new_value,
                "details": entry.details,
                "created_at": entry.created_at,
            }
        )

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "entries": audit_data,
    }


@router.get("/fraud-flags")
def get_fraud_flags(
    resolved: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get fraud/behavior flags.

    Args:
        resolved: Filter by resolved status
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        List of accountability flags
    """
    query = db.query(AccountabilityFlag)

    if resolved is not None:
        query = query.filter(AccountabilityFlag.resolved == resolved)

    total = query.count()

    flags = (
        query.order_by(AccountabilityFlag.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    flag_data = []
    for flag in flags:
        user = db.query(User).filter(User.id == flag.user_id).first()
        flag_data.append(
            {
                "id": flag.id,
                "user_id": flag.user_id,
                "user_name": user.full_name if user else "Unknown",
                "flag_type": flag.flag_type,
                "details": flag.details,
                "resolved": flag.resolved,
                "created_at": flag.created_at,
            }
        )

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "flags": flag_data,
    }


class CreateRepRequest(BaseModel):
    email: str
    password: str
    full_name: str
    phone: Optional[str] = None
    territory: Optional[str] = None


@router.post("/reps")
def create_rep(
    rep_data: CreateRepRequest,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Create a new rep user. Admin only."""
    existing = db.query(User).filter(User.email == rep_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already in use")

    new_rep = User(
        email=rep_data.email,
        hashed_password=hash_password(rep_data.password),
        full_name=rep_data.full_name,
        role="rep",
        phone=rep_data.phone,
        territory=rep_data.territory,
        is_active=True,
    )
    db.add(new_rep)
    db.commit()
    db.refresh(new_rep)

    return {
        "id": new_rep.id,
        "email": new_rep.email,
        "full_name": new_rep.full_name,
        "role": new_rep.role,
        "phone": new_rep.phone,
        "territory": new_rep.territory,
        "is_active": new_rep.is_active,
    }


@router.put("/reps/{rep_id}")
def update_rep(
    rep_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
    close_rate: Optional[float] = Query(None),
    is_active: Optional[bool] = Query(None),
    territory: Optional[str] = Query(None),
):
    """Update rep close_rate, active status, or territory."""
    rep = db.query(User).filter(User.id == rep_id, User.role == "rep").first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")
    if close_rate is not None:
        rep.close_rate = close_rate
    if is_active is not None:
        rep.is_active = is_active
    if territory is not None:
        rep.territory = territory
    db.commit()
    db.refresh(rep)
    return {"id": rep.id, "name": rep.full_name, "close_rate": rep.close_rate, "is_active": rep.is_active, "territory": rep.territory}


@router.get("/appointments")
def get_all_appointments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get all appointments with lead and rep info for admin view."""
    from app.models import Appointment as Appt
    appointments = (
        db.query(Appt)
        .order_by(Appt.appointment_date.desc(), Appt.appointment_time.desc())
        .offset(skip).limit(limit).all()
    )
    result = []
    for appt in appointments:
        lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
        rep = db.query(User).filter(User.id == appt.assigned_rep_id).first()
        result.append({
            "id": appt.id,
            "lead_id": appt.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
            "lead_phone": lead.phone if lead else "",
            "lead_address": lead.property_address if lead else "",
            "rep_id": appt.assigned_rep_id,
            "rep_name": rep.full_name if rep else "Unassigned",
            "date": str(appt.appointment_date),
            "time": str(appt.appointment_time),
            "status": appt.appointment_status,
            "confirmation_status": appt.confirmation_status,
            "outcome": appt.outcome,
            "notes": appt.notes,
            "created_at": str(appt.created_at),
        })
    total = db.query(func.count(Appt.id)).scalar()
    return {"total": total, "appointments": result}


@router.put("/appointments/{appt_id}/status")
def update_appointment_status(
    appt_id: int,
    new_status: str = Query(...),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin: update appointment status. Logs action to timeline."""
    from app.models import Appointment as Appt
    from services.actions import log_action
    appt = db.query(Appt).filter(Appt.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    old_status = appt.appointment_status
    appt.appointment_status = new_status
    db.flush()

    log_action(
        db=db, lead_id=appt.lead_id, action_type="status_change",
        rep_id=appt.assigned_rep_id, appointment_id=appt.id,
        note=f"Admin changed status: {old_status} → {new_status}",
    )

    db.commit()
    return {"id": appt.id, "status": appt.appointment_status}


@router.get("/notifications")
def get_system_notifications(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get system notifications and alerts.

    Args:
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        System notifications and alerts
    """
    # Count recent fraud flags (last 24 hours)
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    recent_flags = (
        db.query(func.count(AccountabilityFlag.id))
        .filter(AccountabilityFlag.created_at >= twenty_four_hours_ago)
        .scalar() or 0
    )

    # Count leads needing follow-up
    followup_leads = (
        db.query(func.count(Lead.id))
        .filter(Lead.follow_up_required == True)
        .scalar() or 0
    )

    # Count overdue appointments
    today = datetime.utcnow().date()
    overdue_appointments = (
        db.query(func.count(Appointment.id))
        .filter(
            Appointment.appointment_date < today,
            Appointment.appointment_status.in_(["scheduled", "confirming", "en_route"])
        )
        .scalar() or 0
    )

    notifications = []

    if recent_flags > 5:
        notifications.append(
            {
                "id": 1,
                "type": "warning",
                "title": "High fraud flag rate",
                "message": f"{recent_flags} flags created in the last 24 hours",
                "created_at": datetime.utcnow(),
            }
        )

    if followup_leads > 50:
        notifications.append(
            {
                "id": 2,
                "type": "info",
                "title": "High follow-up queue",
                "message": f"{followup_leads} leads require follow-up",
                "created_at": datetime.utcnow(),
            }
        )

    if overdue_appointments > 0:
        notifications.append(
            {
                "id": 3,
                "type": "warning",
                "title": "Overdue appointments",
                "message": f"{overdue_appointments} appointments are overdue",
                "created_at": datetime.utcnow(),
            }
        )

    return {"notifications": notifications}


# ============================================================================
# HELD / NOT HELD METRICS + PIPELINE VISIBILITY
# ============================================================================

@router.get("/metrics/held")
def get_held_metrics(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Admin: Get held vs not-held appointment metrics and rep performance.

    Returns:
    - Overall held/not-held counts and rates
    - Per-rep held rate and close rate (closed / held)
    - Pipeline: proposal_sent leads, follow-ups due, rehash queue
    """
    from app.models import FollowUp, RehashEntry

    # All completed/no_show appointments
    completed_appts = (
        db.query(Appointment)
        .filter(Appointment.outcome.isnot(None))
        .all()
    )

    held_outcomes = {"closed", "proposal_presented", "proposal_sent", "follow_up_required", "declined"}
    not_held_outcomes = {"no_show", "not_home", "canceled", "reschedule_requested"}
    # Also count legacy outcomes
    legacy_held = {"not_closed"}  # Old "not_closed" was a held meeting

    held_appts = [a for a in completed_appts if a.outcome in held_outcomes or a.outcome in legacy_held]
    not_held_appts = [a for a in completed_appts if a.outcome in not_held_outcomes]

    total_outcomes = len(held_appts) + len(not_held_appts)
    held_rate = (len(held_appts) / total_outcomes * 100) if total_outcomes > 0 else 0

    # Close rate = closed / held
    closed_appts = [a for a in completed_appts if a.outcome == "closed"]
    close_rate = (len(closed_appts) / len(held_appts) * 100) if held_appts else 0

    # Outcome breakdown
    outcome_counts = {}
    for a in completed_appts:
        outcome_counts[a.outcome] = outcome_counts.get(a.outcome, 0) + 1

    # Per-rep metrics
    reps = db.query(User).filter(User.role == "rep").all()
    rep_metrics = []
    for rep in reps:
        rep_appts = [a for a in completed_appts if a.assigned_rep_id == rep.id]
        rep_held = [a for a in rep_appts if a.outcome in held_outcomes or a.outcome in legacy_held]
        rep_not_held = [a for a in rep_appts if a.outcome in not_held_outcomes]
        rep_closed = [a for a in rep_appts if a.outcome == "closed"]
        rep_total = len(rep_held) + len(rep_not_held)

        rep_metrics.append({
            "id": rep.id,
            "name": rep.full_name,
            "total_appointments": len(rep_appts),
            "held_count": len(rep_held),
            "not_held_count": len(rep_not_held),
            "held_rate": round((len(rep_held) / rep_total * 100) if rep_total > 0 else 0, 1),
            "closed_count": len(rep_closed),
            "close_rate": round((len(rep_closed) / len(rep_held) * 100) if rep_held else 0, 1),
        })

    # Pipeline: proposal_sent leads
    proposal_leads = (
        db.query(Lead)
        .filter(Lead.deal_status == "proposal_sent")
        .all()
    )

    # Follow-ups due
    follow_ups_due = (
        db.query(FollowUp)
        .filter(FollowUp.status == "pending")
        .order_by(FollowUp.scheduled_date.asc())
        .limit(50)
        .all()
    )

    # Rehash queue
    rehash_pending = (
        db.query(RehashEntry)
        .filter(RehashEntry.status == "pending")
        .order_by(RehashEntry.created_at.desc())
        .limit(50)
        .all()
    )

    return {
        "overall": {
            "total_outcomes": total_outcomes,
            "held_count": len(held_appts),
            "not_held_count": len(not_held_appts),
            "held_rate": round(held_rate, 1),
            "closed_count": len(closed_appts),
            "close_rate_from_held": round(close_rate, 1),
            "outcome_breakdown": outcome_counts,
        },
        "rep_metrics": sorted(rep_metrics, key=lambda r: r["close_rate"], reverse=True),
        "pipeline": {
            "proposal_sent_count": len(proposal_leads),
            "proposal_sent_leads": [
                {
                    "id": l.id,
                    "name": f"{l.first_name} {l.last_name}",
                    "phone": l.phone,
                    "follow_up_date": str(l.next_follow_up_date) if l.next_follow_up_date else None,
                    "follow_up_note": l.follow_up_note,
                }
                for l in proposal_leads
            ],
            "follow_ups_due_count": len(follow_ups_due),
            "follow_ups_due": [
                {
                    "id": f.id,
                    "lead_id": f.lead_id,
                    "rep_id": f.assigned_rep_id,
                    "reason": f.reason,
                    "scheduled_date": str(f.scheduled_date) if f.scheduled_date else None,
                    "notes": f.notes,
                    "status": f.status,
                }
                for f in follow_ups_due
            ],
            "rehash_count": len(rehash_pending),
            "rehash_queue": [
                {
                    "id": r.id,
                    "lead_id": r.lead_id,
                    "original_rep_id": r.original_rep_id,
                    "reason": r.reason,
                    "status": r.status,
                    "created_at": str(r.created_at),
                }
                for r in rehash_pending
            ],
        },
    }
