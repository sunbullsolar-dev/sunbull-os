"""
Utility Rate Engine
===================
Real utility rates by provider with TOU pricing and NEM 3.0 export values.

Includes:
- Peak and off-peak TOU rates
- NEM 3.0 export compensation
- Fixed monthly charges
- Rate escalation assumptions

Sources: CPUC rate filings, utility published tariff schedules (2024-2025)
"""
from typing import Dict, Any, Optional

# ============================================================================
# CALIFORNIA UTILITY RATES WITH TOU (2024-2025)
# ============================================================================
# TOU = Time of Use pricing
# Peak: typically 4-9 PM (when solar isn't producing much)
# Off-peak: all other times (when solar IS producing)
# Super off-peak: midday in some schedules (when solar is max)
#
# For solar customers, the TOU structure matters because:
# - Solar produces during off-peak/super-off-peak hours
# - Homeowner uses most electricity during peak hours (evening)
# - NEM 3.0 exports get LOW compensation (~$0.05-0.08/kWh)

_UTILITY_RATES: Dict[str, Dict[str, Any]] = {
    "SCE": {
        "name": "Southern California Edison",
        "state": "CA",
        "rate_schedule": "TOU-D-PRIME",
        # TOU rates (all-in $/kWh)
        "peak_rate": 0.47,           # 4-9 PM weekdays
        "off_peak_rate": 0.33,       # All other times
        "super_off_peak_rate": 0.25, # Midday (when solar produces)
        "blended_rate": 0.36,        # Weighted average
        # TOU time splits (% of annual usage)
        "peak_pct": 0.35,            # 35% of usage during peak
        "off_peak_pct": 0.40,        # 40% during off-peak
        "super_off_peak_pct": 0.25,  # 25% during super off-peak
        # Solar production timing
        "solar_during_peak_pct": 0.15,       # Only 15% of solar output during peak
        "solar_during_off_peak_pct": 0.45,   # 45% during off-peak
        "solar_during_super_off_peak_pct": 0.40,  # 40% during super off-peak (midday)
        # NEM 3.0
        "net_metering": "NEM-3",
        "nem_export_rate_avg": 0.08,  # Average export compensation
        "nem_export_peak": 0.12,      # Export during peak hours
        "nem_export_off_peak": 0.05,  # Export during off-peak
        # Fixed charges
        "fixed_charges_monthly": 15.00,
        "baseline_allowance_kwh": 350,
        # Escalation
        "annual_increase_pct": 5.0,
        "last_updated": "2025-01",
    },
    "LADWP": {
        "name": "Los Angeles Dept of Water & Power",
        "state": "CA",
        "rate_schedule": "R-1A Tiered",
        # LADWP uses TIERED rates, not full TOU
        "peak_rate": 0.28,           # High tier
        "off_peak_rate": 0.22,       # Low tier / baseline
        "super_off_peak_rate": 0.20, # Baseline
        "blended_rate": 0.24,
        "peak_pct": 0.30,
        "off_peak_pct": 0.45,
        "super_off_peak_pct": 0.25,
        "solar_during_peak_pct": 0.15,
        "solar_during_off_peak_pct": 0.45,
        "solar_during_super_off_peak_pct": 0.40,
        # LADWP still has NEM 1.0 (favorable 1:1 credits)
        "net_metering": "NEM-1",
        "nem_export_rate_avg": 0.20,  # Near-retail credit
        "nem_export_peak": 0.24,
        "nem_export_off_peak": 0.18,
        "fixed_charges_monthly": 12.00,
        "baseline_allowance_kwh": 400,
        "annual_increase_pct": 3.0,
        "last_updated": "2025-01",
    },
    "PGE": {
        "name": "Pacific Gas & Electric",
        "state": "CA",
        "rate_schedule": "E-TOU-C",
        "peak_rate": 0.52,           # 4-9 PM — highest in CA
        "off_peak_rate": 0.38,
        "super_off_peak_rate": 0.28,
        "blended_rate": 0.40,
        "peak_pct": 0.35,
        "off_peak_pct": 0.40,
        "super_off_peak_pct": 0.25,
        "solar_during_peak_pct": 0.15,
        "solar_during_off_peak_pct": 0.45,
        "solar_during_super_off_peak_pct": 0.40,
        "net_metering": "NEM-3",
        "nem_export_rate_avg": 0.07,
        "nem_export_peak": 0.10,
        "nem_export_off_peak": 0.04,
        "fixed_charges_monthly": 15.50,
        "baseline_allowance_kwh": 300,
        "annual_increase_pct": 7.0,
        "last_updated": "2025-01",
    },
    "SDGE": {
        "name": "San Diego Gas & Electric",
        "state": "CA",
        "rate_schedule": "TOU-DR1",
        "peak_rate": 0.55,
        "off_peak_rate": 0.40,
        "super_off_peak_rate": 0.30,
        "blended_rate": 0.42,
        "peak_pct": 0.35,
        "off_peak_pct": 0.40,
        "super_off_peak_pct": 0.25,
        "solar_during_peak_pct": 0.15,
        "solar_during_off_peak_pct": 0.45,
        "solar_during_super_off_peak_pct": 0.40,
        "net_metering": "NEM-3",
        "nem_export_rate_avg": 0.06,
        "nem_export_peak": 0.09,
        "nem_export_off_peak": 0.04,
        "fixed_charges_monthly": 16.00,
        "baseline_allowance_kwh": 280,
        "annual_increase_pct": 6.0,
        "last_updated": "2025-01",
    },
    "SMUD": {
        "name": "Sacramento Municipal Utility District",
        "state": "CA",
        "rate_schedule": "R-TOU",
        "peak_rate": 0.22,
        "off_peak_rate": 0.14,
        "super_off_peak_rate": 0.12,
        "blended_rate": 0.16,
        "peak_pct": 0.30,
        "off_peak_pct": 0.45,
        "super_off_peak_pct": 0.25,
        "solar_during_peak_pct": 0.15,
        "solar_during_off_peak_pct": 0.45,
        "solar_during_super_off_peak_pct": 0.40,
        "net_metering": "NEM-1",
        "nem_export_rate_avg": 0.10,
        "nem_export_peak": 0.14,
        "nem_export_off_peak": 0.08,
        "fixed_charges_monthly": 23.00,
        "baseline_allowance_kwh": 500,
        "annual_increase_pct": 3.0,
        "last_updated": "2025-01",
    },
}

