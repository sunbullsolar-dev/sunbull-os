"""Lead management routes."""
from datetime import datetime, timedelta, date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, User, Action, Task, Appointment, Project
from app.auth import get_current_user, require_role
from services.audit import create_audit_log
from services.scoring import calculate_lead_score
from services.actions import log_action
from services.outcome_engine import check_open_tasks

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
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None


class LeadAssign(BaseModel):
    """Assign lead to rep request model."""

    rep_id: int


class LeadResponse(BaseModel):
    """Lead response model."""

    id: int
    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    property_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    source_type: Optional[str] = None
    deal_status: str = "new"
    lead_quality_score: int = 0
    average_monthly_bill: Optional[float] = None
    estimated_annual_kwh: Optional[float] = None
    cost_per_kwh: Optional[float] = None
    assigned_rep_id: Optional[int] = None
    setter_id: Optional[int] = None
    runner_id: Optional[int] = None
    closer_id: Optional[int] = None
    rehash_rep_id: Optional[int] = None
    homeowner_status: Optional[str] = None
    property_type: Optional[str] = None
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    is_held: Optional[bool] = None
    is_archived: Optional[bool] = None
    last_outcome: Optional[str] = None
    next_follow_up_date: Optional[datetime] = None
    deal_value: Optional[float] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

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


class AppointmentSubmission(BaseModel):
    """Public appointment submission from telemarketing/canvassers."""
    first_name: str
    last_name: str
    phone: str
    property_address: str
    city: str = ""
    state: str = "CA"
    zip_code: str = ""
    email: Optional[str] = None
    average_monthly_bill: Optional[float] = None
    appointment_date: str  # YYYY-MM-DD
    appointment_time: str  # HH:MM
    appointment_type: str = "in_person"  # in_person, zoom, phone
    zoom_email: Optional[str] = None
    recording_url: Optional[str] = None
    summary: Optional[str] = None
    submitted_by_name: Optional[str] = None
    source: str = "telemarketing"  # telemarketing, canvasser, web, admin


