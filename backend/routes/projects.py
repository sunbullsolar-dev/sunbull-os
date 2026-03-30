"""Project management routes for installation and funding tracking."""
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.database import get_db
from app.models import Project, User, Deal, Lead
from app.auth import get_current_user, require_role

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectUpdate(BaseModel):
    """Update project request model."""
    install_status: Optional[str] = None
    funding_status: Optional[str] = None
    payment_status: Optional[str] = None
    survey_date: Optional[str] = None
    permit_date: Optional[str] = None
    install_date: Optional[str] = None
    inspection_date: Optional[str] = None
    pto_date: Optional[str] = None
    funded_date: Optional[str] = None
    paid_date: Optional[str] = None
    notes: Optional[str] = None
    installer_id: Optional[int] = None


class ProjectResponse(BaseModel):
    """Project response model."""
    id: int
    lead_id: int
    appointment_id: Optional[int] = None
    closer_id: int
    system_size_kw: Optional[float] = None
    panel_count: Optional[int] = None
    deal_value: Optional[float] = None
    install_status: str
    funding_status: str
    payment_status: str
    sold_date: Optional[datetime] = None
    survey_date: Optional[datetime] = None
    permit_date: Optional[datetime] = None
    install_date: Optional[datetime] = None
    inspection_date: Optional[datetime] = None
    pto_date: Optional[datetime] = None
    funded_date: Optional[datetime] = None
    paid_date: Optional[datetime] = None
    installer_id: Optional[int] = None
    project_manager_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---- DASHBOARD (must be before /{project_id} to avoid route collision) ----

@router.get("/dashboard/summary")
def get_project_dashboard(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get project dashboard summary: counts and values per stage, delayed projects, aging stats."""
    projects = db.query(Project).all()

    install_stages = {}
    funding_stages = {}
    payment_stages = {}
    now = datetime.utcnow()
    delayed_projects = []

    for proj in projects:
        if proj.install_status not in install_stages:
            install_stages[proj.install_status] = {"count": 0, "total_value": 0.0}
        install_stages[proj.install_status]["count"] += 1

        if proj.funding_status not in funding_stages:
            funding_stages[proj.funding_status] = {"count": 0, "total_value": 0.0}
        funding_stages[proj.funding_status]["count"] += 1

        if proj.payment_status not in payment_stages:
            payment_stages[proj.payment_status] = {"count": 0, "total_value": 0.0}
        payment_stages[proj.payment_status]["count"] += 1

        dv = proj.deal_value or 0
        install_stages[proj.install_status]["total_value"] += dv
        funding_stages[proj.funding_status]["total_value"] += dv
        payment_stages[proj.payment_status]["total_value"] += dv

        # Check for delayed projects (>30 days in current stage)
        stage_entered = proj.install_date or proj.funded_date or proj.sold_date
        if stage_entered and (now - stage_entered) > timedelta(days=30):
            lead = db.query(Lead).filter(Lead.id == proj.lead_id).first() if proj.lead_id else None
            delayed_projects.append({
                "project_id": proj.id,
                "lead_id": proj.lead_id,
                "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
                "install_status": proj.install_status,
                "funding_status": proj.funding_status,
                "days_in_stage": (now - stage_entered).days,
                "deal_value": dv,
            })

    aging_stats = {
        "total_projects": len(projects),
        "by_age": {
            "0_30_days": sum(1 for p in projects if (now - p.created_at).days <= 30),
            "31_90_days": sum(1 for p in projects if 30 < (now - p.created_at).days <= 90),
            "91_180_days": sum(1 for p in projects if 90 < (now - p.created_at).days <= 180),
            "180_plus_days": sum(1 for p in projects if (now - p.created_at).days > 180),
        },
    }

    return {
        "install_stages": [{"stage": k, "count": v["count"], "total_value": v["total_value"]} for k, v in install_stages.items()],
        "funding_stages": [{"stage": k, "count": v["count"], "total_value": v["total_value"]} for k, v in funding_stages.items()],
        "payment_stages": [{"stage": k, "count": v["count"], "total_value": v["total_value"]} for k, v in payment_stages.items()],
        "delayed_projects": delayed_projects,
        "aging_stats": aging_stats,
    }


# ---- LIST / CRUD ----

@router.get("")
def list_projects(
    install_status: Optional[str] = Query(None),
    funding_status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    installer_id: Optional[int] = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """List all projects with optional filtering. Returns enriched data with lead/closer names."""
    query = db.query(Project)
    if install_status:
        query = query.filter(Project.install_status == install_status)
    if funding_status:
        query = query.filter(Project.funding_status == funding_status)
    if payment_status:
        query = query.filter(Project.payment_status == payment_status)
    if installer_id:
        query = query.filter(Project.installer_id == installer_id)
    projects = query.order_by(Project.created_at.desc()).all()

    # Enrich with lead and closer names
    result = []
    for p in projects:
        data = {c.name: getattr(p, c.name) for c in p.__table__.columns}
        lead = db.query(Lead).filter(Lead.id == p.lead_id).first() if p.lead_id else None
        closer = db.query(User).filter(User.id == p.closer_id).first() if p.closer_id else None
        data["lead_name"] = f"{lead.first_name} {lead.last_name}" if lead else "Unknown"
        data["lead_address"] = lead.property_address if lead else ""
        data["lead_city"] = f"{lead.city}, {lead.state}" if lead else ""
        data["closer_name"] = closer.full_name if closer else "Unknown"
        result.append(data)
    return result


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get a single project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    update_data: ProjectUpdate,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Update a project's status and details (admin only)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if update_data.install_status is not None:
        project.install_status = update_data.install_status
    if update_data.funding_status is not None:
        project.funding_status = update_data.funding_status
    if update_data.payment_status is not None:
        project.payment_status = update_data.payment_status
    date_fields = ['survey_date', 'permit_date', 'install_date', 'inspection_date',
                    'pto_date', 'funded_date', 'paid_date']
    for field in date_fields:
        val = getattr(update_data, field, None)
        if val is not None:
            setattr(project, field, datetime.fromisoformat(val))
    if update_data.notes is not None:
        project.notes = update_data.notes
    if update_data.installer_id is not None:
        project.installer_id = update_data.installer_id

    project.updated_at = datetime.utcnow()
    db.add(project)
    db.commit()
    db.refresh(project)
    return project
