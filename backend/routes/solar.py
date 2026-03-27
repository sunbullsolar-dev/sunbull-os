"""Solar sizing and bill analysis routes."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, BillAnalysis
from app.auth import get_current_user
from services.audit import create_audit_log

router = APIRouter(prefix="/api/solar", tags=["solar"])

# Solar constants
PANEL_WATTAGE = 435  # Watts
HOURS_PER_DAY = 4.5  # Average solar peak hours
INVERTER_EFFICIENCY = 0.97
SYSTEM_LOSS_FACTOR = 0.85  # Accounts for weather, angles, etc.

# Financing assumptions
SYSTEM_COST_PER_KW = 3000  # $/kW installed
LOAN_TERM_YEARS = 20
ANNUAL_INTEREST_RATE = 0.05  # 5%


class BillAnalysisRequest(BaseModel):
    """Bill analysis request model."""

    estimated_annual_kwh: Optional[float] = None
    monthly_kwh: Optional[float] = None
    cost_per_kwh: float
    state: str


class BillAnalysisResponse(BaseModel):
    """Bill analysis response model."""

    monthly_kwh: float
    monthly_bill: float
    annual_usage: float
    annual_bill: float
    cost_per_kwh: float
    good_sunlight_location: bool


class ProposalRequest(BaseModel):
    """Solar proposal request model."""

    estimated_annual_kwh: Optional[float] = None
    monthly_kwh: Optional[float] = None
    cost_per_kwh: float
    state: str


class ProposalResponse(BaseModel):
    """Solar proposal response model."""

    system_size_kw: float
    panel_count: int
    offset_percentage: float
    monthly_production_kwh: float
    annual_production_kwh: float
    monthly_payment: float
    estimated_monthly_savings: float
    annual_savings: float
    payback_period_years: float


class SavingsPlanRequest(BaseModel):
    """Savings plan request model (public)."""

    average_monthly_bill: Optional[float] = None
    estimated_annual_kwh: Optional[float] = None
    monthly_kwh: Optional[float] = None
    cost_per_kwh: float
    state: str


def _is_good_sunlight_location(state: str) -> bool:
    """
    Check if state has good sunlight for solar.

    Args:
        state: State code (2 letters)

    Returns:
        True if state has good solar potential
    """
    good_states = {
        "CA", "AZ", "TX", "NV", "UT", "CO",
        "NM", "HI", "FL", "GA", "SC", "AL"
    }
    return state.upper() in good_states


def _get_monthly_kwh(
    estimated_annual_kwh: Optional[float] = None,
    monthly_kwh: Optional[float] = None,
    average_monthly_bill: Optional[float] = None,
    cost_per_kwh: Optional[float] = None,
) -> float:
    """
    Resolve monthly kWh from various input formats.

    Args:
        estimated_annual_kwh: Annual usage
        monthly_kwh: Monthly usage
        average_monthly_bill: Monthly bill amount
        cost_per_kwh: Cost per kWh

    Returns:
        Monthly kWh value
    """
    if monthly_kwh:
        return monthly_kwh
    elif estimated_annual_kwh:
        return estimated_annual_kwh / 12
    elif average_monthly_bill and cost_per_kwh:
        return average_monthly_bill / cost_per_kwh
    else:
        raise ValueError("Must provide monthly_kwh, estimated_annual_kwh, or average_monthly_bill")


@router.post("/analyze-bill", response_model=BillAnalysisResponse)
def analyze_bill(
    bill_data: BillAnalysisRequest,
    current_user = Depends(get_current_user),
):
    """
    Analyze an electric bill.

    Accepts estimated_annual_kwh or monthly_kwh for usage data.

    Args:
        bill_data: Bill information
        current_user: Current authenticated user

    Returns:
        Bill analysis with annual projections
    """
    # Resolve monthly kWh
    monthly_kwh = _get_monthly_kwh(
        estimated_annual_kwh=bill_data.estimated_annual_kwh,
        monthly_kwh=bill_data.monthly_kwh,
    )

    monthly_bill = monthly_kwh * bill_data.cost_per_kwh
    annual_usage = monthly_kwh * 12
    annual_bill = monthly_bill * 12

    good_location = _is_good_sunlight_location(bill_data.state)

    return {
        "monthly_kwh": round(monthly_kwh, 2),
        "monthly_bill": round(monthly_bill, 2),
        "annual_usage": round(annual_usage, 2),
        "annual_bill": round(annual_bill, 2),
        "cost_per_kwh": bill_data.cost_per_kwh,
        "good_sunlight_location": good_location,
    }


def _calculate_monthly_payment(system_cost: float) -> float:
    """
    Calculate monthly payment for solar system loan.

    Using standard loan amortization formula.

    Args:
        system_cost: Total system cost

    Returns:
        Monthly payment amount
    """
    # Monthly interest rate
    monthly_rate = ANNUAL_INTEREST_RATE / 12
    num_payments = LOAN_TERM_YEARS * 12

    if monthly_rate == 0:
        return system_cost / num_payments

    # Amortization formula
    monthly_payment = system_cost * (
        (monthly_rate * (1 + monthly_rate) ** num_payments)
        / ((1 + monthly_rate) ** num_payments - 1)
    )

    return monthly_payment


@router.post("/proposal/{lead_id}", response_model=ProposalResponse)
def generate_solar_proposal(
    lead_id: int,
    proposal_data: ProposalRequest,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate a solar proposal for a lead.

    Uses 435W panels and calculates:
    - System size
    - Offset percentage
    - Monthly payment estimate
    - Savings plan data

    Updates lead with system sizing and proposal data.

    Args:
        lead_id: ID of the lead
        proposal_data: Lead's energy usage data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Solar proposal with sizing and financial details

    Raises:
        HTTPException: 404 if lead not found
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Resolve monthly kWh
    monthly_kwh = _get_monthly_kwh(
        estimated_annual_kwh=proposal_data.estimated_annual_kwh,
        monthly_kwh=proposal_data.monthly_kwh,
    )

    # Calculate system size
    # Monthly usage / (days * peak hours per day)
    daily_usage = monthly_kwh / 30
    required_output_kw = daily_usage / HOURS_PER_DAY
    # Account for system losses and inverter efficiency
    system_size_kw = required_output_kw / (SYSTEM_LOSS_FACTOR * INVERTER_EFFICIENCY)

    # Calculate panel count (using 435W panels)
    panel_count = int((system_size_kw * 1000) / PANEL_WATTAGE)
    actual_system_size_kw = (panel_count * PANEL_WATTAGE) / 1000

    # Calculate offset percentage
    monthly_production = (
        actual_system_size_kw
        * HOURS_PER_DAY
        * 30
        * SYSTEM_LOSS_FACTOR
        * INVERTER_EFFICIENCY
    )
    offset_percentage = (monthly_production / monthly_kwh) * 100

    # Calculate annual production
    annual_production = monthly_production * 12

    # Calculate costs
    system_cost = actual_system_size_kw * SYSTEM_COST_PER_KW
    monthly_payment = _calculate_monthly_payment(system_cost)

    # Calculate savings
    monthly_production_value = monthly_production * proposal_data.cost_per_kwh
    estimated_monthly_savings = monthly_production_value - monthly_payment
    annual_savings = estimated_monthly_savings * 12

    # Calculate payback period
    payback_period = (
        system_cost / annual_savings
        if annual_savings > 0
        else 99.0
    )

    # Update lead with proposal data
    lead.system_size_kw = actual_system_size_kw
    lead.panel_count = panel_count
    lead.offset_percentage = min(offset_percentage, 100)
    lead.estimated_monthly_payment = monthly_payment
    lead.estimated_monthly_savings = estimated_monthly_savings

    db.add(lead)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action="proposal_generated",
        entity_type="lead",
        entity_id=lead.id,
        details=f"Solar proposal generated: {actual_system_size_kw}kW system",
    )

    db.commit()

    return {
        "system_size_kw": round(actual_system_size_kw, 2),
        "panel_count": panel_count,
        "offset_percentage": round(min(offset_percentage, 100), 2),
        "monthly_production_kwh": round(monthly_production, 2),
        "annual_production_kwh": round(annual_production, 2),
        "monthly_payment": round(monthly_payment, 2),
        "estimated_monthly_savings": round(estimated_monthly_savings, 2),
        "annual_savings": round(annual_savings, 2),
        "payback_period_years": round(payback_period, 2),
    }


@router.post("/savings-plan", response_model=ProposalResponse)
def generate_savings_plan(
    plan_data: SavingsPlanRequest,
):
    """
    Public endpoint for homeowner portal savings plan calculation.

    No authentication required.
    Accepts average_monthly_bill, estimated_annual_kwh, or monthly_kwh.

    Args:
        plan_data: Energy usage and cost data

    Returns:
        Solar proposal with sizing and financial details
    """
    # Resolve monthly kWh
    try:
        monthly_kwh = _get_monthly_kwh(
            estimated_annual_kwh=plan_data.estimated_annual_kwh,
            monthly_kwh=plan_data.monthly_kwh,
            average_monthly_bill=plan_data.average_monthly_bill,
            cost_per_kwh=plan_data.cost_per_kwh,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Calculate system size
    daily_usage = monthly_kwh / 30
    required_output_kw = daily_usage / HOURS_PER_DAY
    system_size_kw = required_output_kw / (SYSTEM_LOSS_FACTOR * INVERTER_EFFICIENCY)

    # Calculate panel count
    panel_count = int((system_size_kw * 1000) / PANEL_WATTAGE)
    actual_system_size_kw = (panel_count * PANEL_WATTAGE) / 1000

    # Calculate offset percentage
    monthly_production = (
        actual_system_size_kw
        * HOURS_PER_DAY
        * 30
        * SYSTEM_LOSS_FACTOR
        * INVERTER_EFFICIENCY
    )
    offset_percentage = (monthly_production / monthly_kwh) * 100

    # Calculate annual production
    annual_production = monthly_production * 12

    # Calculate costs
    system_cost = actual_system_size_kw * SYSTEM_COST_PER_KW
    monthly_payment = _calculate_monthly_payment(system_cost)

    # Calculate savings
    monthly_production_value = monthly_production * plan_data.cost_per_kwh
    estimated_monthly_savings = monthly_production_value - monthly_payment
    annual_savings = estimated_monthly_savings * 12

    # Calculate payback period
    payback_period = (
        system_cost / annual_savings
        if annual_savings > 0
        else 99.0
    )

    return {
        "system_size_kw": round(actual_system_size_kw, 2),
        "panel_count": panel_count,
        "offset_percentage": round(min(offset_percentage, 100), 2),
        "monthly_production_kwh": round(monthly_production, 2),
        "annual_production_kwh": round(annual_production, 2),
        "monthly_payment": round(monthly_payment, 2),
        "estimated_monthly_savings": round(estimated_monthly_savings, 2),
        "annual_savings": round(annual_savings, 2),
        "payback_period_years": round(payback_period, 2),
    }


# ==========================================================================
# PUBLIC HOMEOWNER CONVERSION ENDPOINTS
# ==========================================================================


class FullSavingsRequest(BaseModel):
    """Full savings request that creates Lead + BillAnalysis records."""
    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    property_address: str
    city: str
    state: str
    zip_code: str
    utility_company: Optional[str] = None
    average_monthly_bill: float
    cost_per_kwh: float = 0.13


@router.post("/full-analysis")
def full_savings_analysis(
    data: FullSavingsRequest,
    db: Session = Depends(get_db),
):
    """Public: Create Lead + BillAnalysis + return savings. No auth."""
    from services.scoring import calculate_lead_score

    quality_score = calculate_lead_score(
        average_monthly_bill=data.average_monthly_bill,
        city=data.city, state=data.state,
    )
    mo_kwh = data.average_monthly_bill / data.cost_per_kwh

    lead = Lead(
        first_name=data.first_name, last_name=data.last_name,
        phone=data.phone, email=data.email,
        property_address=data.property_address,
        city=data.city, state=data.state, zip_code=data.zip_code,
        utility_company=data.utility_company,
        average_monthly_bill=data.average_monthly_bill,
        estimated_annual_kwh=mo_kwh * 12, cost_per_kwh=data.cost_per_kwh,
        source_type="web", deal_status="new",
        lead_quality_score=quality_score, homeowner_status="owner",
    )
    db.add(lead)
    db.flush()

    d_use = mo_kwh / 30
    req_kw = d_use / HOURS_PER_DAY
    sz_kw = req_kw / (SYSTEM_LOSS_FACTOR * INVERTER_EFFICIENCY)
    panels = int((sz_kw * 1000) / PANEL_WATTAGE)
    real_kw = (panels * PANEL_WATTAGE) / 1000
    mo_prod = real_kw * HOURS_PER_DAY * 30 * SYSTEM_LOSS_FACTOR * INVERTER_EFFICIENCY
    offset = min((mo_prod / mo_kwh) * 100, 100)
    cost = real_kw * SYSTEM_COST_PER_KW
    mo_pay = _calculate_monthly_payment(cost)
    mo_sav = (mo_prod * data.cost_per_kwh) - mo_pay
    yr_sav = mo_sav * 12
    payback = cost / yr_sav if yr_sav > 0 else 99.0
    good_loc = _is_good_sunlight_location(data.state)

    analysis = BillAnalysis(
        lead_id=lead.id, annual_kwh=mo_kwh * 12, monthly_kwh=mo_kwh,
        cost_per_kwh=data.cost_per_kwh,
        average_monthly_bill=data.average_monthly_bill, state=data.state,
        system_size_kw=real_kw, panel_count=panels,
        offset_percentage=round(offset, 2),
        estimated_monthly_payment=round(mo_pay, 2),
        estimated_monthly_savings=round(mo_sav, 2),
        annual_savings=round(yr_sav, 2),
        payback_period_years=round(payback, 2),
        system_cost=round(cost, 2),
        good_sunlight_location=good_loc, source="web",
    )
    db.add(analysis)
    db.commit()
    db.refresh(lead)

    # Send email notification for new lead
    try:
        from services.email_alerts import notify_new_lead
        notify_new_lead(
            lead_name=f"{data.first_name} {data.last_name}",
            phone=data.phone,
            email=data.email,
            city=data.city,
            monthly_bill=data.average_monthly_bill,
            lead_id=lead.id,
        )
    except Exception as e:
        print(f"Notification error (non-blocking): {e}")

    return {
        "lead_id": lead.id, "analysis_id": analysis.id,
        "current_monthly_bill": data.average_monthly_bill,
        "current_annual_spend": round(data.average_monthly_bill * 12, 2),
        "system_size_kw": round(real_kw, 2), "panel_count": panels,
        "offset_percentage": round(offset, 2),
        "monthly_payment": round(mo_pay, 2),
        "estimated_monthly_savings": round(mo_sav, 2),
        "annual_savings": round(yr_sav, 2),
        "payback_period_years": round(payback, 2),
        "good_sunlight_location": good_loc,
    }


class PublicBookingRequest(BaseModel):
    """Public appointment booking request."""
    lead_id: int
    preferred_date: str  # "YYYY-MM-DD"
    preferred_time: str  # "HH:MM"
    notes: Optional[str] = None


@router.post("/book-appointment")
def public_book_appointment(
    data: PublicBookingRequest,
    db: Session = Depends(get_db),
):
    """Public: Book consultation, auto-dispatch to best rep. No auth."""
    from app.models import Appointment, User
    from datetime import date as dt_date, time as dt_time

    lead = db.query(Lead).filter(Lead.id == data.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        parts = data.preferred_date.split("-")
        appt_date = dt_date(int(parts[0]), int(parts[1]), int(parts[2]))
        tp = data.preferred_time.split(":")
        appt_time = dt_time(int(tp[0]), int(tp[1]))
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid date/time format")

    reps = db.query(User).filter(
        User.role == "rep", User.is_active == True
    ).all()
    if not reps:
        raise HTTPException(status_code=500, detail="No reps available")

    # Sort by close_rate descending, then pick the first AVAILABLE rep
    reps.sort(key=lambda r: r.close_rate or 0, reverse=True)

    assigned_rep = None
    for rep in reps:
        # Check if this rep already has an appointment at this date+time
        conflict = db.query(Appointment).filter(
            Appointment.assigned_rep_id == rep.id,
            Appointment.appointment_date == appt_date,
            Appointment.appointment_time == appt_time,
            Appointment.appointment_status.notin_(["cancelled", "rescheduled"]),
        ).first()
        if not conflict:
            assigned_rep = rep
            break

    if not assigned_rep:
        raise HTTPException(
            status_code=409,
            detail="All reps are booked at that time. Please choose a different time slot.",
        )

    lead.deal_status = "appointed"
    lead.assigned_rep_id = assigned_rep.id

    appt = Appointment(
        lead_id=lead.id,
        assigned_rep_id=assigned_rep.id,
        appointment_date=appt_date,
        appointment_time=appt_time,
        scheduled_duration_minutes=90,
        appointment_status="scheduled",
        confirmation_status="pending",
        appointment_address=lead.property_address,
        notes=data.notes,
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)

    # Send email notifications
    try:
        from services.email_alerts import notify_appointment_booked
        notify_appointment_booked(
            lead_name=f"{lead.first_name} {lead.last_name}",
            phone=lead.phone,
            address=lead.property_address,
            date=data.preferred_date,
            time=data.preferred_time,
            rep_name=assigned_rep.full_name,
            rep_email=assigned_rep.email,
            monthly_bill=lead.average_monthly_bill or 0,
            appointment_id=appt.id,
        )
    except Exception as e:
        print(f"Notification error (non-blocking): {e}")

    return {
        "appointment_id": appt.id, "lead_id": lead.id,
        "date": data.preferred_date, "time": data.preferred_time,
        "rep_name": assigned_rep.full_name,
        "address": lead.property_address, "status": "scheduled",
        "message": "Your consultation has been booked! A solar expert will visit you at the scheduled time.",
    }


# ============================================================================
# CRM EXPORT ENDPOINTS
# ============================================================================

@router.get("/crm/leads")
def crm_export_leads(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Admin: Get all leads with appointments for CRM view / export."""
    from app.models import Appointment, User
    if current_user.role not in ("admin", "rep"):
        raise HTTPException(status_code=403, detail="Admin or rep access required")

    leads = db.query(Lead).order_by(Lead.created_at.desc()).all()
    result = []
    for lead in leads:
        # Get latest appointment
        appt = db.query(Appointment).filter(
            Appointment.lead_id == lead.id
        ).order_by(Appointment.created_at.desc()).first()

        # Get assigned rep name
        rep_name = None
        if lead.assigned_rep_id:
            rep = db.query(User).filter(User.id == lead.assigned_rep_id).first()
            rep_name = rep.full_name if rep else None

        # Get bill analysis
        analysis = db.query(BillAnalysis).filter(
            BillAnalysis.lead_id == lead.id
        ).first()

        result.append({
            "id": lead.id,
            "name": f"{lead.first_name} {lead.last_name}",
            "phone": lead.phone,
            "email": lead.email,
            "address": lead.property_address,
            "city": lead.city,
            "state": lead.state,
            "zip": lead.zip_code,
            "monthly_bill": lead.average_monthly_bill,
            "status": lead.deal_status,
            "source": lead.source_type,
            "quality_score": lead.lead_quality_score,
            "assigned_rep": rep_name,
            "system_kw": analysis.system_size_kw if analysis else None,
            "panels": analysis.panel_count if analysis else None,
            "monthly_payment": analysis.estimated_monthly_payment if analysis else None,
            "annual_savings": analysis.annual_savings if analysis else None,
            "appointment_date": str(appt.appointment_date) if appt else None,
            "appointment_time": str(appt.appointment_time) if appt else None,
            "appointment_status": appt.appointment_status if appt else None,
            "created_at": str(lead.created_at),
        })

    return {"total": len(result), "leads": result}


