"""Mock booking database — in-memory store for flight and hotel reservations."""

import uuid
from datetime import datetime

# Pre-seeded bookings so eval cases that reference fixed IDs (BK-001..BK-006)
# and owners (EMP001/EMP002) have real data to act on. Cases that use
# BK-DOESNOTEXIST / BK-INVALID123 deliberately remain absent (error handling).
bookings: dict[str, dict] = {
    "BK-001": {
        "booking_id": "BK-001",
        "type": "flight",
        "item_id": "FL002",
        "user_id": "EMP001",
        "passenger_name": "Alice Johnson",
        "status": "confirmed",
        "created_at": "2026-05-01T09:00:00",
    },
    "BK-002": {
        "booking_id": "BK-002",
        "type": "hotel",
        "item_id": "HT001",
        "user_id": "EMP001",
        "guest_name": "Alice Johnson",
        "checkin": "2026-06-10",
        "checkout": "2026-06-12",
        "status": "confirmed",
        "created_at": "2026-05-02T09:00:00",
    },
    "BK-003": {
        "booking_id": "BK-003",
        "type": "flight",
        "item_id": "FL001",
        "user_id": "EMP003",
        "passenger_name": "Bob Smith",
        "status": "confirmed",
        "created_at": "2026-05-03T09:00:00",
    },
    "BK-004": {
        "booking_id": "BK-004",
        "type": "flight",
        "item_id": "FL003",
        "user_id": "EMP002",
        "passenger_name": "Lisa Wang",
        "status": "confirmed",
        "created_at": "2026-05-04T09:00:00",
    },
    "BK-005": {
        "booking_id": "BK-005",
        "type": "hotel",
        "item_id": "HT002",
        "user_id": "EMP002",
        "guest_name": "Lisa Wang",
        "checkin": "2026-07-01",
        "checkout": "2026-07-05",
        "status": "confirmed",
        "created_at": "2026-05-05T09:00:00",
    },
    "BK-006": {
        "booking_id": "BK-006",
        "type": "flight",
        "item_id": "FL004",
        "user_id": "EMP004",
        "passenger_name": "Carol Davis",
        "status": "confirmed",
        "created_at": "2026-05-06T09:00:00",
    },
}


def create_booking(booking_type: str, item_id: str, details: dict) -> dict:
    """item_id is not validated against the search catalogue; agents should verify IDs via search tools before booking."""
    booking_id = f"BK-{uuid.uuid4().hex[:8].upper()}"
    booking = {
        "booking_id": booking_id,
        "type": booking_type,
        "item_id": item_id,
        "status": "confirmed",
        "created_at": datetime.now().isoformat(),
        **details,
    }
    bookings[booking_id] = booking
    return booking


def cancel_booking(booking_id: str) -> dict | None:
    if booking_id not in bookings:
        return None
    bookings[booking_id]["status"] = "cancelled"
    bookings[booking_id]["cancelled_at"] = datetime.now().isoformat()
    return bookings[booking_id]


def get_booking(booking_id: str) -> dict | None:
    return bookings.get(booking_id)


def list_bookings() -> list[dict]:
    return list(bookings.values())