# State-level fallback rates
_STATE_RATES: Dict[str, Dict[str, Any]] = {
    "AZ": {"blended_rate": 0.13, "peak_rate": 0.18, "off_peak_rate": 0.10, "nem_export_rate_avg": 0.04, "annual_increase_pct": 3.0},
    "NV": {"blended_rate": 0.12, "peak_rate": 0.16, "off_peak_rate": 0.10, "nem_export_rate_avg": 0.04, "annual_increase_pct": 3.0},
    "TX": {"blended_rate": 0.13, "peak_rate": 0.18, "off_peak_rate": 0.10, "nem_export_rate_avg": 0.04, "annual_increase_pct": 4.0},
    "FL": {"blended_rate": 0.14, "peak_rate": 0.18, "off_peak_rate": 0.11, "nem_export_rate_avg": 0.05, "annual_increase_pct": 3.5},
    "NY": {"blended_rate": 0.22, "peak_rate": 0.30, "off_peak_rate": 0.18, "nem_export_rate_avg": 0.10, "annual_increase_pct": 4.0},
    "NJ": {"blended_rate": 0.18, "peak_rate": 0.24, "off_peak_rate": 0.14, "nem_export_rate_avg": 0.08, "annual_increase_pct": 3.5},
}

_NATIONAL_RATE = {"blended_rate": 0.16, "peak_rate": 0.22, "off_peak_rate": 0.12, "nem_export_rate_avg": 0.05, "annual_increase_pct": 3.5}


