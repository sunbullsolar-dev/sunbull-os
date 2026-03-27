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
from app.auth import get_current_user, require_role

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
