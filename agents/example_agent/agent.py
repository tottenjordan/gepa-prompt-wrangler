"""Example travel agent with mock function tools for wrangler evaluation."""

from google.adk.agents import Agent

# ---------------------------------------------------------------------------
# Mock function tools (return synthetic data for eval purposes)
# ---------------------------------------------------------------------------


def search_flights(origin: str, destination: str, date: str) -> dict:
    """Search for available flights between two airports on a given date.

    Args:
        origin: IATA code of departure airport (e.g. "SFO").
        destination: IATA code of arrival airport (e.g. "JFK").
        date: Travel date in YYYY-MM-DD format.
    """
    return {
        "flights": [
            {
                "flight": "UA-204",
                "departure": f"{date}T08:00",
                "arrival": f"{date}T16:30",
                "price_usd": 389.00,
                "duration_hours": 5.5,
            },
            {
                "flight": "DL-512",
                "departure": f"{date}T10:15",
                "arrival": f"{date}T18:45",
                "price_usd": 425.00,
                "duration_hours": 5.5,
            },
            {
                "flight": "AA-100",
                "departure": f"{date}T14:00",
                "arrival": f"{date}T22:20",
                "price_usd": 352.00,
                "duration_hours": 5.33,
            },
        ],
        "origin": origin,
        "destination": destination,
        "date": date,
    }


def search_hotels(location: str, check_in: str, check_out: str) -> dict:
    """Search for hotels near a location for given dates.

    Args:
        location: City or landmark to search near (e.g. "Times Square, New York").
        check_in: Check-in date in YYYY-MM-DD format.
        check_out: Check-out date in YYYY-MM-DD format.
    """
    return {
        "hotels": [
            {
                "name": "Marriott Marquis",
                "address": "1535 Broadway, New York",
                "price_per_night_usd": 299.00,
                "rating": 4.5,
            },
            {
                "name": "Hilton Times Square",
                "address": "234 W 42nd St, New York",
                "price_per_night_usd": 275.00,
                "rating": 4.3,
            },
            {
                "name": "Holiday Inn Express",
                "address": "343 W 39th St, New York",
                "price_per_night_usd": 189.00,
                "rating": 4.0,
            },
        ],
        "location": location,
        "check_in": check_in,
        "check_out": check_out,
    }


def check_policy(item_type: str, amount_usd: float) -> dict:
    """Check whether a travel expense complies with corporate policy.

    Args:
        item_type: Type of expense — "flight", "hotel", or "meal".
        amount_usd: Dollar amount to validate.
    """
    limits = {"flight": 500.00, "hotel": 350.00, "meal": 75.00}
    limit = limits.get(item_type, 0)
    compliant = amount_usd <= limit
    return {
        "item_type": item_type,
        "amount_usd": amount_usd,
        "policy_limit_usd": limit,
        "compliant": compliant,
        "policy_name": f"ACME-TRAVEL-{item_type.upper()}-LIMIT",
        "message": (
            f"Approved: ${amount_usd:.2f} is within the ${limit:.2f} {item_type} limit."
            if compliant
            else f"Denied: ${amount_usd:.2f} exceeds the ${limit:.2f} {item_type} limit."
        ),
    }


def create_booking(flight_number: str, date: str, passenger_name: str) -> dict:
    """Create a confirmed flight booking.

    Args:
        flight_number: Flight identifier (e.g. "UA-204").
        date: Travel date in YYYY-MM-DD format.
        passenger_name: Full name of the passenger.
    """
    return {
        "booking_ref": "BK-1001",
        "flight": flight_number,
        "date": date,
        "passenger": passenger_name,
        "status": "confirmed",
    }


def generate_expense_report(booking_ref: str, trip_dates: str) -> dict:
    """Generate an expense summary for a completed trip.

    Args:
        booking_ref: Booking reference ID (e.g. "BK-1001").
        trip_dates: Date range string (e.g. "2025-06-15 to 2025-06-17").
    """
    return {
        "booking_ref": booking_ref,
        "trip_dates": trip_dates,
        "line_items": [
            {"category": "Flight", "amount_usd": 389.00},
            {"category": "Hotel (2 nights)", "amount_usd": 598.00},
            {"category": "Meals", "amount_usd": 145.00},
            {"category": "Ground transport", "amount_usd": 67.50},
        ],
        "total_usd": 1199.50,
        "status": "pending_approval",
    }


TOOLS = [
    search_flights,
    search_hotels,
    check_policy,
    create_booking,
    generate_expense_report,
]

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_INSTRUCTION = (
    "You are a helpful corporate travel assistant. Use the available tools "
    "to help employees with travel planning, booking, and expense management."
)


def create_agent(model: str = DEFAULT_MODEL, instruction: str = DEFAULT_INSTRUCTION) -> Agent:
    """Factory function for wrangler integration.

    Args:
        model: Model string (e.g. "gemini-3.5-flash", "claude-sonnet-4-6").
            Wrangler resolves this to the appropriate backend.
        instruction: System instruction to use for this agent.
    """
    from wrangler.core.config import resolve_model

    return Agent(
        model=resolve_model(model),
        name="travel_agent",
        description="Corporate travel assistant that searches flights and hotels, checks policies, books travel, and generates expense reports.",
        instruction=instruction,
        tools=TOOLS,
    )


# Backward-compatible default instance
agent = create_agent()
root_agent = agent