def get_utility_rate(
    utility_code: Optional[str] = None,
    state: Optional[str] = None,
    manual_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Get full utility rate structure including TOU and NEM.

    Returns complete rate profile for savings calculations.
    """
    # 1. Utility-specific (best)
    if utility_code and utility_code in _UTILITY_RATES:
        u = _UTILITY_RATES[utility_code]
        result = {
            "rate_per_kwh": manual_rate if manual_rate and manual_rate > 0 else u["blended_rate"],
            "peak_rate": u["peak_rate"],
            "off_peak_rate": u["off_peak_rate"],
            "super_off_peak_rate": u.get("super_off_peak_rate", u["off_peak_rate"]),
            "blended_rate": u["blended_rate"],
            "rate_source": "manual" if manual_rate else "utility_table",
            "utility_name": u["name"],
            "rate_schedule": u["rate_schedule"],
            # TOU time splits
            "peak_usage_pct": u["peak_pct"],
            "off_peak_usage_pct": u["off_peak_pct"],
            "super_off_peak_usage_pct": u.get("super_off_peak_pct", 0),
            # Solar production timing
            "solar_during_peak_pct": u["solar_during_peak_pct"],
            "solar_during_off_peak_pct": u["solar_during_off_peak_pct"],
            "solar_during_super_off_peak_pct": u.get("solar_during_super_off_peak_pct", 0),
            # NEM
            "nem_policy": u["net_metering"],
            "nem_export_rate_avg": u["nem_export_rate_avg"],
            "nem_export_peak": u.get("nem_export_peak", u["nem_export_rate_avg"]),
            "nem_export_off_peak": u.get("nem_export_off_peak", u["nem_export_rate_avg"] * 0.6),
            # Fixed + escalation
            "fixed_monthly": u["fixed_charges_monthly"],
            "baseline_allowance_kwh": u.get("baseline_allowance_kwh", 350),
            "annual_increase_pct": u["annual_increase_pct"],
            "confidence": "HIGH",
        }
        return result

    # 2. State-level
    state_upper = (state or "").upper()
    if state_upper in _STATE_RATES:
        s = _STATE_RATES[state_upper]
        return {
            "rate_per_kwh": manual_rate if manual_rate and manual_rate > 0 else s["blended_rate"],
            "peak_rate": s["peak_rate"],
            "off_peak_rate": s["off_peak_rate"],
            "super_off_peak_rate": s["off_peak_rate"] * 0.85,
            "blended_rate": s["blended_rate"],
            "rate_source": "manual" if manual_rate else "state_avg",
            "utility_name": "State Average",
            "rate_schedule": "estimated",
            "peak_usage_pct": 0.35,
            "off_peak_usage_pct": 0.45,
            "super_off_peak_usage_pct": 0.20,
            "solar_during_peak_pct": 0.15,
            "solar_during_off_peak_pct": 0.45,
            "solar_during_super_off_peak_pct": 0.40,
            "nem_policy": "varies",
            "nem_export_rate_avg": s["nem_export_rate_avg"],
            "nem_export_peak": s["nem_export_rate_avg"] * 1.5,
            "nem_export_off_peak": s["nem_export_rate_avg"] * 0.7,
            "fixed_monthly": 15.0,
            "baseline_allowance_kwh": 350,
            "annual_increase_pct": s["annual_increase_pct"],
            "confidence": "MED",
        }

    # 3. National fallback
    n = _NATIONAL_RATE
    return {
        "rate_per_kwh": manual_rate if manual_rate and manual_rate > 0 else n["blended_rate"],
        "peak_rate": n["peak_rate"],
        "off_peak_rate": n["off_peak_rate"],
        "super_off_peak_rate": n["off_peak_rate"] * 0.85,
        "blended_rate": n["blended_rate"],
        "rate_source": "manual" if manual_rate else "national_avg",
        "utility_name": "National Average",
        "rate_schedule": "estimated",
        "peak_usage_pct": 0.35,
        "off_peak_usage_pct": 0.45,
        "super_off_peak_usage_pct": 0.20,
        "solar_during_peak_pct": 0.15,
        "solar_during_off_peak_pct": 0.45,
        "solar_during_super_off_peak_pct": 0.40,
        "nem_policy": "varies",
        "nem_export_rate_avg": n["nem_export_rate_avg"],
        "nem_export_peak": n["nem_export_rate_avg"] * 1.5,
        "nem_export_off_peak": n["nem_export_rate_avg"] * 0.7,
        "fixed_monthly": 15.0,
        "baseline_allowance_kwh": 350,
        "annual_increase_pct": n["annual_increase_pct"],
        "confidence": "LOW",
    }


def estimate_rate_from_bill(monthly_bill: float, monthly_kwh: float) -> float:
    """Calculate implied rate from bill and usage."""
    if monthly_kwh > 0:
        return round(monthly_bill / monthly_kwh, 4)
    return 0.0
