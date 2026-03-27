"""Lead management routes."""
from datetime import datetime, timedelta, date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, User
from app.auth import get_current_user, require_role
from services.audit import create_audit_log
from services.scoring import calculate_lead_score

router = APIRouter(prefix="/api/leads", tags=["leads"])


class LeadCreate(BaseModel):
    """Create lead request model."""

    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    property_address: str
    city: str
    state: str
    zip_code: str
    homeowner_status: Optional[str] = None
    property_type: Optional[str] = None
    roof_type: Optional[str] = None
    utility_company: Optional[str] = None
    average_monthly_bill: Optional[float] = None
    estimated_annual_kwh: Optional[float] = None
    cost_per_kwh: Optional[float] = None
    source_type: str = "web"
    campaign: Optional[str] = None
    notes: Optional[str] = None


class LeadUpdate(BaseModel):
    """Update lead request model."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    property_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    homeowner_status: Optional[str] = None
    property_type: Optional[str] = None
    roof_type: Optional[str] = None
    utility_company: Optional[str] = None
    average_monthly_bill: Optional[float] = None
    estimated_annual_kwh: Optional[float] = None
    cost_per_kwh: Optional[float] = None
    campaign: Optional[str] = None
    notes: Optional[str] = None
    deal_status: Optional[str] = None


class LeadAssign(BaseModel):
    """Assign lead to rep request model."""

    rep_id: int


class LeadResponse(BaseModel):
    """Lead response model."""

    id: int
    first_name: str
    last_name: str
    phone: str
    email: Optional[str]
    property_address: str
    city: str
    state: str
    zip_code: str
    source_type: str
    deal_status: str
    lead_quality_score: int
    average_monthly_bill: Optional[float]
    estimated_annual_kwh: Optional[float]
    assigned_rep_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.post("/public", response_model=LeadResponse)
def create_lead_public(
    lead_data: LeadCreate,
    db: Session = Depends(get_db),
):
    """
    Public endpoint to create a new lead from homeowner portal.

    No authentication required.
    Auto-calculates lead_quality_score based on bill amount and location.

    Args:
        lead_data: Lead creation data
        db: Database session

    Returns:
        Created Lead record
    """
    # Calculate quality score
    quality_score = calculate_lead_score(
        average_monthly_bill=lead_data.average_monthly_bill,
        city=lead_data.city,
        state=lead_data.state,
        confirmation_strength=0,
    )

    lead = Lead(
        first_name=lead_data.first_name,
        last_name=lead_data.last_name,
        phone=lead_data.phone,
        email=lead_data.email,
        property_address=lead_data.property_address,
        city=lead_data.city,
        state=lead_data.state,
        zip_code=lead_data.zip_code,
        homeowner_status=lead_data.homeowner_status,
        property_type=lead_data.property_type,
        roof_type=lead_data.roof_type,
        utility_company=lead_data.utility_company,
        average_monthly_bill=lead_data.average_monthly_bill,
        estimated_annual_kwh=lead_data.estimated_annual_kwh,
        cost_per_kwh=lead_data.cost_per_kwh,
        source_type=lead_data.source_type,
        campaign=lead_data.campaign,
        deal_status="new",
        lead_quality_score=quality_score,
        notes=lead_data.notes,
    )

    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("", response_model=LeadResponse)
def create_lead(
    lead_data: LeadCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new lead (authenticated endpoint).

    Auto-calculates lead_quality_score based on bill amount and location.
    Creates an audit log entry.

    Args:
        lead_data: Lead creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created Lead record
    """
    # Calculate quality score
    quality_score = calculate_lead_score(
        average_monthly_bill=lead_data.average_monthly_bill,
        city=lead_data.city,
        state=lead_data.state,
        confirmation_strength=0,
    )

    lead = Lead(
        first_name=lead_data.first_name,
        last_name=lead_data.last_name,
        phone=lead_data.phone,
        email=lead_data.email,
        property_address=lead_data.property_address,
        city=lead_data.city,
        state=lead_data.state,
        zip_code=lead_data.zip_code,
        homeowner_status=lead_data.homeowner_status,
        property_type=lead_data.property_type,
        roof_type=lead_data.roof_type,
        utility_company=lead_data.utility_company,
        average_monthly_bill=lead_data.average_monthly_bill,
        estimated_annual_kwh=lead_data.estimated_annual_kwh,
        cost_per_kwh=lead_data.cost_per_kwh,
        source_type=lead_data.source_type,
        campaign=lead_data.campaign,
        deal_status="new",
        lead_quality_score=quality_score,
        notes=lead_data.notes,
    )

    db.add(lead)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="create",
        entity_type="lead",
        entity_id=lead.id,
        new_value=f"{lead.first_name} {lead.last_name}",
        details=f"Lead created with quality score {quality_score}",
    )

    db.commit()
    db.refresh(lead)
    return lead


