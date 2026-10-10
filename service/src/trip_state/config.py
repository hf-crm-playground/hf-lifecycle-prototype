"""Thresholds for the trip heuristics.

Every number here is a policy choice from the design document, not a fact
about customers. Change them in one place; the tests pin the defaults.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TripConfig:
    # Holafly's definition: a trip ends after 14 days without usage.
    trip_gap_days: int = 14
    # The end is provisional at 14 days and confirmed at 16, to absorb
    # usage-reporting latency of up to 48 h.
    trip_end_confirm_days: int = 16
    # Post-trip window after the (provisional) end day; then "between trips".
    post_trip_days: int = 30
    # Usage older than this cannot support run-out decisions.
    usage_stale_hours: int = 48
    # An activation at home with less than this total usage is a test install,
    # not a trip.
    test_install_max_mb: float = 50.0
    # When the network country is unknown, a single day at or above this
    # usage is treated as real travel usage (high confidence).
    high_confidence_day_mb: float = 50.0
    # If an eSIM was activated within this many days before the first usage
    # day, the trip starts at the activation timestamp (the business
    # definition). Otherwise the activation was a test and the trip starts at
    # first usage.
    activation_to_usage_max_days: int = 3
    # Trips longer than this exhaust the in-trip playbook.
    long_trip_days: int = 30
    # Burn rate for run-out projection = mean of the last N usage days.
    runout_projection_days: int = 3
    # "Plan ending soon" horizon.
    plan_end_soon_hours: int = 48
    # Below this share of plan data remaining, the run-out action becomes
    # eligible (policy uses it; the builder only reports the number).
    low_data_pct: float = 20.0


DEFAULT_CONFIG = TripConfig()
