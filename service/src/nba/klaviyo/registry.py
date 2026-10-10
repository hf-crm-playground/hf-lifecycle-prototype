"""What the service expects to find in Holafly's Klaviyo account (H5sgvh).

Metric and flow ids were read from the live account on 10 Oct 2026 (read
only; see klaviyo_verification.json). `verify.verify_account` re-checks them
by name, so a renamed or recreated metric shows up as a failed check rather
than silently empty data.
"""

from __future__ import annotations

from typing import Dict, Tuple

ACCOUNT_ID = "H5sgvh"
NBA_METRIC_NAME = "NBA Decision"  # created by the first NBA Decision event
DISPATCHER_FLOW_NAME = "NBA Dispatcher"  # to be built from setup.dispatcher_spec()

# name -> id, as verified. Grouped by what the service uses them for.
TRIP_METRICS: Dict[str, str] = {
    "activation_event": "TyzA4H",
    "expiration_date_eSIM_v2": "Xmv58M",
    "entitlement_end_date_event": "VJ8K9D",
    "hly_always_on_consumption_threshold": "UPuvhM",
    "hly_always_on_activated": "VGpiWA",
    "hly_consumption_start": "TLDFtV",
    "esim_installation_start": "TP3Dek",
    "esim_installation_complete": "RT8z6X",
    "hly_order_confirmed": "ScxfD3",
    "hly_order_created": "WDtkeZ",
    "Top-up order created": "VwiAKD",
    "Refunded Order": "PiNeBJ",
    "Cancelled Order": "PVPhem",
}

APP_METRICS: Dict[str, str] = {
    "first_open": "QXGFLD",
    "session_start": "Rxnb8H",
    "screen_view": "XqvQ9U",
    "virtual_location_toggle": "WUB972",
    "safe_browsing_toggle": "R6JwwR",
    "referral_link_shared": "SbbqCS",
    "referral_code_copied": "TXBi9m",
    "Swell Referral Completed": "Xb5T4h",
}

INTENT_METRICS: Dict[str, str] = {
    "Viewed Product": "HQTjJT",
    "Added to Cart": "X4b2Fr",
    "Checkout Started": "HivxtE",
    "view_item": "Vw9tQG",
    "begin_checkout": "Sp8XLw",
    "Submitted PDP Calendar Lead": "WScBrE",
    "Submitted Home Calendar Lead": "WE68YJ",
    "Submitted NPS Score": "WZKynx",
}

# Message events feeding the cap ledger: name -> (id, kind, channel).
MESSAGE_METRICS: Dict[str, Tuple[str, str, str]] = {
    "Received Email": ("LbGZwB", "received", "email"),
    "Opened Email": ("Jph828", "opened", "email"),
    "Clicked Email": ("PRc2SX", "clicked", "email"),
    "Bounced Email": ("MnbQtc", "bounced", "email"),
    "Marked Email as Spam": ("Jqm84R", "spam", "email"),
    "Unsubscribed from Email Marketing": ("RwKA5y", "unsubscribed", "email"),
    "Received Push": ("XuL6rm", "received", "push"),
    "Opened Push": ("TgTgML", "opened", "push"),
    "Bounced Push": ("V5XKLk", "bounced", "push"),
    "Received WhatsApp": ("Whh4bY", "received", "whatsapp"),
    "Opened WhatsApp": ("WZ4TrM", "opened", "whatsapp"),
    "Clicked WhatsApp": ("VZvPh5", "clicked", "whatsapp"),
    "Received Text Message": ("V5jaif", "received", "sms"),
    "Skipped Send": ("WgJWLR", "skipped", "email"),
}

# Flows whose messages are service messages, not marketing: excluded from
# the cap, but a transactional push blocks marketing push for 6 h.
TRANSACTIONAL_FLOW_IDS = frozenset({
    "RDaeGW",  # Service - Event Dispatcher - Push Notifications
})

