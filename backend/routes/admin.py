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
    AccountabilityFlag, InstallerProfile, Task, Project, Violation,
)
from app.auth import get_current_user, require_role, hash_password
from services.outcome_engine import mark_overdue_tasks
from services.enforcement_engine import get_enforcement_overview, run_enforcement_check

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

    # ACCURATE close rate: closed / held appointments (not all leads)
    total_leads = db.query(func.count(Lead.id)).filter(Lead.is_archived == False).scalar() or 1
    closed_won_leads = (
        db.query(func.count(Lead.id))
        .filter(Lead.deal_status == "closed_won")
        .scalar() or 0
    )

    # Close rate based on appointments with outcomes
    held_outcomes = {"closed", "proposal_presented", "proposal_sent", "follow_up_required", "declined"}
    completed_appts = db.query(Appointment).filter(Appointment.outcome.isnot(None)).all()
    held_appts = [a for a in completed_appts if a.outcome in held_outcomes]
    closed_appts = [a for a in completed_appts if a.outcome == "closed"]
    not_held_appts = [a for a in completed_appts if a.outcome not in held_outcomes]

    total_w_outcome = len(completed_appts)
    close_rate = (len(closed_appts) / len(held_appts) * 100) if held_appts else 0
    held_rate = (len(held_appts) / total_w_outcome * 100) if total_w_outcome > 0 else 0

    # Revenue per rep (total deal value / number of active reps)
    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()
    total_revenue = (
        db.query(func.sum(Deal.deal_value)).scalar() or 0
    )
    revenue_per_rep = (total_revenue / len(reps)) if reps else 0

    # Enforcement stats
    run_enforcement_check(db)
    overdue_tasks = db.query(func.count(Task.id)).filter(Task.status == "overdue").scalar() or 0
    active_violations = db.query(func.count(Violation.id)).filter(Violation.acknowledged == False).scalar() or 0

    return {
        "leads_per_day": round(leads_per_day, 2),
        "close_rate": round(close_rate, 2),
        "revenue_per_rep": round(revenue_per_rep, 2),
        "held_rate": round(held_rate, 2),
        "total_leads": total_leads,
        "closed_won_leads": closed_won_leads,
        "total_revenue": round(total_revenue, 2),
        "total_reps": len(reps),
        "held_appointments": len(held_appts),
        "not_held_appointments": len(not_held_appts),
        "total_appointments_completed": total_w_outcome,
        "overdue_tasks": overdue_tasks,
        "active_violations": active_violations,
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


# ── Task Management (Admin View) ─────────────────────────────
@router.get("/tasks")
def admin_tasks(
    status_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin view of all tasks across all reps. Auto-marks overdue."""
    mark_overdue_tasks(db)

    query = db.query(Task)
    if status_filter:
        query = query.filter(Task.status == status_filter)

    tasks = query.order_by(Task.due_date.asc()).all()

    result = []
    for t in tasks:
        lead = db.query(Lead).filter(Lead.id == t.lead_id).first()
        assignee = db.query(User).filter(User.id == t.assigned_to).first()
        result.append({
            "id": t.id,
            "lead_id": t.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "assigned_to": t.assigned_to,
            "assignee_name": assignee.full_name if assignee else None,
            "task_type": t.task_type,
            "title": t.title,
            "status": t.status,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "source_outcome": t.source_outcome,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    return result


# ── Project Pipeline (Admin View) ────────────────────────────
@router.get("/projects")
def admin_projects(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin view of all post-sale projects. Shows install pipeline."""
    projects = db.query(Project).order_by(Project.created_at.desc()).all()

    result = []
    for p in projects:
        lead = db.query(Lead).filter(Lead.id == p.lead_id).first()
        closer = db.query(User).filter(User.id == p.closer_id).first()
        result.append({
            "id": p.id,
            "lead_id": p.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "closer_name": closer.full_name if closer else None,
            "system_size_kw": p.system_size_kw,
            "deal_value": p.deal_value,
            "install_status": p.install_status,
            "funding_status": p.funding_status,
            "payment_status": p.payment_status,
            "sold_date": p.sold_date.isoformat() if p.sold_date else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return result


# ============================================================================
# ENFORCEMENT OVERVIEW (ADMIN)
# ============================================================================

@router.get("/enforcement")
def admin_enforcement_overview(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Admin enforcement dashboard.
    Shows per-rep: overdue tasks, violations, held rate, close rate.
    Global: total overdue, total violations, reps in red.
    """
    return get_enforcement_overview(db)


@router.get("/violations")
def admin_violations(
    rep_id: Optional[int] = Query(None),
    violation_type: Optional[str] = Query(None),
    acknowledged: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin: list all violations with filters."""
    run_enforcement_check(db)

    query = db.query(Violation)
    if rep_id:
        query = query.filter(Violation.rep_id == rep_id)
    if violation_type:
        query = query.filter(Violation.violation_type == violation_type)
    if acknowledged is not None:
        query = query.filter(Violation.acknowledged == acknowledged)

    total = query.count()
    violations = (
        query.order_by(Violation.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    result = []
    for v in violations:
        rep = db.query(User).filter(User.id == v.rep_id).first()
        lead = db.query(Lead).filter(Lead.id == v.lead_id).first() if v.lead_id else None
        result.append({
            "id": v.id,
            "rep_id": v.rep_id,
            "rep_name": rep.full_name if rep else None,
            "violation_type": v.violation_type,
            "severity": v.severity,
            "description": v.description,
            "lead_id": v.lead_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
            "task_id": v.task_id,
            "appointment_id": v.appointment_id,
            "acknowledged": v.acknowledged,
            "acknowledged_by": v.acknowledged_by,
            "admin_notes": v.admin_notes,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })

    return {"total": total, "violations": result}


# ============================================================================
# ADMIN CONTROLS — PHASE 2
# ============================================================================

class SoftDeleteRequest(BaseModel):
    reason: Optional[str] = None


@router.delete("/leads/{lead_id}")
def admin_soft_delete_lead(
    lead_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Soft-delete (archive) a lead. Does NOT permanently remove data."""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.is_archived:
        raise HTTPException(status_code=400, detail="Lead already archived")

    lead.is_archived = True
    lead.archived_at = datetime.utcnow()
    lead.archived_by = current_user.id

    from services.audit import create_audit_log
    create_audit_log(
        db=db, user_id=current_user.id, action="archive",
        entity_type="lead", entity_id=lead.id,
        details=f"Lead archived: {lead.first_name} {lead.last_name}",
    )

    db.commit()
    return {"ok": True, "message": f"Lead #{lead_id} archived"}


@router.post("/leads/{lead_id}/restore")
def admin_restore_lead(
    lead_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Restore a soft-deleted lead."""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.is_archived:
        raise HTTPException(status_code=400, detail="Lead is not archived")

    lead.is_archived = False
    lead.archived_at = None
    lead.archived_by = None
    db.commit()
    return {"ok": True, "message": f"Lead #{lead_id} restored"}


@router.delete("/appointments/{appt_id}")
def admin_cancel_appointment(
    appt_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin cancels an appointment."""
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appt.appointment_status in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel: status is {appt.appointment_status}")

    old_status = appt.appointment_status
    appt.appointment_status = "cancelled"

    from services.audit import create_audit_log
    create_audit_log(
        db=db, user_id=current_user.id, action="admin_cancel",
        entity_type="appointment", entity_id=appt.id,
        details=f"Admin cancelled appointment (was: {old_status})",
    )

    db.commit()
    return {"ok": True, "message": f"Appointment #{appt_id} cancelled"}


class ForceOutcomeRequest(BaseModel):
    outcome: str
    notes: str


@router.post("/appointments/{appt_id}/force-outcome")
def admin_force_outcome(
    appt_id: int,
    data: ForceOutcomeRequest,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin force-override an appointment outcome, bypassing normal flow."""
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    valid_outcomes = {
        "closed", "proposal_presented", "proposal_sent",
        "follow_up_required", "declined",
        "no_show", "not_home", "canceled", "reschedule_requested",
    }
    if data.outcome not in valid_outcomes:
        raise HTTPException(status_code=400, detail=f"Invalid outcome: {data.outcome}")

    old_outcome = appt.outcome
    old_status = appt.appointment_status
    appt.outcome = data.outcome
    appt.appointment_status = "completed"
    appt.notes = f"[ADMIN OVERRIDE] {data.notes}"

    # Update lead — mirror outcome_engine logic
    lead = db.query(Lead).filter(Lead.id == appt.lead_id).first()
    if lead:
        held_outcomes = {"closed", "proposal_presented", "proposal_sent", "follow_up_required", "declined"}
        lead.is_held = data.outcome in held_outcomes
        lead.last_outcome = data.outcome
        status_map = {
            "closed": "closed_won", "proposal_presented": "proposal_sent",
            "proposal_sent": "proposal_sent", "follow_up_required": "follow_up",
            "declined": "closed_lost", "no_show": "no_show",
            "not_home": "reschedule", "canceled": "canceled",
            "reschedule_requested": "reschedule",
        }
        if data.outcome in status_map:
            lead.deal_status = status_map[data.outcome]
        # Handle follow-up flag
        fu_outcomes = {"proposal_presented", "proposal_sent", "follow_up_required", "not_home", "reschedule_requested"}
        lead.follow_up_required = data.outcome in fu_outcomes

    from services.audit import create_audit_log
    create_audit_log(
        db=db, user_id=current_user.id, action="force_outcome",
        entity_type="appointment", entity_id=appt.id,
        details=f"Admin forced outcome: {old_outcome or old_status} -> {data.outcome}. Notes: {data.notes}",
    )

    db.commit()
    return {"ok": True, "message": f"Outcome forced to {data.outcome}"}


class ReassignRequest(BaseModel):
    new_rep_id: int
    entity_type: str  # "lead" or "appointment" or "task"
    entity_id: int


@router.post("/reassign")
def admin_reassign(
    data: ReassignRequest,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin instant-reassign any entity to a different rep."""
    new_rep = db.query(User).filter(User.id == data.new_rep_id).first()
    if not new_rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    from services.audit import create_audit_log

    if data.entity_type == "lead":
        lead = db.query(Lead).filter(Lead.id == data.entity_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        old_rep = lead.assigned_rep_id
        lead.assigned_rep_id = data.new_rep_id
        create_audit_log(db=db, user_id=current_user.id, action="reassign",
                         entity_type="lead", entity_id=lead.id,
                         details=f"Reassigned from rep {old_rep} to {data.new_rep_id}")

    elif data.entity_type == "appointment":
        appt = db.query(Appointment).filter(Appointment.id == data.entity_id).first()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        old_rep = appt.assigned_rep_id
        appt.assigned_rep_id = data.new_rep_id
        create_audit_log(db=db, user_id=current_user.id, action="reassign",
                         entity_type="appointment", entity_id=appt.id,
                         details=f"Reassigned from rep {old_rep} to {data.new_rep_id}")

    elif data.entity_type == "task":
        task = db.query(Task).filter(Task.id == data.entity_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        old_rep = task.assigned_to
        task.assigned_to = data.new_rep_id
        create_audit_log(db=db, user_id=current_user.id, action="reassign",
                         entity_type="task", entity_id=task.id,
                         details=f"Reassigned from rep {old_rep} to {data.new_rep_id}")
    else:
        raise HTTPException(status_code=400, detail="Invalid entity_type")

    db.commit()
    return {"ok": True, "message": f"{data.entity_type} #{data.entity_id} reassigned to {new_rep.full_name}"}


@router.get("/rep-performance")
def admin_rep_performance(
    sort_by: str = Query("score", description="Sort by: score, close_rate, held_rate, violations, overdue"),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Full rep performance breakdown with sorting."""
    from services.enforcement import calculate_rep_performance

    reps = db.query(User).filter(User.role == "rep", User.is_active == True).all()
    results = []
    for rep in reps:
        perf = calculate_rep_performance(db, rep.id)
        perf["name"] = rep.full_name
        perf["email"] = rep.email
        perf["is_active"] = rep.is_active

        # Composite score: higher is better
        perf["score"] = round(
            perf["close_rate"] * 3 + perf["held_rate"] * 2
            - perf["overdue_tasks"] * 15 - perf["active_violations"] * 20,
            1
        )
        results.append(perf)

    # Sort
    sort_map = {
        "score": lambda r: r["score"],
        "close_rate": lambda r: r["close_rate"],
        "held_rate": lambda r: r["held_rate"],
        "violations": lambda r: r["active_violations"],
        "overdue": lambda r: r["overdue_tasks"],
    }
    sort_fn = sort_map.get(sort_by, sort_map["score"])
    reverse = sort_by not in ("violations", "overdue")
    results.sort(key=sort_fn, reverse=reverse)

    return {"reps": results}


@router.get("/recommend-rep/{lead_id}")
def admin_recommend_rep(
    lead_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get recommended reps for assignment based on workload + performance."""
    from services.enforcement import recommend_rep
    return {"recommendations": recommend_rep(db, lead_id)}


@router.get("/lead-lock/{lead_id}")
def admin_check_lead_lock(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check if a lead is locked due to pending tasks. Reps can only check their assigned leads."""
    if current_user.role == "rep":
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead or lead.assigned_rep_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not your lead")
    from services.enforcement_engine import check_lead_locked
    return check_lead_locked(db, lead_id)


# ========================================================================
# PHASE 3 — ESCALATION, PRIORITY, REVENUE, LEADERBOARD, NOTIFICATIONS
# ========================================================================

@router.get("/escalation")
def admin_escalation(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Run escalation engine and return results."""
    from services.phase3_engines import run_escalation_engine
    return run_escalation_engine(db)


@router.get("/priority-leads")
def admin_priority_leads(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get top priority leads to close."""
    from services.phase3_engines import get_top_leads_to_close
    return {"leads": get_top_leads_to_close(db, limit)}


@router.get("/rep-priority-leads/{rep_id}")
def admin_rep_priority_leads(
    rep_id: int,
    limit: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get top priority leads for a specific rep. Reps can only view their own."""
    if current_user.role == "rep" and current_user.id != rep_id:
        raise HTTPException(status_code=403, detail="Cannot view other reps' priority leads")
    from services.phase3_engines import get_rep_top_leads
    return {"leads": get_rep_top_leads(db, rep_id, limit)}


@router.get("/revenue")
def admin_revenue(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get revenue visibility dashboard."""
    from services.phase3_engines import get_revenue_dashboard
    return get_revenue_dashboard(db)


@router.get("/reassignment-suggestions")
def admin_reassignment_suggestions(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get leads that should be reassigned."""
    from services.phase3_engines import get_reassignment_suggestions
    return {"suggestions": get_reassignment_suggestions(db)}


@router.get("/leaderboard")
def admin_leaderboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get rep leaderboard with rankings."""
    from services.phase3_engines import get_rep_leaderboard
    return get_rep_leaderboard(db)


@router.get("/smart-recommend/{lead_id}")
def admin_smart_recommend(
    lead_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Smart dispatch Level 2 — enhanced rep recommendation."""
    from services.phase3_engines import smart_recommend_rep
    return smart_recommend_rep(db, lead_id)


@router.get("/notifications")
def get_notifications(
    unread_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get notifications for current user."""
    from services.phase3_engines import get_user_notifications
    return {"notifications": get_user_notifications(db, current_user.id, unread_only)}


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a notification as read."""
    from services.phase3_engines import mark_notification_read
    mark_notification_read(db, notification_id, current_user.id)
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark all notifications as read."""
    from services.phase3_engines import mark_all_notifications_read
    mark_all_notifications_read(db, current_user.id)
    return {"ok": True}


@router.get("/accountability")
def get_accountability_dashboard(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Real-time accountability dashboard: who's failing, what's overdue, what needs action."""
    now = datetime.utcnow()
    today = now.date()

    # Run enforcement check first
    run_enforcement_check(db)

    # 1. Reps late today
    late_appts = (
        db.query(Appointment)
        .filter(Appointment.appointment_date == today)
        .all()
    )
    reps_late = []
    for a in late_appts:
        if getattr(a, "rep_late", False):
            rep = db.query(User).filter(User.id == a.assigned_rep_id).first()
            lead = db.query(Lead).filter(Lead.id == a.lead_id).first()
            if rep and lead:
                mins_late = 0
                if a.rep_checked_in_at and a.appointment_time:
                    sched = datetime.combine(a.appointment_date, a.appointment_time)
                    mins_late = max(0, int((a.rep_checked_in_at - sched).total_seconds() / 60))
                reps_late.append({
                    "rep_name": rep.full_name, "rep_id": rep.id,
                    "lead_name": f"{lead.first_name} {lead.last_name}",
                    "mins_late": mins_late, "appointment_id": a.id,
                })

    # 2. Unacknowledged appointments
    unacked = (
        db.query(Appointment)
        .filter(
            Appointment.dispatch_status == "assigned",
            Appointment.rep_acknowledged_at == None,
        )
        .all()
    )
    unacked_list = []
    for a in unacked:
        rep = db.query(User).filter(User.id == a.assigned_rep_id).first()
        lead = db.query(Lead).filter(Lead.id == a.lead_id).first()
        mins_waiting = int((now - (a.assigned_at or a.created_at)).total_seconds() / 60) if a.assigned_at else 0
        unacked_list.append({
            "appointment_id": a.id,
            "rep_name": rep.full_name if rep else "Unknown",
            "rep_id": a.assigned_rep_id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
            "mins_waiting": mins_waiting,
            "appointment_date": str(a.appointment_date),
            "appointment_time": str(a.appointment_time) if a.appointment_time else None,
        })

    # 3. Overdue confirmations
    overdue_confirms = (
        db.query(Appointment)
        .filter(
            Appointment.confirmation_status.in_(["pending", "confirming"]),
        )
        .all()
    )
    overdue_confirm_list = []
    for a in overdue_confirms:
        lead = db.query(Lead).filter(Lead.id == a.lead_id).first()
        hours_waiting = int((now - a.created_at).total_seconds() / 3600) if a.created_at else 0
        overdue_confirm_list.append({
            "appointment_id": a.id,
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
            "phone": lead.phone if lead else None,
            "hours_waiting": hours_waiting,
            "attempts": a.confirmation_attempts_count or 0,
            "appointment_date": str(a.appointment_date),
            "is_overdue": hours_waiting >= 2,
        })

    # 4. Missed updates (today's appts without outcome past scheduled time)
    missed_updates = []
    for a in late_appts:
        if a.appointment_time and not a.outcome:
            sched = datetime.combine(a.appointment_date, a.appointment_time)
            if now > sched + timedelta(hours=1) and a.appointment_status not in ("completed", "cancelled", "no_show"):
                rep = db.query(User).filter(User.id == a.assigned_rep_id).first()
                lead = db.query(Lead).filter(Lead.id == a.lead_id).first()
                missed_updates.append({
                    "appointment_id": a.id,
                    "rep_name": rep.full_name if rep else "Unknown",
                    "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
                    "status": a.appointment_status,
                    "scheduled_time": str(a.appointment_time),
                    "hours_overdue": int((now - sched).total_seconds() / 3600),
                })

    # 5. Active violations summary
    active_violations = (
        db.query(Violation)
        .filter(Violation.acknowledged == False)
        .order_by(Violation.created_at.desc())
        .limit(50)
        .all()
    )
    violations_list = []
    for v in active_violations:
        rep = db.query(User).filter(User.id == v.rep_id).first()
        violations_list.append({
            "id": v.id, "rep_name": rep.full_name if rep else "Unknown",
            "type": v.violation_type, "severity": v.severity,
            "description": v.description,
            "created_at": str(v.created_at) if v.created_at else None,
        })

    # 6. Overdue tasks
    overdue_tasks = db.query(Task).filter(Task.status == "overdue").count()
    pending_tasks = db.query(Task).filter(Task.status == "pending").count()

    return {
        "reps_late_today": reps_late,
        "unacknowledged_dispatches": unacked_list,
        "overdue_confirmations": [c for c in overdue_confirm_list if c["is_overdue"]],
        "pending_confirmations": [c for c in overdue_confirm_list if not c["is_overdue"]],
        "missed_updates": missed_updates,
        "active_violations": violations_list,
        "overdue_tasks_count": overdue_tasks,
        "pending_tasks_count": pending_tasks,
        "summary": {
            "reps_late": len(reps_late),
            "unacked": len(unacked_list),
            "overdue_confirms": len([c for c in overdue_confirm_list if c["is_overdue"]]),
            "missed_updates": len(missed_updates),
            "violations": len(violations_list),
            "overdue_tasks": overdue_tasks,
        },
    }
