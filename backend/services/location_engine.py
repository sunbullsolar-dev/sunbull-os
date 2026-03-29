"""
Location + Utility Mapping Engine
=================================
Maps address → lat/lng, city, state, zip, utility provider, solar region.

Uses zip-code-based lookup tables for California utilities.
Geocoding via free Nominatim (OpenStreetMap) with fallback to zip centroid.
"""
import re
from typing import Optional, Dict, Any

# ============================================================================
# CALIFORNIA UTILITY TERRITORY MAP (by zip code prefix)
# ============================================================================
# Source: CPUC service territory maps, utility websites
# Format: zip_prefix → (utility_code, utility_name)
#
# This covers the major California IOUs and municipal utilities.
# For zips not in the map, we fall back to state-level estimation.

# SCE (Southern California Edison) - serves most of SoCal inland
# LADWP (Los Angeles Dept of Water & Power) - serves City of LA
# PGE (Pacific Gas & Electric) - serves NorCal + Central Coast
# SDGE (San Diego Gas & Electric) - serves San Diego area
# SMUD (Sacramento Municipal Utility District) - Sacramento area

# Detailed zip-to-utility mapping for California
# Key = first 3 digits of zip code
_CA_ZIP3_TO_UTILITY: Dict[str, tuple] = {
    # LADWP territory (City of Los Angeles)
    "900": ("LADWP", "Los Angeles Dept of Water & Power"),
    "901": ("LADWP", "Los Angeles Dept of Water & Power"),
    "902": ("SCE", "Southern California Edison"),  # Inglewood area - SCE
    "903": ("SCE", "Southern California Edison"),  # Inglewood
    "904": ("SCE", "Southern California Edison"),  # Santa Monica area
    "905": ("SCE", "Southern California Edison"),  # Torrance
    "906": ("SCE", "Southern California Edison"),  # Whittier/Long Beach
    "907": ("SCE", "Southern California Edison"),  # Long Beach
    "908": ("SCE", "Southern California Edison"),  # Long Beach
    "910": ("SCE", "Southern California Edison"),  # Pasadena area
    "911": ("SCE", "Southern California Edison"),  # Pasadena
    "912": ("SCE", "Southern California Edison"),  # Glendale
    "913": ("SCE", "Southern California Edison"),  # Van Nuys/North Hollywood
    "914": ("SCE", "Southern California Edison"),  # Van Nuys
    "915": ("SCE", "Southern California Edison"),  # Burbank area
    "916": ("SCE", "Southern California Edison"),  # Encino/Tarzana
    "917": ("SCE", "Southern California Edison"),  # Industry/Covina
    "918": ("SCE", "Southern California Edison"),  # Azusa/Covina
    "919": ("SCE", "Southern California Edison"),  # San Fernando Valley N
    "920": ("SDGE", "San Diego Gas & Electric"),  # San Diego
    "921": ("SDGE", "San Diego Gas & Electric"),  # San Diego
    "922": ("SDGE", "San Diego Gas & Electric"),  # Inland Empire border
    "923": ("SCE", "Southern California Edison"),  # San Bernardino
    "924": ("SCE", "Southern California Edison"),  # San Bernardino
    "925": ("SCE", "Southern California Edison"),  # Riverside
    "926": ("SCE", "Southern California Edison"),  # Santa Ana/OC
    "927": ("SCE", "Southern California Edison"),  # Santa Ana/OC
    "928": ("SCE", "Southern California Edison"),  # Anaheim/OC
    "930": ("SCE", "Southern California Edison"),  # Oxnard/Ventura
    "931": ("SCE", "Southern California Edison"),  # Santa Barbara
    "932": ("SCE", "Southern California Edison"),  # Bakersfield
    "933": ("SCE", "Southern California Edison"),  # Bakersfield
    "934": ("PGE", "Pacific Gas & Electric"),  # Santa Barbara N
    "935": ("SCE", "Southern California Edison"),  # Mojave/Lancaster
    "936": ("SCE", "Southern California Edison"),  # Palmdale/Lancaster
    "937": ("SCE", "Southern California Edison"),  # Palm Springs
    "940": ("PGE", "Pacific Gas & Electric"),  # San Francisco
    "941": ("PGE", "Pacific Gas & Electric"),  # San Francisco
    "942": ("PGE", "Pacific Gas & Electric"),  # Sacramento (PGE territory)
    "943": ("PGE", "Pacific Gas & Electric"),  # Palo Alto area
    "944": ("PGE", "Pacific Gas & Electric"),  # San Mateo
    "945": ("PGE", "Pacific Gas & Electric"),  # Oakland
    "946": ("PGE", "Pacific Gas & Electric"),  # Oakland
    "947": ("PGE", "Pacific Gas & Electric"),  # Berkeley
    "948": ("PGE", "Pacific Gas & Electric"),  # Richmond
    "949": ("PGE", "Pacific Gas & Electric"),  # San Rafael
    "950": ("PGE", "Pacific Gas & Electric"),  # San Jose
    "951": ("PGE", "Pacific Gas & Electric"),  # San Jose
    "952": ("PGE", "Pacific Gas & Electric"),  # Stockton
    "953": ("PGE", "Pacific Gas & Electric"),  # Stockton
    "954": ("PGE", "Pacific Gas & Electric"),  # Santa Rosa
    "955": ("PGE", "Pacific Gas & Electric"),  # Eureka
    "956": ("SMUD", "Sacramento Municipal Utility District"),  # Sacramento
    "957": ("SMUD", "Sacramento Municipal Utility District"),  # Sacramento
    "958": ("SMUD", "Sacramento Municipal Utility District"),  # Sacramento
    "959": ("PGE", "Pacific Gas & Electric"),  # Marysville
    "960": ("PGE", "Pacific Gas & Electric"),  # Redding
    "961": ("PGE", "Pacific Gas & Electric"),  # Reno border
    "930": ("PGE", "Pacific Gas & Electric"),  # Ventura (PGE/SCE overlap)
    "934": ("PGE", "Pacific Gas & Electric"),  # Santa Maria
    "935": ("PGE", "Pacific Gas & Electric"),  # Bakersfield N
    "936": ("PGE", "Pacific Gas & Electric"),  # Fresno
    "937": ("PGE", "Pacific Gas & Electric"),  # Fresno
    "939": ("PGE", "Pacific Gas & Electric"),  # Salinas
    "910": ("SCE", "Southern California Edison"),  # Pasadena
    # Granada Hills / Northridge area (91344, 91325, etc.) - SCE territory
    "913": ("SCE", "Southern California Edison"),
}

