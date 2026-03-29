"""
Sunbull Solar Analysis Engine
=============================
The master orchestrator. Connects location, solar resource, utility rates,
usage analysis, system sizing, and savings calculations into one pipeline.

Every estimate is:
- location-based
- utility-aware
- TOU + NEM 3.0 aware
- confidence-scored
- auditable (every assumption tracked)

This replaces the old flat-rate calculator with a real solar economics engine.
"""
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import math

from services.location_engine import resolve_location, lookup_utility
from services.solar_resource import get_solar_resource
from services.utility_rates import get_utility_rate


# ============================================================================
# CONSTANTS
# ============================================================================
PANEL_WATTAGE = 435          # Watts per panel (standard residential)
SYSTEM_EFFICIENCY = 0.80     # Overall system derate factor
DEFAULT_SHADING_LOSS = 0.10  # 10% shading loss default
INVERTER_EFFICIENCY = 0.97   # Microinverter efficiency
DEGRADATION_YEAR1 = 0.02     # 2% first-year degradation
DEGRADATION_ANNUAL = 0.005   # 0.5% annual degradation after year 1

# Financing defaults
SYSTEM_COST_PER_WATT = 3.00  # $/W installed (before incentives)
ITC_RATE = 0.30              # 30% federal Investment Tax Credit
LOAN_TERM_YEARS = 25
ANNUAL_INTEREST_RATE = 0.049 # 4.9% APR


# ============================================================================
# USAGE / BILL ENGINE
# ============================================================================

