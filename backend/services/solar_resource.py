"""
Solar Resource Engine
=====================
Provides location-based solar irradiance and production data.

Uses NREL PVWatts-derived data for California regions.
Peak sun hours (PSH) = daily average kWh/m²/day of solar irradiance
Production factor = annual kWh produced per kW of installed capacity

Data sources:
- NREL Solar Resource Data (TMY3)
- PVWatts Calculator reference values
- California Energy Commission solar maps
"""
from typing import Dict, Any, Optional

# ============================================================================
# PEAK SUN HOURS BY REGION
# ============================================================================
# PSH = kWh/m²/day (also called "equivalent sun hours")
# These represent annual averages for a south-facing, ~20° tilt system.
# Source: NREL TMY3 / PVWatts reference data

# By latitude band within California
_CA_PSH_BY_LAT: list = [
    # (min_lat, max_lat, psh_annual_avg, psh_summer, psh_winter, region_name)
    (32.0, 33.0, 5.7, 7.2, 4.0, "Imperial/San Diego South"),
    (33.0, 33.5, 5.6, 7.1, 3.9, "San Diego / Orange County"),
    (33.5, 34.0, 5.5, 7.0, 3.8, "LA Basin / Inland Empire"),
    (34.0, 34.5, 5.4, 6.9, 3.7, "San Fernando Valley / Pasadena"),
    (34.5, 35.0, 5.6, 7.2, 3.8, "High Desert / Lancaster"),
    (35.0, 36.0, 5.5, 7.1, 3.7, "Bakersfield / Central Valley South"),
    (36.0, 37.0, 5.3, 6.8, 3.6, "Fresno / Central Valley"),
    (37.0, 37.5, 5.1, 6.6, 3.4, "San Jose / Bay Area South"),
    (37.5, 38.0, 5.0, 6.5, 3.3, "San Francisco / East Bay"),
    (38.0, 39.0, 5.2, 6.7, 3.5, "Sacramento / Central Valley North"),
    (39.0, 40.0, 5.0, 6.6, 3.3, "Chico / Red Bluff"),
    (40.0, 42.0, 4.7, 6.3, 3.0, "Redding / Far North"),
]

# By utility territory (weighted average for territory)
_PSH_BY_UTILITY: Dict[str, Dict[str, float]] = {
    "SCE": {
        "annual_avg": 5.5,
        "summer_avg": 7.0,
        "winter_avg": 3.8,
        "production_factor": 1650,  # kWh/kW/year
    },
    "LADWP": {
        "annual_avg": 5.4,
        "summer_avg": 6.9,
        "winter_avg": 3.7,
        "production_factor": 1620,  # kWh/kW/year
    },
    "PGE": {
        "annual_avg": 5.1,
        "summer_avg": 6.6,
        "winter_avg": 3.4,
        "production_factor": 1530,  # kWh/kW/year
    },
    "SDGE": {
        "annual_avg": 5.6,
        "summer_avg": 7.1,
        "winter_avg": 3.9,
        "production_factor": 1680,  # kWh/kW/year
    },
    "SMUD": {
        "annual_avg": 5.2,
        "summer_avg": 6.7,
        "winter_avg": 3.5,
        "production_factor": 1560,  # kWh/kW/year
    },
}

# State-level defaults for outside CA
_PSH_BY_STATE: Dict[str, Dict[str, float]] = {
    "AZ": {"annual_avg": 6.5, "summer_avg": 7.8, "winter_avg": 5.0, "production_factor": 1850},
    "NV": {"annual_avg": 6.2, "summer_avg": 7.5, "winter_avg": 4.7, "production_factor": 1780},
    "NM": {"annual_avg": 6.4, "summer_avg": 7.7, "winter_avg": 4.9, "production_factor": 1830},
    "TX": {"annual_avg": 5.2, "summer_avg": 6.4, "winter_avg": 3.8, "production_factor": 1520},
    "FL": {"annual_avg": 5.3, "summer_avg": 6.0, "winter_avg": 4.4, "production_factor": 1480},
    "CO": {"annual_avg": 5.5, "summer_avg": 6.8, "winter_avg": 4.0, "production_factor": 1590},
    "UT": {"annual_avg": 5.5, "summer_avg": 7.0, "winter_avg": 3.8, "production_factor": 1600},
    "HI": {"annual_avg": 5.8, "summer_avg": 6.5, "winter_avg": 5.0, "production_factor": 1680},
    "GA": {"annual_avg": 4.7, "summer_avg": 5.8, "winter_avg": 3.4, "production_factor": 1370},
    "SC": {"annual_avg": 4.8, "summer_avg": 5.9, "winter_avg": 3.5, "production_factor": 1390},
    "NC": {"annual_avg": 4.7, "summer_avg": 5.8, "winter_avg": 3.4, "production_factor": 1360},
    "NY": {"annual_avg": 3.8, "summer_avg": 5.0, "winter_avg": 2.5, "production_factor": 1150},
    "NJ": {"annual_avg": 4.2, "summer_avg": 5.3, "winter_avg": 2.8, "production_factor": 1230},
    "PA": {"annual_avg": 3.9, "summer_avg": 5.1, "winter_avg": 2.6, "production_factor": 1170},
    "OH": {"annual_avg": 3.7, "summer_avg": 4.9, "winter_avg": 2.4, "production_factor": 1110},
    "WA": {"annual_avg": 3.5, "summer_avg": 5.5, "winter_avg": 1.8, "production_factor": 1050},
    "OR": {"annual_avg": 3.7, "summer_avg": 5.4, "winter_avg": 2.0, "production_factor": 1100},
}