# More specific 5-digit zip overrides (takes priority over 3-digit)
_CA_ZIP5_TO_UTILITY: Dict[str, tuple] = {
    # LADWP territory overrides (City of LA proper)
    "90001": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90002": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90003": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90004": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90005": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90006": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90007": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90008": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90009": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90010": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90011": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90012": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90013": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90014": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90015": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90016": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90017": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90018": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90019": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90020": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90023": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90024": ("LADWP", "Los Angeles Dept of Water & Power"),  # Westwood
    "90025": ("LADWP", "Los Angeles Dept of Water & Power"),  # West LA
    "90026": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90027": ("LADWP", "Los Angeles Dept of Water & Power"),  # Los Feliz
    "90028": ("LADWP", "Los Angeles Dept of Water & Power"),  # Hollywood
    "90029": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90031": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90032": ("LADWP", "Los Angeles Dept of Water & Power"),  # El Sereno
    "90033": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90034": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90035": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90036": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90037": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90038": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90039": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90041": ("LADWP", "Los Angeles Dept of Water & Power"),  # Eagle Rock
    "90042": ("LADWP", "Los Angeles Dept of Water & Power"),  # Highland Park
    "90043": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90044": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90045": ("LADWP", "Los Angeles Dept of Water & Power"),  # Westchester
    "90046": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90047": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90048": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90049": ("LADWP", "Los Angeles Dept of Water & Power"),  # Brentwood
    "90056": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90057": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90058": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90059": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90061": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90062": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90063": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90064": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90065": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90066": ("LADWP", "Los Angeles Dept of Water & Power"),  # Mar Vista
    "90067": ("LADWP", "Los Angeles Dept of Water & Power"),  # Century City
    "90068": ("LADWP", "Los Angeles Dept of Water & Power"),  # Hollywood Hills
    "90069": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90071": ("LADWP", "Los Angeles Dept of Water & Power"),  # DTLA
    "90077": ("LADWP", "Los Angeles Dept of Water & Power"),  # Bel Air
    "90094": ("LADWP", "Los Angeles Dept of Water & Power"),
    "90095": ("LADWP", "Los Angeles Dept of Water & Power"),  # UCLA
    "90210": ("SCE", "Southern California Edison"),  # Beverly Hills - own utility but small
    "90291": ("LADWP", "Los Angeles Dept of Water & Power"),  # Venice
    "90292": ("LADWP", "Los Angeles Dept of Water & Power"),  # Marina del Rey
    "90293": ("LADWP", "Los Angeles Dept of Water & Power"),  # Playa del Rey
    "90402": ("LADWP", "Los Angeles Dept of Water & Power"),  # Santa Monica (partial)
    # San Fernando Valley - mostly LADWP for City of LA portions
    "91302": ("LADWP", "Los Angeles Dept of Water & Power"),  # Calabasas (partial)
    "91303": ("LADWP", "Los Angeles Dept of Water & Power"),  # Canoga Park
    "91304": ("LADWP", "Los Angeles Dept of Water & Power"),  # Canoga Park
    "91306": ("LADWP", "Los Angeles Dept of Water & Power"),  # Winnetka
    "91307": ("LADWP", "Los Angeles Dept of Water & Power"),  # West Hills
    "91311": ("LADWP", "Los Angeles Dept of Water & Power"),  # Chatsworth
    "91316": ("LADWP", "Los Angeles Dept of Water & Power"),  # Encino
    "91324": ("LADWP", "Los Angeles Dept of Water & Power"),  # Northridge
    "91325": ("LADWP", "Los Angeles Dept of Water & Power"),  # Northridge
    "91326": ("LADWP", "Los Angeles Dept of Water & Power"),  # Porter Ranch
    "91330": ("LADWP", "Los Angeles Dept of Water & Power"),  # CSUN
    "91331": ("LADWP", "Los Angeles Dept of Water & Power"),  # Pacoima
    "91335": ("LADWP", "Los Angeles Dept of Water & Power"),  # Reseda
    "91340": ("LADWP", "Los Angeles Dept of Water & Power"),  # San Fernando
    "91342": ("LADWP", "Los Angeles Dept of Water & Power"),  # Sylmar
    "91343": ("LADWP", "Los Angeles Dept of Water & Power"),  # North Hills
    "91344": ("LADWP", "Los Angeles Dept of Water & Power"),  # Granada Hills
    "91345": ("LADWP", "Los Angeles Dept of Water & Power"),  # Mission Hills
    "91352": ("LADWP", "Los Angeles Dept of Water & Power"),  # Sun Valley
    "91356": ("LADWP", "Los Angeles Dept of Water & Power"),  # Tarzana
    "91364": ("LADWP", "Los Angeles Dept of Water & Power"),  # Woodland Hills
    "91367": ("LADWP", "Los Angeles Dept of Water & Power"),  # Woodland Hills
    "91401": ("LADWP", "Los Angeles Dept of Water & Power"),  # Van Nuys
    "91402": ("LADWP", "Los Angeles Dept of Water & Power"),  # Panorama City
    "91403": ("LADWP", "Los Angeles Dept of Water & Power"),  # Sherman Oaks
    "91405": ("LADWP", "Los Angeles Dept of Water & Power"),  # Van Nuys
    "91406": ("LADWP", "Los Angeles Dept of Water & Power"),  # Van Nuys
    "91411": ("LADWP", "Los Angeles Dept of Water & Power"),  # Sherman Oaks
    "91423": ("LADWP", "Los Angeles Dept of Water & Power"),  # Sherman Oaks
    "91436": ("LADWP", "Los Angeles Dept of Water & Power"),  # Encino
    "91501": ("SCE", "Southern California Edison"),  # Burbank - own utility
    "91502": ("SCE", "Southern California Edison"),  # Burbank
    "91504": ("SCE", "Southern California Edison"),  # Burbank
    "91505": ("SCE", "Southern California Edison"),  # Burbank
    "91601": ("LADWP", "Los Angeles Dept of Water & Power"),  # North Hollywood
    "91602": ("LADWP", "Los Angeles Dept of Water & Power"),  # North Hollywood
    "91604": ("LADWP", "Los Angeles Dept of Water & Power"),  # Studio City
    "91605": ("LADWP", "Los Angeles Dept of Water & Power"),  # North Hollywood
    "91606": ("LADWP", "Los Angeles Dept of Water & Power"),  # North Hollywood
    "91607": ("LADWP", "Los Angeles Dept of Water & Power"),  # Valley Village
}