@router.post("/submit-appointment")
def submit_appointment_public(
    data: AppointmentSubmission,
    db: Session = Depends(get_db),
):
    """
    Public appointment submission endpoint for telemarketing and canvassers.
    No authentication required. Creates lead + appointment in one call.
    Sets deal_status = 'submitted' and confirmation_status = 'pending'.
    """
    from datetime import date as date_type, time as time_type

    # Check if lead already exists by phone
    existing_lead = db.query(Lead).filter(Lead.phone == data.phone).first()

    if existing_lead:
        lead = existing_lead
        # Update address if provided
        if data.property_address:
            lead.property_address = data.property_address
        if data.city:
            lead.city = data.city
        if data.state:
            lead.state = data.state
        if data.zip_code:
            lead.zip_code = data.zip_code
        if data.average_monthly_bill:
            lead.average_monthly_bill = data.average_monthly_bill
    else:
        quality_score = calculate_lead_score(
            average_monthly_bill=data.average_monthly_bill,
            city=data.city,
            state=data.state,
            confirmation_strength=0,
        )
        lead = Lead(
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            email=data.email,
            property_address=data.property_address,
            city=data.city,
            state=data.state,
            zip_code=data.zip_code,
            average_monthly_bill=data.average_monthly_bill,
            source_type=data.source,
            deal_status="submitted",
            lead_quality_score=quality_score,
            homeowner_status="owner",
            property_type="single_family",
        )
        db.add(lead)
        db.flush()

    # Update lead status to submitted
    lead.deal_status = "submitted"

    # Parse date/time
    try:
        parts = data.appointment_date.split("-")
        appt_date = date_type(int(parts[0]), int(parts[1]), int(parts[2]))
        tparts = data.appointment_time.split(":")
        appt_time = time_type(int(tparts[0]), int(tparts[1]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    # Create appointment
    appointment = Appointment(
        lead_id=lead.id,
        assigned_rep_id=1,  # Placeholder, will be assigned during dispatch
        appointment_date=appt_date,
        appointment_time=appt_time,
        appointment_status="scheduled",
        confirmation_status="pending",
        appointment_address=data.property_address,
        appointment_type=data.appointment_type,
        zoom_email=data.zoom_email,
        recording_url=data.recording_url,
        telemarketing_summary=data.summary,
        submitted_by_name=data.submitted_by_name,
        submission_source=data.source,
        dispatch_status="pending",
    )
    db.add(appointment)

    # Log action
    log_action(
        db=db,
        lead_id=lead.id,
        action_type="created",
        note=f"Appointment submitted via {data.source}" + (f" by {data.submitted_by_name}" if data.submitted_by_name else ""),
    )

    db.commit()
    db.refresh(lead)
    db.refresh(appointment)

    return {
        "ok": True,
        "lead_id": lead.id,
        "appointment_id": appointment.id,
        "message": "Appointment submitted successfully",
    }


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
    assigned_rep_id: Optional[int] = Query(None),
    city: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List leads filtered by role and criteria.

    Reps only see their assigned leads. Admins see all leads.

    Args:
        deal_status: Filter by deal status
        source_type: Filter by source type
        assigned_rep_id: Filter by assigned rep ID
        city: Filter by city
        date_from: Filter created_at >= (YYYY-MM-DD)
        date_to: Filter created_at <= (YYYY-MM-DD)
        search: Search in first_name, last_name, phone, email
        skip: Number of records to skip
        limit: Number of records to return
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of Lead records
    """
    query = db.query(Lead).filter(Lead.is_archived == False)

    # Filter by role
    if current_user.role == "rep":
        query = query.filter(Lead.assigned_rep_id == current_user.id)

    # Filter by deal_status
    if deal_status:
        query = query.filter(Lead.deal_status == deal_status)

    # Filter by source_type
    if source_type:
        query = query.filter(Lead.source_type == source_type)

    # Filter by assigned_rep_id (admin only)
    if assigned_rep_id and current_user.role == "admin":
        query = query.filter(Lead.assigned_rep_id == assigned_rep_id)

    # Filter by city
    if city:
        query = query.filter(Lead.city.ilike(f"%{city}%"))

    # Filter by date range
    if date_from:
        from_date = datetime.strptime(date_from, "%Y-%m-%d")
        query = query.filter(Lead.created_at >= from_date)

    if date_to:
        to_date = datetime.strptime(date_to, "%Y-%m-%d")
        to_date = to_date.replace(hour=23, minute=59, second=59)
        query = query.filter(Lead.created_at <= to_date)

    # Search across multiple fields
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Lead.first_name.ilike(search_term))
            | (Lead.last_name.ilike(search_term))
            | (Lead.phone.ilike(search_term))
            | (Lead.email.ilike(search_term))
        )

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

    if lead_data.geo_lat is not None:
        lead.geo_lat = lead_data.geo_lat
    if lead_data.geo_lng is not None:
        lead.geo_lng = lead_data.geo_lng

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

    # Log action to timeline
    log_action(
        db=db,
        lead_id=lead.id,
        action_type="assigned",
        rep_id=assignment.rep_id,
        note=f"Assigned by {current_user.full_name}" + (f" (previously rep #{old_rep_id})" if old_rep_id else ""),
    )

    db.commit()
    db.refresh(lead)
    return lead


@router.get("/rehash-queue")
def get_rehash_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Dedicated rehash queue: leads in rehash/follow_up/dead status.
    Grouped by reason, with scheduled callbacks and assigned reps.
    """
    from app.models import RehashEntry
    leads = (
        db.query(Lead)
        .filter(Lead.deal_status.in_(["rehash", "follow_up", "dead"]))
        .order_by(Lead.next_follow_up_date.asc().nullslast(), Lead.updated_at.desc())
        .limit(200)
        .all()
    )

    # Get rehash entries for context
    rehash_entries = {}
    try:
        entries = db.query(RehashEntry).filter(RehashEntry.status == "pending").all()
        for e in entries:
            rehash_entries[e.lead_id] = {
                "reason": e.reason,
                "callback_at": str(e.callback_at) if e.callback_at else None,
                "attempts": e.attempts,
                "original_rep_id": e.original_rep_id,
            }
    except Exception:
        pass

    # Get associated tasks
    task_map = {}
    try:
        from app.models import Task as _T
        tasks = db.query(_T).filter(
            _T.lead_id.in_([l.id for l in leads]),
            _T.status.in_(["pending", "overdue"]),
            _T.task_type.in_(["rehash", "follow_up"]),
        ).all()
        for t in tasks:
            if t.lead_id not in task_map:
                task_map[t.lead_id] = []
            task_map[t.lead_id].append({
                "task_id": t.id,
                "title": t.title,
                "due_date": str(t.due_date) if t.due_date else None,
                "status": t.status,
                "task_type": t.task_type,
            })
    except Exception:
        pass

    # Get rep names
    rep_map = {}
    try:
        reps = db.query(User).filter(User.role == "rep").all()
        for r in reps:
            rep_map[r.id] = r.full_name
    except Exception:
        pass

    result = []
    for lead in leads:
        rehash_info = rehash_entries.get(lead.id, {})
        result.append({
            "lead_id": lead.id,
            "name": f"{lead.first_name} {lead.last_name}",
            "phone": lead.phone,
            "property_address": lead.property_address,
            "city": lead.city,
            "state": lead.state,
            "deal_status": lead.deal_status,
            "last_outcome": lead.last_outcome,
            "average_monthly_bill": lead.average_monthly_bill,
            "assigned_rep_id": lead.assigned_rep_id,
            "assigned_rep_name": rep_map.get(lead.assigned_rep_id),
            "rehash_rep_id": lead.rehash_rep_id,
            "rehash_rep_name": rep_map.get(lead.rehash_rep_id),
            "follow_up_required": lead.follow_up_required,
            "next_follow_up_date": str(lead.next_follow_up_date) if lead.next_follow_up_date else None,
            "rehash_reason": rehash_info.get("reason"),
            "rehash_callback": rehash_info.get("callback_at"),
            "rehash_attempts": rehash_info.get("attempts", 0),
            "tasks": task_map.get(lead.id, []),
            "created_at": str(lead.created_at) if lead.created_at else None,
        })

    return result


class NoteCreate(BaseModel):
    note: str
    note_type: Optional[str] = "general"


@router.post("/{lead_id}/notes")
def add_lead_note(
    lead_id: int,
    data: NoteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Add a note or call log to a lead's timeline.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    if current_user.role == "rep" and lead.assigned_rep_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if not data.note or not data.note.strip():
        raise HTTPException(status_code=400, detail="Note text required")

    action_type = "call_log" if data.note_type == "call_log" else "note"
    log_action(
        db=db,
        lead_id=lead.id,
        action_type=action_type,
        rep_id=current_user.id,
        note=data.note.strip(),
    )
    db.commit()
    return {"ok": True, "message": "Note added"}


@router.get("/{lead_id}/timeline")
def get_lead_timeline(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get FULL timeline for a lead: actions + tasks + appointments + project.

    This is the single source of truth for what happened with a lead.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    if current_user.role == "rep" and lead.assigned_rep_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this lead's timeline",
        )

    # Actions (main timeline)
    actions = (
        db.query(Action)
        .filter(Action.lead_id == lead_id)
        .order_by(Action.created_at.asc())
        .all()
    )

    timeline = []
    for action in actions:
        rep = db.query(User).filter(User.id == action.rep_id).first() if action.rep_id else None
        timeline.append({
            "id": action.id,
            "entry_type": "action",
            "action_type": action.action_type,
            "rep_id": action.rep_id,
            "rep_name": rep.full_name if rep else None,
            "appointment_id": action.appointment_id,
            "note": action.note,
            "created_at": action.created_at.isoformat() if action.created_at else None,
        })

    # Tasks
    tasks = db.query(Task).filter(Task.lead_id == lead_id).all()
    task_list = []
    for t in tasks:
        assignee = db.query(User).filter(User.id == t.assigned_to).first()
        task_list.append({
            "id": t.id,
            "task_type": t.task_type,
            "title": t.title,
            "status": t.status,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "assigned_to": t.assigned_to,
            "assignee_name": assignee.full_name if assignee else None,
            "source_outcome": t.source_outcome,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })

    # Appointments
    appointments = db.query(Appointment).filter(Appointment.lead_id == lead_id).all()
    appt_list = []
    for a in appointments:
        rep = db.query(User).filter(User.id == a.assigned_rep_id).first()
        appt_list.append({
            "id": a.id,
            "date": a.appointment_date.isoformat() if a.appointment_date else None,
            "time": a.appointment_time.isoformat() if a.appointment_time else None,
            "status": a.appointment_status,
            "outcome": a.outcome,
            "rep_name": rep.full_name if rep else None,
        })

    # Project (if exists)
    project = db.query(Project).filter(Project.lead_id == lead_id).first()
    project_data = None
    if project:
        project_data = {
            "id": project.id,
            "install_status": project.install_status,
            "funding_status": project.funding_status,
            "payment_status": project.payment_status,
            "deal_value": project.deal_value,
            "sold_date": project.sold_date.isoformat() if project.sold_date else None,
        }

    # Open tasks check
    open_tasks = check_open_tasks(db, lead_id)

    # Ownership
    ownership = {
        "setter": None,
        "runner": None,
        "closer": None,
        "rehash_rep": None,
    }
    if lead.setter_id:
        setter = db.query(User).filter(User.id == lead.setter_id).first()
        ownership["setter"] = setter.full_name if setter else None
    if lead.runner_id:
        runner = db.query(User).filter(User.id == lead.runner_id).first()
        ownership["runner"] = runner.full_name if runner else None
    if lead.closer_id:
        closer = db.query(User).filter(User.id == lead.closer_id).first()
        ownership["closer"] = closer.full_name if closer else None
    if lead.rehash_rep_id:
        rehash = db.query(User).filter(User.id == lead.rehash_rep_id).first()
        ownership["rehash_rep"] = rehash.full_name if rehash else None

    return {
        "lead_id": lead_id,
        "lead_name": f"{lead.first_name} {lead.last_name}",
        "deal_status": lead.deal_status,
        "is_held": lead.is_held,
        "last_outcome": lead.last_outcome,
        "timeline": timeline,
        "tasks": task_list,
        "appointments": appt_list,
        "project": project_data,
        "open_tasks_count": len(open_tasks),
        "has_open_tasks": len(open_tasks) > 0,
        "ownership": ownership,
    }
