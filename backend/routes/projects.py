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
    install_scheduled_date: Optional[str] = None  # YYYY-MM-DD
    install_completed_date: Optional[str] = None  # YYYY-MM-DD
    funding_submitted_date: Optional[str] = None  # YYYY-MM-DD
    funding_approved_date: Optional[str] = None  # YYYY-MM-DD
    payment_received_date: Optional[str] = None  # YYYY-MM-DD
    notes: Optional[str] = None
    installer_id: Optional[int] = None


class ProjectResponse(BaseModel):
    """Project response model."""
    id: int
    deal_id: Optional[int]
    lead_id: Optional[int]
    install_status: str
    funding_status: str
    payment_status: str
    install_scheduled_date: Optional[datetime]
    install_completed_date: Optional[datetime]
    funding_submitted_date: Optional[datetime]
    funding_approved_date: Optional[datetime]
    payment_received_date: Optional[datetime]
    notes: Optional[str]
    installer_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectDashboardItem(BaseModel):
    """Dashboard summary item."""
    stage: str
    count: int
    total_value: float


class ProjectDashboardResponse(BaseModel):
    """Project dashboard summary."""
    install_stages: List[ProjectDashboardItem]
    funding_stages: List[ProjectDashboardItem]
    payment_stages: List[ProjectDashboardItem]
    delayed_projects: List[dict]
    aging_stats: dict


@router.get("", response_model=List[ProjectResponse])
def list_projects(
    install_status: Optional[str] = Query(None),
    funding_status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    installer_id: Optional[int] = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    List all projects with optional filtering.

    Args:
        install_status: Filter by installation status
        funding_status: Filter by funding status
        payment_status: Filter by payment status
        installer_id: Filter by installer ID
        current_user: Current authenticated user (admin only)
        db: Database session

    Returns:
        List of projects matching filters
    """
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

    return projects


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get a single project with related lead and deal information.

    Args:
        project_id: Project ID
        current_user: Current authenticated user (admin only)
        db: Database session

    Returns:
        Project details

    Raises:
        HTTPException: 404 if project not found
    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    update_data: ProjectUpdate,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Update a project's status and details.

    Args:
        project_id: Project ID
        update_data: Fields to update
        current_user: Current authenticated user (admin only)
        db: Database session

    Returns:
        Updated project

    Raises:
        HTTPException: 404 if project not found
    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Update fields
    if update_data.install_status is not None:
        project.install_status = update_data.install_status
    if update_data.funding_status is not None:
        project.funding_status = update_data.funding_status
    if update_data.payment_status is not None:
        project.payment_status = update_data.payment_status
    if update_data.install_scheduled_date is not None:
        project.install_scheduled_date = datetime.fromisoformat(update_data.install_scheduled_date)
    if update_data.install_completed_date is not None:
        project.install_completed_date = datetime.fromisoformat(update_data.install_completed_date)
    if update_data.funding_submitted_date is not None:
        project.funding_submitted_date = datetime.fromisoformat(update_data.funding_submitted_date)
    if update_data.funding_approved_date is not None:
        project.funding_approved_date = datetime.fromisoformat(update_data.funding_approved_date)
    if update_data.payment_received_date is not None:
        project.payment_received_date = datetime.fromisoformat(update_data.payment_received_date)
    if update_data.notes is not None:
        project.notes = update_data.notes
    if update_data.installer_id is not None:
        project.installer_id = update_data.installer_id

    project.updated_at = datetime.utcnow()

    db.add(project)
    db.commit()
    db.refresh(project)

    return project


@router.get("/dashboard/summary")
def get_project_dashboard(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get project dashboard summary: counts and values per stage, delayed projects, aging stats.

    Returns:
        Dashboard summary with stage breakdowns and delayed project list
    """
    projects = db.query(Project).all()

    # Build stage summaries
    install_stages = {}
    funding_stages = {}
    payment_stages = {}

    now = datetime.utcnow()
    delayed_projects = []

    for proj in projects:
        # Install stages
        if proj.install_status not in install_stages:
            install_stages[proj.install_status] = {"count": 0, "total_value": 0.0}
        install_stages[proj.install_status]["count"] += 1

        # Funding stages
        if proj.funding_status not in funding_stages:
            funding_stages[proj.funding_status] = {"count": 0, "total_value": 0.0}
        funding_stages[proj.funding_status]["count"] += 1

        # Payment stages
        if proj.payment_status not in payment_stages:
            payment_stages[proj.payment_status] = {"count": 0, "total_value": 0.0}
        payment_stages[proj.payment_status]["count"] += 1

        # Get deal value if available
        if proj.deal_id:
            deal = db.query(Deal).filter(Deal.id == proj.deal_id).first()
            if deal:
                install_stages[proj.install_status]["total_value"] += deal.deal_value or 0
                funding_stages[proj.funding_status]["total_value"] += deal.deal_value or 0
                payment_stages[proj.payment_status]["total_value"] += deal.deal_value or 0

        # Check for delayed projects (>30 days in current stage)
        stage_entered = None
        if proj.install_status == "scheduled" and proj.install_scheduled_date:
            stage_entered = proj.install_scheduled_date
        elif proj.install_status == "in_progress" and proj.install_scheduled_date:
            stage_entered = proj.install_scheduled_date
        elif proj.funding_status == "submitted" and proj.funding_submitted_date:
            stage_entered = proj.funding_submitted_date

        if stage_entered and (now - stage_entered) > timedelta(days=30):
            deal = db.query(Deal).filter(Deal.id == proj.deal_id).first() if proj.deal_id else None
            lead = db.query(Lead).filter(Lead.id == proj.lead_id).first() if proj.lead_id else None

            delayed_projects.append({
                "project_id": proj.id,
                "deal_id": proj.deal_id,
                "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
                "install_status": proj.install_status,
                "funding_status": proj.funding_status,
                "days_in_stage": (now - stage_entered).days,
                "deal_value": deal.deal_value if deal else 0,
            })

    # Calculate aging stats
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
        "install_stages": [
            {"stage": k, "count": v["count"], "total_value": v["total_value"]}
            for k, v in install_stages.items()
        ],
        "funding_stages": [
            {"stage": k, "count": v["count"], "total_value": v["total_value"]}
            for k, v in funding_stages.items()
        ],
        "payment_stages": [
            {"stage": k, "count": v["count"], "total_value": v["total_value"]}
            for k, v in payment_stages.items()
        ],
        "delayed_projects": delayed_projects,
        "aging_stats": aging_stats,
    }