# National fallback
_NATIONAL_DEFAULT = {
    "annual_avg": 4.5,
    "summer_avg": 5.8,
    "winter_avg": 3.2,
    "production_factor": 1350,
}


def get_solar_resource(
    latitude: Optional[float] = None,
    utility_code: Optional[str] = None,
    state: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get solar resource data for a location.

    Priority:
    1. Latitude-based lookup (most accurate for CA)
    2. Utility territory average
    3. State-level default
    4. National fallback

    Returns:
        {
            "sun_hours_per_day": float,
            "summer_sun_hours": float,
            "winter_sun_hours": float,
            "production_factor_kwh_per_kw": float,  # annual kWh per kW installed
            "data_source": str,
            "data_confidence": "HIGH" | "MED" | "LOW"
        }
    """
    assumptions = []

    # 1. Try latitude-based (CA only, most precise)
    if latitude and state and state.upper() == "CA":
        for min_lat, max_lat, psh, summer, winter, region in _CA_PSH_BY_LAT:
            if min_lat <= latitude < max_lat:
                # Calculate production factor from PSH
                # PF = PSH * 365 * system_performance_ratio
                # Typical PR = 0.82 (accounts for inverter, wiring, temp losses)
                pf = psh * 365 * 0.82
                return {
                    "sun_hours_per_day": psh,
                    "summer_sun_hours": summer,
                    "winter_sun_hours": winter,
                    "production_factor_kwh_per_kw": round(pf, 0),
                    "annual_irradiance_kwh_m2": round(psh * 365, 0),
                    "region_name": region,
                    "data_source": "latitude_lookup",
                    "data_confidence": "HIGH",
                }

    # 2. Try utility territory average
    if utility_code and utility_code in _PSH_BY_UTILITY:
        data = _PSH_BY_UTILITY[utility_code]
        return {
            "sun_hours_per_day": data["annual_avg"],
            "summer_sun_hours": data["summer_avg"],
            "winter_sun_hours": data["winter_avg"],
            "production_factor_kwh_per_kw": data["production_factor"],
            "annual_irradiance_kwh_m2": round(data["annual_avg"] * 365, 0),
            "region_name": f"{utility_code} territory",
            "data_source": "utility_territory",
            "data_confidence": "MED",
        }

    # 3. Try state-level
    state_upper = (state or "").upper()
    if state_upper in _PSH_BY_STATE:
        data = _PSH_BY_STATE[state_upper]
        return {
            "sun_hours_per_day": data["annual_avg"],
            "summer_sun_hours": data["summer_avg"],
            "winter_sun_hours": data["winter_avg"],
            "production_factor_kwh_per_kw": data["production_factor"],
            "annual_irradiance_kwh_m2": round(data["annual_avg"] * 365, 0),
            "region_name": f"{state_upper} state average",
            "data_source": "state_default",
            "data_confidence": "LOW",
        }

    # 4. National fallback
    return {
        "sun_hours_per_day": _NATIONAL_DEFAULT["annual_avg"],
        "summer_sun_hours": _NATIONAL_DEFAULT["summer_avg"],
        "winter_sun_hours": _NATIONAL_DEFAULT["winter_avg"],
        "production_factor_kwh_per_kw": _NATIONAL_DEFAULT["production_factor"],
        "annual_irradiance_kwh_m2": round(_NATIONAL_DEFAULT["annual_avg"] * 365, 0),
        "region_name": "National average",
        "data_source": "national_fallback",
        "data_confidence": "LOW",
    }