def resolve_usage(
    monthly_bill: Optional[float] = None,
    annual_kwh: Optional[float] = None,
    monthly_kwh: Optional[float] = None,
    rate_per_kwh: Optional[float] = None,
    utility_code: Optional[str] = None,
    state: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve annual kWh usage from whatever input is available.

    Priority:
    1. actual annual_kwh (highest confidence)
    2. monthly_kwh × 12
    3. monthly_bill ÷ rate_per_kwh × 12
    4. monthly_bill ÷ estimated_rate × 12 (lowest confidence)

    Returns:
        {
            "annual_kwh": float,
            "monthly_kwh": float,
            "monthly_bill": float,
            "rate_used": float,
            "calculation_method": str,
            "confidence": "HIGH" | "MED" | "LOW"
        }
    """
    assumptions = []

    # Get rate info for bill→kWh conversion
    rate_info = get_utility_rate(utility_code, state)
    effective_rate = rate_per_kwh or rate_info["rate_per_kwh"]

    # Method 1: Direct kWh input
    if annual_kwh and annual_kwh > 0:
        mo_kwh = annual_kwh / 12
        mo_bill = mo_kwh * effective_rate
        return {
            "annual_kwh": round(annual_kwh, 0),
            "monthly_kwh": round(mo_kwh, 0),
            "monthly_bill": round(mo_bill if not monthly_bill else monthly_bill, 2),
            "rate_used": effective_rate,
            "calculation_method": "actual_annual_kwh",
            "confidence": "HIGH",
            "assumptions": ["Usage provided directly"],
        }

    if monthly_kwh and monthly_kwh > 0:
        yr_kwh = monthly_kwh * 12
        mo_bill = monthly_kwh * effective_rate
        return {
            "annual_kwh": round(yr_kwh, 0),
            "monthly_kwh": round(monthly_kwh, 0),
            "monthly_bill": round(mo_bill if not monthly_bill else monthly_bill, 2),
            "rate_used": effective_rate,
            "calculation_method": "monthly_kwh_x12",
            "confidence": "HIGH",
            "assumptions": ["Monthly kWh provided directly"],
        }

    # Method 2: Bill ÷ rate
    if monthly_bill and monthly_bill > 0:
        if rate_per_kwh and rate_per_kwh > 0:
            mo_kwh = monthly_bill / rate_per_kwh
            return {
                "annual_kwh": round(mo_kwh * 12, 0),
                "monthly_kwh": round(mo_kwh, 0),
                "monthly_bill": round(monthly_bill, 2),
                "rate_used": rate_per_kwh,
                "calculation_method": "bill_div_provided_rate",
                "confidence": "MED",
                "assumptions": [
                    f"Rate ${rate_per_kwh}/kWh provided by user",
                    "kWh = monthly bill / rate",
                ],
            }
        else:
            # Use utility-estimated rate
            mo_kwh = monthly_bill / effective_rate
            return {
                "annual_kwh": round(mo_kwh * 12, 0),
                "monthly_kwh": round(mo_kwh, 0),
                "monthly_bill": round(monthly_bill, 2),
                "rate_used": effective_rate,
                "calculation_method": "bill_div_estimated_rate",
                "confidence": "LOW",
                "assumptions": [
                    f"Rate ${effective_rate}/kWh estimated from {rate_info.get('utility_name', 'avg')}",
                    "kWh = monthly bill / estimated rate",
                    "Actual rate may differ based on usage tier",
                ],
            }

    # Fallback: no usable input
    return {
        "annual_kwh": 0,
        "monthly_kwh": 0,
        "monthly_bill": 0,
        "rate_used": effective_rate,
        "calculation_method": "none",
        "confidence": "LOW",
        "assumptions": ["No usage data provided"],
    }


# ============================================================================
# SYSTEM SIZING ENGINE
# ============================================================================

def size_system(
    annual_kwh: float,
    sun_hours_per_day: float,
    system_efficiency: float = SYSTEM_EFFICIENCY,
    shading_loss: float = DEFAULT_SHADING_LOSS,
    offset_target: float = 1.0,  # 100% offset by default
) -> Dict[str, Any]:
    """
    Calculate system size using real formula.

    system_size_kw = (annual_kwh × offset_target) / (365 × sun_hours × efficiency × (1 - shading))

    Returns system specs and production estimates.
    """
    if annual_kwh <= 0 or sun_hours_per_day <= 0:
        return {
            "system_size_kw": 0, "panel_count": 0,
            "annual_production_kwh": 0,
            "error": "Insufficient data for sizing",
        }

    # Core sizing formula
    effective_efficiency = system_efficiency * (1 - shading_loss) * INVERTER_EFFICIENCY
    target_annual = annual_kwh * offset_target

    system_size_kw = target_annual / (365 * sun_hours_per_day * effective_efficiency)

    # Round to panel count
    panel_count = math.ceil((system_size_kw * 1000) / PANEL_WATTAGE)
    actual_size_kw = (panel_count * PANEL_WATTAGE) / 1000

    # Calculate actual annual production
    annual_production = actual_size_kw * 365 * sun_hours_per_day * effective_efficiency

    # First year derate
    year1_production = annual_production * (1 - DEGRADATION_YEAR1)

    offset_pct = (annual_production / annual_kwh) * 100 if annual_kwh > 0 else 0

    return {
        "system_size_kw": round(actual_size_kw, 2),
        "panel_count": panel_count,
        "panel_wattage": PANEL_WATTAGE,
        "annual_production_kwh": round(annual_production, 0),
        "year1_production_kwh": round(year1_production, 0),
        "monthly_production_kwh": round(annual_production / 12, 0),
        "offset_percentage": round(min(offset_pct, 150), 1),  # Cap at 150%
        "system_efficiency": system_efficiency,
        "shading_loss_pct": shading_loss * 100,
        "inverter_efficiency": INVERTER_EFFICIENCY,
        "effective_derate": round(effective_efficiency, 4),
    }


# ============================================================================
# SELF-CONSUMPTION + EXPORT MODEL
# ============================================================================

def model_consumption(
    annual_production_kwh: float,
    annual_usage_kwh: float,
    rate_data: Dict[str, Any],
    self_consumption_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Model production vs usage timing (simplified).

    Solar production timing:
    - ~15% during peak (late afternoon overlap)
    - ~45% during off-peak
    - ~40% during super off-peak (midday prime)

    Self-consumption depends on:
    - Household usage pattern (daytime vs evening)
    - System size relative to usage
    - Whether battery storage exists

    Without battery, typical self-consumption: 30-50%
    With battery: 70-90%
    """
    if annual_production_kwh <= 0:
        return {"self_consumed_kwh": 0, "exported_kwh": 0}

    # Estimate self-consumption if not provided
    if self_consumption_pct is None:
        # Heuristic: smaller systems relative to usage → higher self-consumption
        ratio = annual_production_kwh / annual_usage_kwh if annual_usage_kwh > 0 else 1.0
        if ratio <= 0.5:
            self_consumption_pct = 0.60   # Small system, most is used
        elif ratio <= 0.8:
            self_consumption_pct = 0.45   # Moderate system
        elif ratio <= 1.0:
            self_consumption_pct = 0.38   # Full offset system
        elif ratio <= 1.2:
            self_consumption_pct = 0.32   # Slight over-production
        else:
            self_consumption_pct = 0.25   # Significant over-production

    self_consumed = annual_production_kwh * self_consumption_pct
    exported = annual_production_kwh - self_consumed

    # Break down self-consumption by TOU period
    solar_peak_pct = rate_data.get("solar_during_peak_pct", 0.15)
    solar_off_peak_pct = rate_data.get("solar_during_off_peak_pct", 0.45)
    solar_super_off_peak_pct = rate_data.get("solar_during_super_off_peak_pct", 0.40)

    # Value of self-consumed electricity (avoids buying at retail)
    # Weighted by when solar is produced during each TOU period
    peak_rate = rate_data.get("peak_rate", rate_data.get("rate_per_kwh", 0.30))
    off_peak_rate = rate_data.get("off_peak_rate", rate_data.get("rate_per_kwh", 0.20))
    super_off_peak_rate = rate_data.get("super_off_peak_rate", off_peak_rate * 0.85)

    # Self-consumed kWh valued at the retail rate of the period
    self_consumed_value = (
        self_consumed * solar_peak_pct * peak_rate +
        self_consumed * solar_off_peak_pct * off_peak_rate +
        self_consumed * solar_super_off_peak_pct * super_off_peak_rate
    )

    # Exported kWh compensated at NEM export rates
    nem_export_avg = rate_data.get("nem_export_rate_avg", 0.06)
    nem_export_peak = rate_data.get("nem_export_peak", nem_export_avg * 1.5)
    nem_export_off_peak = rate_data.get("nem_export_off_peak", nem_export_avg * 0.7)

    export_value = (
        exported * solar_peak_pct * nem_export_peak +
        exported * solar_off_peak_pct * nem_export_off_peak +
        exported * solar_super_off_peak_pct * nem_export_off_peak  # super off-peak exports ≈ off-peak rate
    )

    # Blended effective rate of all solar (self-consumed + exported)
    total_value = self_consumed_value + export_value
    effective_solar_rate = total_value / annual_production_kwh if annual_production_kwh > 0 else 0

    return {
        "self_consumed_kwh": round(self_consumed, 0),
        "exported_kwh": round(exported, 0),
        "self_consumption_pct": round(self_consumption_pct * 100, 1),
        "export_pct": round((1 - self_consumption_pct) * 100, 1),
        "self_consumed_value": round(self_consumed_value, 2),
        "export_value": round(export_value, 2),
        "total_solar_value": round(total_value, 2),
        "effective_solar_rate": round(effective_solar_rate, 4),
        "nem_policy": rate_data.get("nem_policy", "unknown"),
        "nem_export_rate_avg": nem_export_avg,
    }


# ============================================================================
# SAVINGS ENGINE WITH 3 SCENARIOS
# ============================================================================

def calculate_savings(
    annual_usage_kwh: float,
    rate_data: Dict[str, Any],
    system_data: Dict[str, Any],
    consumption_data: Dict[str, Any],
    monthly_solar_payment: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate real savings with TOU + NEM 3.0 economics.

    current_cost = annual_kwh × blended_rate + fixed_charges
    solar_benefit = self_consumed_value + export_value
    remaining_bill = (annual_kwh - self_consumed) × blended_rate + fixed_charges
    net_cost_with_solar = remaining_bill + solar_payment - export_credits
    savings = current_cost - net_cost_with_solar
    """
    blended_rate = rate_data.get("rate_per_kwh", 0.30)
    fixed_monthly = rate_data.get("fixed_monthly", 15.0)

    # Current annual cost (no solar)
    current_annual_cost = annual_usage_kwh * blended_rate + (fixed_monthly * 12)
    current_monthly_bill = current_annual_cost / 12

    # Solar production value
    total_solar_value = consumption_data.get("total_solar_value", 0)
    self_consumed_kwh = consumption_data.get("self_consumed_kwh", 0)

    # Remaining electricity needed from grid
    remaining_grid_kwh = max(0, annual_usage_kwh - self_consumed_kwh)
    remaining_grid_cost = remaining_grid_kwh * blended_rate + (fixed_monthly * 12)

    # Solar payment (loan or lease)
    system_cost = system_data.get("system_size_kw", 0) * SYSTEM_COST_PER_WATT * 1000
    itc_savings = system_cost * ITC_RATE
    net_system_cost = system_cost - itc_savings

    if monthly_solar_payment is None:
        monthly_solar_payment = _calculate_loan_payment(net_system_cost)

    annual_solar_payment = monthly_solar_payment * 12

    # Export credits reduce the remaining bill
    export_value = consumption_data.get("export_value", 0)

    # Net annual cost with solar
    net_annual_cost = remaining_grid_cost + annual_solar_payment - export_value
    net_monthly_cost = net_annual_cost / 12

    # Savings
    annual_savings = current_annual_cost - net_annual_cost
    monthly_savings = annual_savings / 12

    # Payback (cash purchase, no loan)
    cash_payback = net_system_cost / (total_solar_value) if total_solar_value > 0 else 99

    return {
        "current_monthly_bill": round(current_monthly_bill, 2),
        "current_annual_cost": round(current_annual_cost, 2),
        "system_cost_gross": round(system_cost, 2),
        "itc_credit": round(itc_savings, 2),
        "net_system_cost": round(net_system_cost, 2),
        "monthly_solar_payment": round(monthly_solar_payment, 2),
        "annual_solar_payment": round(annual_solar_payment, 2),
        "remaining_grid_cost_annual": round(remaining_grid_cost, 2),
        "export_credits_annual": round(export_value, 2),
        "net_monthly_bill_with_solar": round(net_monthly_cost, 2),
        "monthly_savings": round(monthly_savings, 2),
        "annual_savings": round(annual_savings, 2),
        "savings_percentage": round((annual_savings / current_annual_cost) * 100, 1) if current_annual_cost > 0 else 0,
        "cash_payback_years": round(cash_payback, 1),
    }


def generate_scenarios(
    annual_usage_kwh: float,
    rate_data: Dict[str, Any],
    system_data: Dict[str, Any],
    base_consumption: Dict[str, Any],
    monthly_solar_payment: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Generate 3 savings scenarios: conservative, base, aggressive.

    Conservative: lower self-consumption, higher shading loss
    Base: realistic middle ground
    Aggressive: higher self-consumption (battery behavior), lower losses
    """
    base = calculate_savings(annual_usage_kwh, rate_data, system_data, base_consumption, monthly_solar_payment)

    # Conservative scenario: 25% less self-consumption, 5% more shading
    conservative_self_pct = max(0.20, (base_consumption.get("self_consumption_pct", 38) / 100) - 0.10)
    cons_consumption = model_consumption(
        system_data["annual_production_kwh"] * 0.95,  # 5% more loss
        annual_usage_kwh,
        rate_data,
        self_consumption_pct=conservative_self_pct,
    )
    conservative = calculate_savings(annual_usage_kwh, rate_data, system_data, cons_consumption, monthly_solar_payment)

    # Aggressive scenario: higher self-consumption (battery-like), minimal extra loss
    aggressive_self_pct = min(0.80, (base_consumption.get("self_consumption_pct", 38) / 100) + 0.20)
    agg_consumption = model_consumption(
        system_data["annual_production_kwh"],
        annual_usage_kwh,
        rate_data,
        self_consumption_pct=aggressive_self_pct,
    )
    aggressive = calculate_savings(annual_usage_kwh, rate_data, system_data, agg_consumption, monthly_solar_payment)

    return {
        "conservative": {**conservative, "scenario": "conservative", "label": "Low Estimate",
                         "self_consumption_pct": round(conservative_self_pct * 100, 1)},
        "base": {**base, "scenario": "base", "label": "Expected",
                 "self_consumption_pct": base_consumption.get("self_consumption_pct", 38)},
        "aggressive": {**aggressive, "scenario": "aggressive", "label": "High Estimate (w/ Battery)",
                       "self_consumption_pct": round(aggressive_self_pct * 100, 1)},
    }


# ============================================================================
# CONFIDENCE ENGINE
# ============================================================================

def score_confidence(
    usage_confidence: str,
    rate_confidence: str,
    solar_confidence: str,
    utility_confidence: str,
    has_real_bill: bool = False,
    has_address: bool = False,
) -> Dict[str, Any]:
    """
    Score overall estimate confidence.

    HIGH = real bill + correct utility + good solar data
    MED = partial real data
    LOW = mostly estimated
    """
    scores = {"HIGH": 3, "MED": 2, "LOW": 1}

    total = (
        scores.get(usage_confidence, 1) +
        scores.get(rate_confidence, 1) +
        scores.get(solar_confidence, 1) +
        scores.get(utility_confidence, 1)
    )

    # Bonuses
    if has_real_bill:
        total += 2
    if has_address:
        total += 1

    # Thresholds
    if total >= 14:
        level = "HIGH"
    elif total >= 9:
        level = "MED"
    else:
        level = "LOW"

    return {
        "confidence_score": level,
        "confidence_points": total,
        "max_points": 17,
        "breakdown": {
            "usage_data": usage_confidence,
            "rate_data": rate_confidence,
            "solar_resource": solar_confidence,
            "utility_lookup": utility_confidence,
            "has_real_bill": has_real_bill,
            "has_address": has_address,
        },
    }


# ============================================================================
# LOAN CALCULATOR
# ============================================================================

def _calculate_loan_payment(principal: float) -> float:
    """Standard amortization formula."""
    if principal <= 0:
        return 0
    monthly_rate = ANNUAL_INTEREST_RATE / 12
    num_payments = LOAN_TERM_YEARS * 12
    if monthly_rate == 0:
        return principal / num_payments
    payment = principal * (
        (monthly_rate * (1 + monthly_rate) ** num_payments) /
        ((1 + monthly_rate) ** num_payments - 1)
    )
    return payment


# ============================================================================
# MASTER ANALYSIS PIPELINE
# ============================================================================

def run_full_analysis(
    # Address
    address: str = "",
    city: str = "",
    state: str = "CA",
    zip_code: str = "",
    # Usage inputs (provide at least one)
    monthly_bill: Optional[float] = None,
    annual_kwh: Optional[float] = None,
    monthly_kwh: Optional[float] = None,
    cost_per_kwh: Optional[float] = None,
    # Overrides
    utility_override: Optional[str] = None,
    shading_loss: float = DEFAULT_SHADING_LOSS,
    offset_target: float = 1.0,
    self_consumption_override: Optional[float] = None,
    include_battery_scenario: bool = False,
) -> Dict[str, Any]:
    """
    Run the complete solar analysis pipeline.

    This is the main entry point. It:
    1. Resolves location + utility
    2. Gets solar resource data
    3. Calculates usage from inputs
    4. Gets real utility rates (TOU + NEM)
    5. Sizes the system
    6. Models self-consumption vs export
    7. Calculates savings (3 scenarios)
    8. Scores confidence
    9. Tracks all assumptions

    Returns the full SolarEstimate structure.
    """
    assumptions = []
    missing_data = []

    # ── Step 1: Location ──────────────────────────────────────
    location = resolve_location(address, city, state, zip_code)

    if not zip_code:
        missing_data.append("zip_code")
    if not location["latitude"]:
        missing_data.append("coordinates")
        assumptions.append("Could not determine exact coordinates")

    # ── Step 2: Utility ───────────────────────────────────────
    utility_code = utility_override or location["utility_code"]
    utility_confidence = location["utility_confidence"]

    if utility_code == "UNKNOWN":
        missing_data.append("utility_provider")
        assumptions.append("Utility provider unknown — using state average rates")

    # ── Step 3: Solar Resource ────────────────────────────────
    solar = get_solar_resource(
        latitude=location["latitude"],
        utility_code=utility_code,
        state=state,
    )

    assumptions.append(f"Peak sun hours: {solar['sun_hours_per_day']} hrs/day ({solar['data_source']})")

    # ── Step 4: Utility Rates ─────────────────────────────────
    rate_data = get_utility_rate(
        utility_code=utility_code,
        state=state,
        manual_rate=cost_per_kwh,
    )

    if rate_data["rate_source"] == "utility_table":
        assumptions.append(f"Rate: ${rate_data['rate_per_kwh']}/kWh from {rate_data['utility_name']} ({rate_data.get('rate_schedule', '')})")
    elif rate_data["rate_source"] == "manual":
        assumptions.append(f"Rate: ${rate_data['rate_per_kwh']}/kWh provided by user")
    else:
        assumptions.append(f"Rate: ${rate_data['rate_per_kwh']}/kWh estimated ({rate_data['rate_source']})")

    if rate_data.get("nem_policy"):
        assumptions.append(f"Net metering: {rate_data['nem_policy']} (export rate ~${rate_data.get('nem_export_rate_avg', 0):.2f}/kWh)")

    # ── Step 5: Usage ─────────────────────────────────────────
    usage = resolve_usage(
        monthly_bill=monthly_bill,
        annual_kwh=annual_kwh,
        monthly_kwh=monthly_kwh,
        rate_per_kwh=cost_per_kwh,
        utility_code=utility_code,
        state=state,
    )

    if usage["annual_kwh"] <= 0:
        missing_data.append("usage_data")
        return {
            "error": "Cannot generate estimate without usage data",
            "missing_data": missing_data,
            "assumptions": assumptions,
        }

    assumptions.extend(usage.get("assumptions", []))

    # ── Step 6: System Sizing ─────────────────────────────────
    system = size_system(
        annual_kwh=usage["annual_kwh"],
        sun_hours_per_day=solar["sun_hours_per_day"],
        system_efficiency=SYSTEM_EFFICIENCY,
        shading_loss=shading_loss,
        offset_target=offset_target,
    )

    assumptions.append(f"System derate: {SYSTEM_EFFICIENCY * 100:.0f}%")
    assumptions.append(f"Shading loss: {shading_loss * 100:.0f}%")
    assumptions.append(f"Panel wattage: {PANEL_WATTAGE}W")

    # ── Step 7: Self-Consumption Model ────────────────────────
    consumption = model_consumption(
        annual_production_kwh=system["annual_production_kwh"],
        annual_usage_kwh=usage["annual_kwh"],
        rate_data=rate_data,
        self_consumption_pct=self_consumption_override,
    )

    assumptions.append(
        f"Self-consumption: {consumption['self_consumption_pct']}% "
        f"({consumption['self_consumed_kwh']:.0f} kWh used, "
        f"{consumption['exported_kwh']:.0f} kWh exported)"
    )

    # ── Step 8: Savings (3 Scenarios) ─────────────────────────
    scenarios = generate_scenarios(
        annual_usage_kwh=usage["annual_kwh"],
        rate_data=rate_data,
        system_data=system,
        base_consumption=consumption,
    )

    # ── Step 9: Battery Scenario ──────────────────────────────
    battery_scenario = None
    if include_battery_scenario:
        battery_consumption = model_consumption(
            annual_production_kwh=system["annual_production_kwh"],
            annual_usage_kwh=usage["annual_kwh"],
            rate_data=rate_data,
            self_consumption_pct=0.85,  # Battery enables ~85% self-consumption
        )
        battery_savings = calculate_savings(
            usage["annual_kwh"], rate_data, system, battery_consumption
        )
        battery_scenario = {
            **battery_savings,
            "scenario": "with_battery",
            "label": "With Battery Storage",
            "self_consumption_pct": 85.0,
            "battery_note": "Assumes 10-13 kWh battery, ~85% self-consumption",
        }
        assumptions.append("Battery scenario: assumes 85% self-consumption with storage")

    # ── Step 10: Confidence ───────────────────────────────────
    confidence = score_confidence(
        usage_confidence=usage["confidence"],
        rate_confidence=rate_data["confidence"],
        solar_confidence=solar["data_confidence"],
        utility_confidence=utility_confidence,
        has_real_bill=monthly_bill is not None and monthly_bill > 0,
        has_address=bool(address),
    )

    # ── Assemble Final Output ─────────────────────────────────
    base = scenarios["base"]

    return {
        # Summary (what reps see)
        "summary": {
            "utility": rate_data.get("utility_name", "Unknown"),
            "utility_code": utility_code,
            "annual_kwh": usage["annual_kwh"],
            "rate_per_kwh": rate_data["rate_per_kwh"],
            "system_size_kw": system["system_size_kw"],
            "panel_count": system["panel_count"],
            "annual_production_kwh": system["annual_production_kwh"],
            "self_consumption_pct": consumption["self_consumption_pct"],
            "export_pct": consumption["export_pct"],
            "monthly_bill_before": base["current_monthly_bill"],
            "monthly_bill_after": base["net_monthly_bill_with_solar"],
            "monthly_savings": base["monthly_savings"],
            "annual_savings": base["annual_savings"],
            "savings_percentage": base["savings_percentage"],
            "confidence_score": confidence["confidence_score"],
        },

        # Detailed sections
        "location": location,
        "solar_resource": solar,
        "usage": usage,
        "rate_structure": {
            "utility_name": rate_data.get("utility_name"),
            "rate_schedule": rate_data.get("rate_schedule"),
            "blended_rate": rate_data.get("blended_rate"),
            "peak_rate": rate_data.get("peak_rate"),
            "off_peak_rate": rate_data.get("off_peak_rate"),
            "super_off_peak_rate": rate_data.get("super_off_peak_rate"),
            "nem_policy": rate_data.get("nem_policy"),
            "nem_export_rate": rate_data.get("nem_export_rate_avg"),
            "fixed_monthly": rate_data.get("fixed_monthly"),
            "annual_increase_pct": rate_data.get("annual_increase_pct"),
        },
        "system": system,
        "consumption_model": consumption,
        "scenarios": scenarios,
        "battery_scenario": battery_scenario,
        "financials": {
            "system_cost_gross": base["system_cost_gross"],
            "itc_credit_30pct": base["itc_credit"],
            "net_system_cost": base["net_system_cost"],
            "monthly_payment": base["monthly_solar_payment"],
            "loan_term_years": LOAN_TERM_YEARS,
            "interest_rate": ANNUAL_INTEREST_RATE,
            "cash_payback_years": base["cash_payback_years"],
        },
        "confidence": confidence,
        "assumptions_used": assumptions,
        "missing_data": missing_data,
    }
