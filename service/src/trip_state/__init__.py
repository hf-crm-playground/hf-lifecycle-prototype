"""Trip State Builder: heuristic trip-state inference for Holafly's NBA system.

A trip starts at the customer's first eSIM activation and ends after 14 days
without data usage (Holafly's definition). This package turns orders,
activations, daily usage and plan end dates into one trip state per customer
with a confidence tier, exactly as specified in the design document
("Handling missing, delayed or uncertain travel signals").

No third-party dependencies.
"""

from .config import TripConfig
from .models import (
    Activation,
    Customer,
    Entitlement,
    Order,
    Trip,
    TripSnapshot,
    UsageDay,
)
from .builder import build_trip_state
from .klaviyo import to_profile_properties

__all__ = [
    "TripConfig",
    "Activation",
    "Customer",
    "Entitlement",
    "Order",
    "Trip",
    "TripSnapshot",
    "UsageDay",
    "build_trip_state",
    "to_profile_properties",
]

__version__ = "0.1.0"