@router.get("", response_model=List[LeadResponse])
def list_leads(
    deal_status: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List leads filtered by role.

    Reps only see their assigned leads. Admins see all leads.

    Args:
        deal_status: Filter by deal status
        source_type: Filter by source type
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of Lead records
    """
    query = db.query(Lead)

    # Filter by role
    if current_user.role == "rep":
        query = query.filter(Lead.assigned_rep_id == current_user.id)

    # Filter by deal_status
    if deal_status:
        query = query.filter(Lead.deal_status == deal_status)

    # Filter by source_type
    if source_type:
        query = query.filter(Lead.source_type == source_type)

    return (
        query.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()
    )


@router.get("/{lead_id}", response_model=LeadResponse)
def get_lead(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get lead details.

    Args:
        lead_id: ID of the lead
        current_user: Current authenticated user
        db: Database session

    Returns:
        Lead record

    Raises:
        HTTPException: 404 if lead not found, 403 if unauthorized
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    # Check authorization
    if (
        current_user.role == "rep"
        and lead.assigned_rep_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this lead",
        )

    return lead


@router.put("/{lead_id}", response_model=LeadResponse)
def update_lead(
    lead_id: int,
    lead_data: LeadUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update a lead.

    ENFORCES LEAD LOCKING - only assigned rep or admin can update.
    Creates an audit log entry for every change with previous/new values.

    Args:
        lead_id: ID of the lead
        lead_data: Updated lead data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated Lead record

    Raises:
        HTTPException: 404 if lead not found, 403 if unauthorized
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    # Check authorization (only assigned rep or admin)
    if (
        current_user.role == "rep"
        and lead.assigned_rep_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this lead",
        )

    # Track changes for audit log
    updates = []

    # Update fields
    if lead_data.first_name is not None and lead.first_name != lead_data.first_name:
        updates.append(("first_name", lead.first_name, lead_data.first_name))
        lead.first_name = lead_data.first_name

    if lead_data.last_name is not None and lead.last_name != lead_data.last_name:
        updates.append(("last_name", lead.last_name, lead_data.last_name))
        lead.last_name = lead_data.last_name

    if lead_data.phone is not None and lead.phone != lead_data.phone:
        updates.append(("phone", lead.phone, lead_data.phone))
        lead.phone = lead_data.phone

    if lead_data.email is not None and lead.email != lead_data.email:
        updates.append(("email", lead.email, lead_data.email))
        lead.email = lead_data.email

    if lead_data.property_address is not None and lead.property_address != lead_data.property_address:
        updates.append(("property_address", lead.property_address, lead_data.property_address))
        lead.property_address = lead_data.property_address

    if lead_data.city is not None and lead.city != lead_data.city:
        updates.append(("city", lead.city, lead_data.city))
        lead.city = lead_data.city

    if lead_data.state is not None and lead.state != lead_data.state:
        updates.append(("state", lead.state, lead_data.state))
        lead.state = lead_data.state

    if lead_data.zip_code is not None and lead.zip_code != lead_data.zip_code:
        updates.append(("zip_code", lead.zip_code, lead_data.zip_code))
        lead.zip_code = lead_data.zip_code

    if lead_data.homeowner_status is not None and lead.homeowner_status != lead_data.homeowner_status:
        updates.append(("homeowner_status", lead.homeowner_status, lead_data.homeowner_status))
        lead.homeowner_status = lead_data.homeowner_status

    if lead_data.property_type is not None and lead.property_type != lead_data.property_type:
        updates.append(("property_type", lead.property_type, lead_data.property_type))
        lead.property_type = lead_data.property_type

    if lead_data.roof_type is not None and lead.roof_type != lead_data.roof_type:
        updates.append(("roof_type", lead.roof_type, lead_data.roof_type))
        lead.roof_type = lead_data.roof_type

    if lead_data.utility_company is not None and lead.utility_company != lead_data.utility_company:
        updates.append(("utility_company", lead.utility_company, lead_data.utility_company))
        lead.utility_company = lead_data.utility_company

    if (
        lead_data.average_monthly_bill is not None
        and lead.average_monthly_bill != lead_data.average_monthly_bill
    ):
        updates.append(("average_monthly_bill", lead.average_monthly_bill, lead_data.average_monthly_bill))
        lead.average_monthly_bill = lead_data.average_monthly_bill

    if (
        lead_data.estimated_annual_kwh is not None
        and lead.estimated_annual_kwh != lead_data.estimated_annual_kwh
    ):
        updates.append(("estimated_annual_kwh", lead.estimated_annual_kwh, lead_data.estimated_annual_kwh))
        lead.estimated_annual_kwh = lead_data.estimated_annual_kwh

    if (
        lead_data.cost_per_kwh is not None
        and lead.cost_per_kwh != lead_data.cost_per_kwh
    ):
        updates.append(("cost_per_kwh", lead.cost_per_kwh, lead_data.cost_per_kwh))
        lead.cost_per_kwh = lead_data.cost_per_kwh

    if lead_data.campaign is not None and lead.campaign != lead_data.campaign:
        updates.append(("campaign", lead.campaign, lead_data.campaign))
        lead.campaign = lead_data.campaign

    if lead_data.notes is not None and lead.notes != lead_data.notes:
        updates.append(("notes", lead.notes, lead_data.notes))
        lead.notes = lead_data.notes

    if lead_data.deal_status is not None and lead.deal_status != lead_data.deal_status:
        updates.append(("deal_status", lead.deal_status, lead_data.deal_status))
        lead.deal_status = lead_data.deal_status

    # Save changes
    db.add(lead)
    db.flush()

    # Create audit logs for each change
    for field_name, old_value, new_value in updates:
        create_audit_log(
            db=db,
            user_id=current_user.id,
            action="update",
            entity_type="lead",
            entity_id=lead.id,
            previous_value=old_value,
            new_value=new_value,
            details=f"Field '{field_name}' updated",
        )

    db.commit()
    db.refresh(lead)
    return lead


@router.post("/{lead_id}/assign", response_model=LeadResponse)
def assign_lead(
    lead_id: int,
    assignment: LeadAssign,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Assign lead to a rep.

    Admin only. Creates an audit log entry.

    Args:
        lead_id: ID of the lead
        assignment: Assignment data with rep_id
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Updated Lead record

    Raises:
        HTTPException: 404 if lead or rep not found, 403 if unauthorized
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    # Verify rep exists
    rep = db.query(User).filter(User.id == assignment.rep_id).first()
    if not rep:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rep not found")

    old_rep_id = lead.assigned_rep_id
    lead.assigned_rep_id = assignment.rep_id

    db.add(lead)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="assign",
        entity_type="lead",
        entity_id=lead.id,
        previous_value=old_rep_id,
        new_value=assignment.rep_id,
        details=f"Lead assigned from rep {old_rep_id} to rep {assignment.rep_id}",
    )

    db.commit()
    db.refresh(lead)
    return lead


@router.get("/rehash-queue", response_model=List[dict])
def get_rehash_queue(
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get leads needing follow-up (follow_up_required=True).

    Args:
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        List of leads requiring follow-up
    """
    leads = (
        db.query(Lead)
        .filter(Lead.follow_up_required == True)
        .order_by(Lead.next_follow_up_date.asc())
        .all()
    )

    result = []
    for lead in leads:
        result.append(
            {
                "lead_id": lead.id,
                "name": f"{lead.first_name} {lead.last_name}",
                "phone": lead.phone,
                "property_address": lead.property_address,
                "city": lead.city,
                "state": lead.state,
                "assigned_rep_id": lead.assigned_rep_id,
                "follow_up_required": lead.follow_up_required,
                "next_follow_up_date": lead.next_follow_up_date,
            }
        )

    return result