# State-level utility defaults (for states outside CA)
_STATE_DEFAULT_UTILITY: Dict[str, tuple] = {
    "AZ": ("APS", "Arizona Public Service"),
    "NV": ("NVE", "NV Energy"),
    "TX": ("ERCOT", "ERCOT (varies by REP)"),
    "FL": ("FPL", "Florida Power & Light"),
    "NY": ("CONED", "Consolidated Edison"),
    "NJ": ("PSEG", "PSE&G"),
}

# Solar region classification
_SOLAR_REGIONS: Dict[str, str] = {
    "CA": "high_solar",
    "AZ": "very_high_solar",
    "NV": "very_high_solar",
    "TX": "high_solar",
    "FL": "high_solar",
    "CO": "high_solar",
    "NM": "very_high_solar",
    "UT": "high_solar",
    "HI": "very_high_solar",
    "GA": "moderate_solar",
    "SC": "moderate_solar",
    "NC": "moderate_solar",
    "AL": "moderate_solar",
    "NY": "low_solar",
    "NJ": "moderate_solar",
    "PA": "low_solar",
    "OH": "low_solar",
    "WA": "low_solar",
    "OR": "low_solar",
}

# Approximate zip-code centroids for major CA areas (lat, lng)
_ZIP3_CENTROIDS: Dict[str, tuple] = {
    "900": (33.94, -118.25), "901": (33.94, -118.25),
    "902": (33.96, -118.35), "903": (33.95, -118.39),
    "904": (34.02, -118.49), "905": (33.84, -118.35),
    "906": (33.98, -118.03), "907": (33.77, -118.19),
    "908": (33.79, -118.14), "910": (34.15, -118.14),
    "911": (34.15, -118.14), "912": (34.14, -118.25),
    "913": (34.19, -118.38), "914": (34.19, -118.45),
    "915": (34.18, -118.31), "916": (34.16, -118.50),
    "917": (34.00, -117.91), "918": (34.14, -117.88),
    "919": (34.28, -118.43), "920": (32.72, -117.16),
    "921": (32.82, -117.15), "922": (33.13, -117.08),
    "923": (34.11, -117.29), "924": (34.11, -117.29),
    "925": (33.95, -117.40), "926": (33.75, -117.87),
    "927": (33.75, -117.87), "928": (33.84, -117.91),
    "930": (34.20, -119.18), "931": (34.42, -119.70),
    "932": (35.37, -119.02), "933": (35.37, -119.02),
    "934": (34.95, -120.44), "935": (34.74, -118.18),
    "936": (36.74, -119.77), "937": (36.74, -119.77),
    "939": (36.67, -121.66), "940": (37.77, -122.42),
    "941": (37.77, -122.42), "942": (38.58, -121.49),
    "943": (37.44, -122.14), "944": (37.56, -122.31),
    "945": (37.80, -122.27), "946": (37.80, -122.27),
    "947": (37.87, -122.27), "948": (37.94, -122.35),
    "949": (37.97, -122.53), "950": (37.34, -121.89),
    "951": (37.34, -121.89), "952": (37.95, -121.29),
    "953": (37.95, -121.29), "954": (38.44, -122.71),
    "955": (40.80, -124.16), "956": (38.58, -121.49),
    "957": (38.58, -121.49), "958": (38.58, -121.49),
    "959": (39.14, -121.59), "960": (40.59, -122.39),
}


