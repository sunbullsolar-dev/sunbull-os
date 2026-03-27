"""Lead scoring service for calculating lead quality scores."""
from typing import Optional

# Configurable list of good locations
GOOD_LOCATIONS = {
    "CA": ["Los Angeles", "San Diego", "San Francisco", "Sacramento"],
    "TX": ["Austin", "Houston", "Dallas", "San Antonio"],
    "AZ": ["Phoenix", "Mesa", "Scottsdale"],
    "FL": ["Miami", "Orlando", "Tampa"],
}


def calculate_lead_score(
    average_monthly_bill: Optional[float] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    confirmation_strength: int = 0,
    # Legacy alias support
    bill_amount: Optional[float] = None,
) -> int:
    """
    Calculate lead quality score from 0-100.

    Scoring breakdown:
    - Base: 20 points
    - Bill > $300: +30 points
    - Bill > $200: +20 points
    - Bill > $150: +10 points
    - Confirmed: +25 points
    - Good location: +15 points

    Args:
        average_monthly_bill: Monthly bill amount (canonical name)
        city: City name
        state: State code (2 letter)
        confirmation_strength: 0 (unconfirmed) or 1 (confirmed)
        bill_amount: Legacy alias for average_monthly_bill

    Returns:
        Score from 0-100
    """
    score = 20  # Base score

    # Resolve canonical parameter (support legacy alias)
    resolved_bill = average_monthly_bill or bill_amount

    # Bill amount scoring (mutually exclusive - take highest)
    if resolved_bill:
        if resolved_bill > 300:
            score += 30
        elif resolved_bill > 200:
            score += 20
        elif resolved_bill > 150:
            score += 10

    # Confirmation scoring
    if confirmation_strength == 1:
        score += 25

    # Location scoring
    if city and state:
        if state in GOOD_LOCATIONS:
            if city in GOOD_LOCATIONS[state]:
                score += 15

    # Cap at 100
    return min(score, 100)
