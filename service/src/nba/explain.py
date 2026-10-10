"""One plain sentence per decision, under 200 characters, written to the
`nba_last_reason` profile property so anyone in Klaviyo can see why a
customer got (or did not get) a message."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .actions import SHORT_NAME

if TYPE_CHECKING:  # pragma: no cover
    from .context import DecisionContext
    from .policy import Decision

MAX_LEN = 200

SKIP_LABEL = {
    "OUTRANKED": "outranked",
    "RECENCY_GAP": "recency gap",
    "CONTENT_FATIGUE": "ignored twice in 60 d",
    "LOW_TRIP_CONFIDENCE": "low trip confidence",
    "BUDGET_RESERVED_FOR_TIER0": "budget reserved for Tier 0",
    "CAPTURE_BUDGET_SPENT": "capture budget spent",
    "KILL_SWITCH": "paused",
    "DEMOTED": "demoted",
    "USAGE_STALE": "usage stale",
    "DATA_GAP": "needs v2 data",
    "SCORE_NOT_POSITIVE": "S(a) <= 0",
}

CHANNEL_LABEL = {"email": "email", "push": "push", "in_app": "in-app", "whatsapp": "WhatsApp", "native_flow": "native cart flow"}

NONE_LABEL = {
    "HOLDOUT": "measurement holdout",
    "CARE_OPEN": "open care conversation",
    "REFUNDED": "refunded order",
    "NO_ELIGIBLE_ACTION": "nothing useful to say now",
    "NO_DELIVERABLE_CHANNEL": "no deliverable channel",
    "ACTION_CONTROL": "action-level control",
    "OPERATOR_EXCLUDE": "excluded by operator",
    "SCORE_NOT_POSITIVE": "no action scores above zero",
}


def situation(c: "DecisionContext") -> str:
    if c.phase == "pre_trip":
        h = c.s.hours_since_order
        ordered = f"ordered {int(h)} h ago" if h is not None else "ordered"
        return f"Pre-trip, {ordered}, {'installed' if c.s.install_completed else 'not installed'}"
    if c.phase == "in_trip":
        s = f"In-trip day {c.days_in_trip}"
        if c.data_remaining_pct is not None:
            s += f", {c.data_remaining_pct:.0f}% data left"
        if c.projected_runout_days is not None and (c.runout_before_plan_end or (c.data_remaining_pct or 100) <= 30):
            s += f", run-out in ~{max(1, round(c.projected_runout_days))} d"
        return s
    if c.phase == "trip_end_provisional":
        return f"Trip end provisional, {c.days_since_last_usage} d without usage"
    if c.phase == "post_trip":
        return f"Post-trip, end confirmed {c.days_after_confirm} d ago"
    if c.phase == "between_trips":
        return f"Between trips, {c.days_since_trip_end} d since last trip"
    return "No trip yet"


def sentence_for(d: "Decision", c: "DecisionContext") -> str:
    if d.outcome == "send":
        s = f"{situation(c)} -> {SHORT_NAME.get(d.action, d.action)} by {CHANNEL_LABEL.get(d.channel, d.channel)}."
    else:
        if d.reason == "CAP_REACHED":
            why = f"{d.cap_state['cap']} sends already this month"
        else:
            why = NONE_LABEL.get(d.reason or "", d.reason or "")
        s = f"{situation(c)} -> no message: {why}."
    skipped = [
        f"{SHORT_NAME.get(r.id, r.id)} ({SKIP_LABEL[r.why]})"
        for r in d.candidates
        if r.status == "rejected" and r.why in SKIP_LABEL
    ][:2]
    if skipped:
        s += f" Skipped: {', '.join(skipped)}."
    used_after = d.cap_state["used"] + (1 if d.counts_toward_cap else 0)
    s += f" Budget {used_after} of {d.cap_state['cap']} used."
    return s if len(s) <= MAX_LEN else s[: MAX_LEN - 1] + "…"