def lookup_utility(zip_code: str, state: str = "CA") -> Dict[str, Any]:
    """
    Look up utility provider from zip code.

    Returns:
        {
            "utility_code": str,
            "utility_name": str,
            "source": "zip5" | "zip3" | "state_default" | "unknown",
            "confidence": "HIGH" | "MED" | "LOW"
        }
    """
    zip_clean = (zip_code or "").strip().replace("-", "")[:5]

    # Try 5-digit exact match first
    if zip_clean in _CA_ZIP5_TO_UTILITY:
        code, name = _CA_ZIP5_TO_UTILITY[zip_clean]
        return {
            "utility_code": code,
            "utility_name": name,
            "source": "zip5",
            "confidence": "HIGH",
        }

    # Try 3-digit prefix
    zip3 = zip_clean[:3]
    if zip3 in _CA_ZIP3_TO_UTILITY:
        code, name = _CA_ZIP3_TO_UTILITY[zip3]
        return {
            "utility_code": code,
            "utility_name": name,
            "source": "zip3",
            "confidence": "MED",
        }

    # State-level default
    state_upper = (state or "").upper()
    if state_upper in _STATE_DEFAULT_UTILITY:
        code, name = _STATE_DEFAULT_UTILITY[state_upper]
        return {
            "utility_code": code,
            "utility_name": name,
            "source": "state_default",
            "confidence": "LOW",
        }

    # Unknown
    return {
        "utility_code": "UNKNOWN",
        "utility_name": "Unknown Utility",
        "source": "unknown",
        "confidence": "LOW",
    }


