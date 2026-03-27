"""Deal and pipeline tracking routes."""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import Lead, Deal, Commission, User
from app.auth import get_current_user, require_role
from services.audit import create_audit_log

router = APIRouter(prefix="/api/deals", tags=["deals"])


class DealCreate(BaseModel):
    """Create deal request model."""

    lead_id: int
    deal_value: float
    installer_id: Optional[int] = None


class DealStageUpdate(BaseModel):
    """Update deal stage request model."""

    pipeline_stage: str


class DealResponse(BaseModel):
    """Deal response model."""

    id: int
    lead_id: int
    deal_value: float
    pipeline_stage: str
    installer_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=DealResponse)
def create_deal(
    deal_data: DealCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a deal from a closed lead.

    Creates Deal + Commission records.
    Updates lead.deal_value and lead.commission_amount.

    Args:
        deal_data: Deal creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created Deal record

    Raises:
        HTTPException: 404 if lead not found, 400 if lead not closed won
    """
    lead = db.query(Lead).filter(Lead.id == deal_data.lead_id).first()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Verify lead is closed won
    if lead.deal_status != "closed_won":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead must be closed won to create a deal",
        )

    deal = Deal(
        lead_id=deal_data.lead_id,
        rep_id=lead.assigned_rep_id or current_user.id,
        deal_value=deal_data.deal_value,
        pipeline_stage="sold",
        installer_id=deal_data.installer_id,
        responsible_party="installer" if deal_data.installer_id else "rep",
        sold_at=datetime.utcnow(),
        stage_entered_at=datetime.utcnow(),
    )

    db.add(deal)
    db.flush()

    # Create Commission record if rep assigned
    if lead.assigned_rep_id:
        # Standard commission: 14% of deal value
        commission_rate = 0.14
        commission_amount = deal_data.deal_value * commission_rate
        company_revenue = deal_data.deal_value * (1 - commission_rate)

        commission = Commission(
            deal_id=deal.id,
            rep_id=lead.assigned_rep_id,
            deal_value=deal_data.deal_value,
            commission_rate=commission_rate,
            commission_amount=commission_amount,
            company_revenue=company_revenue,
            status="earned",
        )
        db.add(commission)
        db.flush()

        # Update lead with commission info
        lead.commission_amount = commission_amount

    # Update lead with deal value
    lead.deal_value = deal_data.deal_value
    db.add(lead)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="create",
        entity_type="deal",
        entity_id=deal.id,
        new_value=f"Deal ${deal_data.deal_value}",
        details=f"Deal created from lead {lead.id}",
    )

    db.commit()
    db.refresh(deal)

    return deal


@router.get("", response_model=List[DealResponse])
def list_deals(
    pipeline_stage: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List deals with lead info.

    Args:
        pipeline_stage: Filter by pipeline stage
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of Deal records
    """
    query = db.query(Deal)

    if pipeline_stage:
        query = query.filter(Deal.pipeline_stage == pipeline_stage)

    deals = query.order_by(Deal.created_at.desc()).offset(skip).limit(limit).all()

    return deals


@router.get("/pipeline", response_model=dict)
def get_pipeline_overview(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get pipeline overview with count and value per stage.

    Args:
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Pipeline overview data
    """
    pipeline_stages = [
        "sold",
        "installed",
        "submitted_for_funding",
        "funded",
        "paid",
    ]

    pipeline_data = {}

    for stage in pipeline_stages:
        deals = db.query(Deal).filter(Deal.pipeline_stage == stage).all()

        count = len(deals)
        total_value = sum(d.deal_value for d in deals) if deals else 0

        pipeline_data[stage] = {
            "count": count,
            "total_value": round(total_value, 2),
        }

    return {"pipeline": pipeline_data}


@router.put("/{deal_id}/stage", response_model=DealResponse)
def advance_pipeline_stage(
    deal_id: int,
    stage_data: DealStageUpdate,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Advance deal to new pipeline stage.

    Creates audit log with timestamp.

    Args:
        deal_id: ID of the deal
        stage_data: New stage
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Updated Deal record

    Raises:
        HTTPException: 404 if deal not found
    """
    deal = db.query(Deal).filter(Deal.id == deal_id).first()

    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found"
        )

    old_stage = deal.pipeline_stage

    # Update stage and set stage-specific timestamp
    deal.pipeline_stage = stage_data.pipeline_stage
    deal.stage_entered_at = datetime.utcnow()

    stage_timestamp_map = {
        "sold": "sold_at",
        "installed": "installed_at",
        "submitted_for_funding": "submitted_at",
        "funded": "funded_at",
        "paid": "paid_at",
    }
    ts_field = stage_timestamp_map.get(stage_data.pipeline_stage)
    if ts_field:
        setattr(deal, ts_field, datetime.utcnow())

    db.add(deal)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="update",
        entity_type="deal",
        entity_id=deal.id,
        previous_value=old_stage,
        new_value=stage_data.pipeline_stage,
        details=f"Pipeline stage advanced from {old_stage} to {stage_data.pipeline_stage}",
    )

    db.commit()
    db.refresh(deal)

    return deal


@router.get("/commissions", response_model=dict)
def get_commission_tracking(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get commission tracking.

    Reps see their own commissions, admins see all.

    Args:
        current_user: Current authenticated user
        db: Database session

    Returns:
        Commission tracking data
    """
    # If user is a rep, show their commissions
    if current_user.role == "rep":
        commissions = (
            db.query(Commission)
            .filter(Commission.rep_id == current_user.id)
            .all()
        )
    else:
        # Admin sees all commissions
        commissions = db.query(Commission).all()

    commission_data = []
    for comm in commissions:
        rep = db.query(User).filter(User.id == comm.rep_id).first()
        deal = db.query(Deal).filter(Deal.id == comm.deal_id).first()

        commission_data.append(
            {
                "id": comm.id,
                "rep_id": comm.rep_id,
                "rep_name": rep.full_name if rep else "Unknown",
                "deal_id": comm.deal_id,
                "deal_value": comm.deal_value,
                "commission_rate": comm.commission_rate,
                "commission_amount": round(comm.commission_amount, 2),
                "company_revenue": round(comm.company_revenue, 2),
                "status": comm.status,
                "created_at": comm.created_at,
            }
        )

    return {"commissions": commission_data}