@router.get("/crm/export-csv")
def crm_export_csv(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Admin: Export all leads as CSV."""
    from app.models import Appointment, User
    from fastapi.responses import StreamingResponse
    import csv
    import io

    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    leads = db.query(Lead).order_by(Lead.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "First Name", "Last Name", "Phone", "Email",
        "Address", "City", "State", "Zip", "Monthly Bill",
        "Status", "Source", "Quality Score", "Assigned Rep",
        "System kW", "Panels", "Monthly Payment", "Annual Savings",
        "Appt Date", "Appt Time", "Appt Status", "Created",
    ])

    for lead in leads:
        appt = db.query(Appointment).filter(
            Appointment.lead_id == lead.id
        ).order_by(Appointment.created_at.desc()).first()
        rep = None
        if lead.assigned_rep_id:
            rep = db.query(User).filter(User.id == lead.assigned_rep_id).first()
        analysis = db.query(BillAnalysis).filter(BillAnalysis.lead_id == lead.id).first()

        writer.writerow([
            lead.id, lead.first_name, lead.last_name, lead.phone, lead.email,
            lead.property_address, lead.city, lead.state, lead.zip_code,
            lead.average_monthly_bill, lead.deal_status, lead.source_type,
            lead.lead_quality_score, rep.full_name if rep else "",
            analysis.system_size_kw if analysis else "",
            analysis.panel_count if analysis else "",
            analysis.estimated_monthly_payment if analysis else "",
            analysis.annual_savings if analysis else "",
            str(appt.appointment_date) if appt else "",
            str(appt.appointment_time) if appt else "",
            appt.appointment_status if appt else "",
            str(lead.created_at),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sunbull_leads_export.csv"},
    )