def get_solar_region(state: str) -> str:
    """Classify solar region from state."""
    return _SOLAR_REGIONS.get((state or "").upper(), "moderate_solar")


def estimate_coordinates(zip_code: str) -> Dict[str, Optional[float]]:
    """
    Estimate lat/lng from zip code prefix.
    Returns approximate centroid for the zip3 area.
    """
    zip_clean = (zip_code or "").strip().replace("-", "")[:5]
    zip3 = zip_clean[:3]

    if zip3 in _ZIP3_CENTROIDS:
        lat, lng = _ZIP3_CENTROIDS[zip3]
        return {"latitude": lat, "longitude": lng, "source": "zip3_centroid"}

    return {"latitude": None, "longitude": None, "source": "unknown"}


def resolve_location(
    address: str,
    city: str,
    state: str,
    zip_code: str,
) -> Dict[str, Any]:
    """
    Full location resolution: coordinates, utility, solar region.

    Returns a complete location profile for a lead.
    """
    coords = estimate_coordinates(zip_code)
    utility = lookup_utility(zip_code, state)
    region = get_solar_region(state)

    return {
        "latitude": coords["latitude"],
        "longitude": coords["longitude"],
        "coordinate_source": coords["source"],
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "utility_code": utility["utility_code"],
        "utility_name": utility["utility_name"],
        "utility_source": utility["source"],
        "utility_confidence": utility["confidence"],
        "solar_region": region,
    }
