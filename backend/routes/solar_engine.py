"""
Solar Engine API Routes
=======================
Endpoints for the real solar analysis engine.
"""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, SolarEstimate
from app.auth import get_current_user
from services.solar_engine import run_full_analysis
from services.actions import log_action

router = APIRouter(prefix="/api/solar/engine", tags=["solar_engine"])


class AnalysisRequest(BaseModel):
    """Request to run the solar analysis engine on a lead."""
    lead_id: Optional[int] = None
    # Address (optional if lead_id provided)
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = "CA"
    zip_code: Optional[str] = None
    # Usage inputs (at least one required)
    monthly_bill: Optional[float] = None
    annual_kwh: Optional[float] = None
    monthly_kwh: Optional[float] = None
    cost_per_kwh: Optional[float] = None
    # Overrides
    utility_override: Optional[str] = None
    shading_loss: Optional[float] = 0.10
    offset_target: Optional[float] = 1.0
    include_battery: Optional[bool] = False


class QuickAnalysisRequest(BaseModel):
    """Lightweight request for public-facing quick estimates."""
    address: str
    city: str
    state: str = "CA"
    zip_code: str
    monthly_bill: float
    cost_per_kwh: Optional[float] = None


@router.post("/analyze")
def analyze_lead(
    req: AnalysisRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Run the full solar analysis engine.

    Can be called with a lead_id (pulls address/usage from DB) or
    with raw inputs. Results are stored as a SolarEstimate record.
    """
    # Pull lead data if lead_id provided
    lead = None
    if req.lead_id:
        lead = db.query(Lead).filter(Lead.id == req.lead_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        # Use lead data as defaults (request params override)
        address = req.address or lead.property_address or ""
        city = req.city or lead.city or ""
        state = req.state or lead.state or "CA"
        zip_code = req.zip_code or lead.zip_code or ""
        monthly_bill = req.monthly_bill if req.monthly_bill is not None else lead.average_monthly_bill
        annual_kwh = req.annual_kwh if req.annual_kwh is not None else lead.estimated_annual_kwh
        cost_per_kwh = req.cost_per_kwh if req.cost_per_kwh is not None else lead.cost_per_kwh
        utility_override = req.utility_override or lead.utility_company
    else:
        address = req.address or ""
        city = req.city or ""
        state = req.state or "CA"
        zip_code = req.zip_code or ""
        monthly_bill = req.monthly_bill
        annual_kwh = req.annual_kwh
        cost_per_kwh = req.cost_per_kwh
        utility_override = req.utility_override

    # Run the engine
    result = run_full_analysis(
        address=address,
        city=city,
        state=state,
        zip_code=zip_code,
        monthly_bill=monthly_bill,
        annual_kwh=annual_kwh,
        monthly_kwh=req.monthly_kwh,
        cost_per_kwh=cost_per_kwh,
        utility_override=utility_override,
        shading_loss=req.shading_loss or 0.10,
        offset_target=req.offset_target or 1.0,
        include_battery_scenario=req.include_battery or False,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result)

    # Store estimate if we have a lead
    if lead:
        summary = result["summary"]
        estimate = SolarEstimate(
            lead_id=lead.id,
            latitude=result["location"]["latitude"],
            longitude=result["location"]["longitude"],
            utility_code=summary["utility_code"],
            utility_name=summary["utility"],
            solar_region=result["location"]["solar_region"],
            sun_hours_per_day=result["solar_resource"]["sun_hours_per_day"],
            production_factor=result["solar_resource"]["production_factor_kwh_per_kw"],
            annual_kwh=summary["annual_kwh"],
            monthly_kwh=result["usage"]["monthly_kwh"],
            monthly_bill=result["usage"]["monthly_bill"],
            rate_per_kwh=summary["rate_per_kwh"],
            usage_calculation_method=result["usage"]["calculation_method"],
            peak_rate=result["rate_structure"]["peak_rate"],
            off_peak_rate=result["rate_structure"]["off_peak_rate"],
            nem_policy=result["rate_structure"]["nem_policy"],
            nem_export_rate=result["rate_structure"]["nem_export_rate"],
            system_size_kw=summary["system_size_kw"],
            panel_count=summary["panel_count"],
            annual_production_kwh=summary["annual_production_kwh"],
            self_consumption_pct=summary["self_consumption_pct"],
            self_consumed_kwh=result["consumption_model"]["self_consumed_kwh"],
            exported_kwh=result["consumption_model"]["exported_kwh"],
            monthly_bill_before=summary["monthly_bill_before"],
            monthly_bill_after=summary["monthly_bill_after"],
            monthly_savings=summary["monthly_savings"],
            annual_savings=summary["annual_savings"],
            savings_percentage=summary["savings_percentage"],
            scenarios_json=json.dumps(result["scenarios"]),
            system_cost_gross=result["financials"]["system_cost_gross"],
            itc_credit=result["financials"]["itc_credit_30pct"],
            net_system_cost=result["financials"]["net_system_cost"],
            monthly_payment=result["financials"]["monthly_payment"],
            cash_payback_years=result["financials"]["cash_payback_years"],
            confidence_score=summary["confidence_score"],
            assumptions_json=json.dumps(result["assumptions_used"]),
            missing_data_json=json.dumps(result["missing_data"]),
            source="engine",
        )
        db.add(estimate)

        # Update lead with latest estimate data
        lead.utility_company = summary["utility_code"]
        lead.system_size_kw = summary["system_size_kw"]
        lead.panel_count = summary["panel_count"]
        lead.estimated_annual_kwh = summary["annual_kwh"]
        lead.cost_per_kwh = summary["rate_per_kwh"]
        lead.estimated_monthly_savings = summary["monthly_savings"]
        lead.offset_percentage = result["system"]["offset_percentage"]

        db.flush()

        # Log action
        log_action(
            db=db,
            lead_id=lead.id,
            action_type="solar_analysis",
            rep_id=current_user.id,
            note=f"Solar analysis: {summary['system_size_kw']}kW, {summary['panel_count']} panels, "
                 f"${summary['monthly_savings']:.0f}/mo savings ({summary['confidence_score']} confidence)",
        )

        db.commit()
        db.refresh(estimate)
        result["estimate_id"] = estimate.id

    return result


@router.post("/quick-estimate")
def quick_estimate(req: QuickAnalysisRequest):
    """
    Public endpoint: Quick solar estimate from address + bill.
    No auth required. Does not store results.
    """
    result = run_full_analysis(
        address=req.address,
        city=req.city,
        state=req.state,
        zip_code=req.zip_code,
        monthly_bill=req.monthly_bill,
        cost_per_kwh=req.cost_per_kwh,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result)

    # Return simplified version for public use
    summary = result["summary"]
    return {
        "utility": summary["utility"],
        "annual_kwh": summary["annual_kwh"],
        "rate_per_kwh": summary["rate_per_kwh"],
        "system_size_kw": summary["system_size_kw"],
        "panel_count": summary["panel_count"],
        "annual_production_kwh": summary["annual_production_kwh"],
        "self_consumption_pct": summary["self_consumption_pct"],
        "monthly_bill_before": summary["monthly_bill_before"],
        "monthly_bill_after": summary["monthly_bill_after"],
        "monthly_savings": summary["monthly_savings"],
        "annual_savings": summary["annual_savings"],
        "confidence_score": summary["confidence_score"],
        "scenarios": {
            "conservative": {
                "monthly_savings": result["scenarios"]["conservative"]["monthly_savings"],
                "annual_savings": result["scenarios"]["conservative"]["annual_savings"],
            },
            "base": {
                "monthly_savings": result["scenarios"]["base"]["monthly_savings"],
                "annual_savings": result["scenarios"]["base"]["annual_savings"],
            },
            "aggressive": {
                "monthly_savings": result["scenarios"]["aggressive"]["monthly_savings"],
                "annual_savings": result["scenarios"]["aggressive"]["annual_savings"],
            },
        },
        "assumptions": result["assumptions_used"],
    }


@router.get("/estimate/{lead_id}")
def get_lead_estimates(
    lead_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all solar estimates for a lead, most recent first."""
    estimates = (
        db.query(SolarEstimate)
        .filter(SolarEstimate.lead_id == lead_id)
        .order_by(SolarEstimate.created_at.desc())
        .all()
    )

    return {
        "lead_id": lead_id,
        "count": len(estimates),
        "estimates": [
            {
                "id": e.id,
                "utility": e.utility_name,
                "utility_code": e.utility_code,
                "annual_kwh": e.annual_kwh,
                "rate_per_kwh": e.rate_per_kwh,
                "system_size_kw": e.system_size_kw,
                "panel_count": e.panel_count,
                "annual_production_kwh": e.annual_production_kwh,
                "self_consumption_pct": e.self_consumption_pct,
                "monthly_bill_before": e.monthly_bill_before,
                "monthly_bill_after": e.monthly_bill_after,
                "monthly_savings": e.monthly_savings,
                "annual_savings": e.annual_savings,
                "savings_percentage": e.savings_percentage,
                "confidence_score": e.confidence_score,
                "nem_policy": e.nem_policy,
                "peak_rate": e.peak_rate,
                "off_peak_rate": e.off_peak_rate,
                "system_cost_gross": e.system_cost_gross,
                "net_system_cost": e.net_system_cost,
                "monthly_payment": e.monthly_payment,
                "cash_payback_years": e.cash_payback_years,
                "assumptions": json.loads(e.assumptions_json) if e.assumptions_json else [],
                "missing_data": json.loads(e.missing_data_json) if e.missing_data_json else [],
                "scenarios": json.loads(e.scenarios_json) if e.scenarios_json else {},
                "created_at": str(e.created_at),
            }
            for e in estimates
        ],
    }