# Live flows that overlap NBA actions. Phase 0: pause (or route through the
# dispatcher) before the action goes live, so one moment has one owner.
OVERLAPPING_FLOWS: Dict[str, Tuple[str, str]] = {
    # id: (name, NBA action that takes over, or "review": keeps running, counts toward the cap)
    "TWmsiY": ("CRM_TRIPS_POST_TOP_UP_V2", "I1"),
    "RJGDNX": ("MAFLY06 - Top up Flow", "I1"),
    "RX3ADw": ("CRM_TRIPS_ALWAYS_ON_ENDING_TOP_UP_PUSH", "I3"),
    "UTREx6": ("CRM_TRIPS_POST_UPSELLING_V3", "T2"),
    "SEhSMh": ("CRM_TRIPS_POST_WINBACK_ACTIVE_BUYERS_V3", "T2"),
    "T2SZRT": ("CRM_TRIPS_POST_WINBACK_ACTIVE_BUYERS_V2", "T2"),
    "ReC6vj": ("CRM_TRIPS_REACT_WINBACK_AT_RISK_CUSTOMERS", "B2"),
    "XrinQr": ("CRM_TRIPS_REACT_WINBACK_DORMANT_CUSTOMERS", "B2"),
    "YtYkP2": ("CRM_TRIPS_REACT_WINBACK_SUPER_DORMANT_CUSTOMERS", "B2"),
    "SegAKS": ("CRM_TRIPS_REACT_AT_RISK_SEGMENT_GAMIFY_10OFF", "review"),
    "UPDqwa": ("CRM_TRIPS_REACT_D1_PRE_TRAVEL_REMINDER (date-based)", "P1"),
    "ULbTxg": ("CRM_TRIPS_POST_LOYALTY_PLATINUM", "review"),
    "VQBu4P": ("CRM_TRIPS_POST_LOYALTY_GOLD", "review"),
    "WDWMtu": ("CRM_TRIPS_POST_LOYALTY_SILVER", "review"),
    "RamEKB": ("CRM_PLANS_POST_ONBOARDING", "review"),
}

# C1 capture: these native flows keep sending, gated by nba_capture_allowed.
CAPTURE_FLOWS: Dict[str, str] = {
    "SMEfpK": "CRM_TRIPS_PRE_ABANDONED_CART_V3",
    "Y3MGDJ": "CRM_TRIPS_PRE_ABANDONED_CART_ES",
    "X8RSQq": "CRM_TRIPS_PRE_ABANDONED_CART_EN",
    "YqWmDE": "CRM_TRIPS_PRE_ABANDONED_CART_FR",
    "RxgHLm": "CRM_TRIPS_PRE_ABANDONED_CART_DE",
    "Xpnhkr": "CRM_TRIPS_PRE_ABANDONED_CART_PT",
    "TvFpJJ": "CRM_TRIPS_PRE_ABANDONED_CART_APP_BEGIN_CHECKOUT_V2",
    "YkTUXw": "CRM_TRIPS_PRE_APP_VIEW_ITEM_RECOVERY_V2",
    "TUTQFa": "CRM_TRIPS_PRE_ABANDONED_ORDER_CREATED_V1",
    "QeUyvL": "PRO - Abandoned Cart",
}

REQUIRED_METRICS = {**TRIP_METRICS, **{k: v[0] for k, v in MESSAGE_METRICS.items()}}
ALL_METRICS = {**TRIP_METRICS, **APP_METRICS, **INTENT_METRICS, **{k: v[0] for k, v in MESSAGE_METRICS.items()}}

# Known gaps found in verification (not errors: the service degrades).
KNOWN_MISSING = {
    NBA_METRIC_NAME: "created by the first NBA Decision event (live mode only)",
    "Intercom conversation metrics": "CARE_OPEN needs an Intercom source outside Klaviyo (warehouse or Intercom API)",
    "daily usage": "not in Klaviyo (gap 2); usage_daily comes from the warehouse",
    "esim_id on activation_event": "eSIMs keyed by order_id; expiration_date_eSIM_v2 also carries the ICCID",
}
