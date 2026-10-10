"""What has to exist in Klaviyo for the service to go live, as a build spec.

The service never creates flows, segments or templates (its write allowlist
is events and profile imports only). This module writes the spec a CRM
operator follows in the Klaviyo UI, generated from the same tables the
policy uses, so the dispatcher branches always match the decision table.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..actions import ACTIONS, FIXED
from .registry import CAPTURE_FLOWS, DISPATCHER_FLOW_NAME, NBA_METRIC_NAME, OVERLAPPING_FLOWS


def dispatcher_spec() -> Dict[str, Any]:
    branches: List[Dict[str, Any]] = []
    for a in ACTIONS + FIXED:
        for ch in a.channels:
            if ch not in ("email", "push", "whatsapp"):
                continue
            branches.append({
                "condition": f"action = {a.id} AND channel = {ch}",
                "content_key": a.key,
                "message": f"{ch}: {a.name}",
                "localise_by": "locale (event property), fallback profile locale",
                "deep_link": ch == "push",
            })
    return {
        "flow": DISPATCHER_FLOW_NAME,
        "trigger": f"Metric: {NBA_METRIC_NAME}",
        "trigger_filters": ["arm equals treatment"],
        "flow_filters": ["nba_exclude is not true"],
        "smart_sending": "OFF (the Decision Service enforces cap, sub-caps and recency)",
        "quiet_hours": "OFF (the Decision Service holds decisions to the local send window)",
        "first_step": "Branch immediately: the service emits the event only when the local send window is open, so no delay or freshness split is needed (expires_at is for audit)",
        "branches": branches,
        "tracking": "UTM utm_source=klaviyo&utm_medium=<channel>&utm_campaign=nba_<content_key>&utm_content=<decision_id>",
        "creative_ab": "Optional random-sample split inside a branch (creative layer only)",
    }


def segments_spec() -> List[Dict[str, Any]]:
    return [
        {"name": "NBA - Global holdout", "definition": "Properties about someone: nba_holdout_group equals holdout",
         "use": "Exclude from every marketing campaign and from overlapping flows, so the holdout is clean"},
        {"name": "NBA - In-app slot active", "definition": "nba_inapp_action is set AND nba_inapp_expires_at is after today",
         "use": "The app reads nba_inapp_action through the profile API for its in-app card"},
        {"name": "NBA - Capture allowed", "definition": "nba_capture_allowed is true AND nba_capture_allowed_until is after now",
         "use": "Flow filter on the abandoned-cart and browse-recovery flows (C1)"},
        {"name": "NBA - Excluded by operator", "definition": "nba_exclude is true", "use": "Audit"},
    ]


def profile_properties_spec() -> List[Dict[str, str]]:
    return [
        {"property": "nba_holdout_group", "type": "string", "values": "holdout | treatment"},
        {"property": "nba_last_action", "type": "string", "values": "content_key or none"},
        {"property": "nba_last_decision_id", "type": "string", "values": "dec_..."},
        {"property": "nba_last_decision_at", "type": "date", "values": "ISO 8601"},
        {"property": "nba_last_reason", "type": "string", "values": "plain sentence, < 200 chars"},
        {"property": "nba_sends_30d_total / _email / _push / _whatsapp", "type": "number", "values": "after this decision"},
        {"property": "nba_inapp_action, nba_inapp_expires_at", "type": "string, date", "values": "in-app slot, 7 days"},
        {"property": "nba_capture_allowed, nba_capture_allowed_until", "type": "boolean, date", "values": "C1 gate, 24 h"},
        {"property": "rating_prompt_allowed", "type": "boolean", "values": "R2; the app enforces store quotas"},
        {"property": "nba_policy_version", "type": "string", "values": "v1.0"},
        {"property": "nba_exclude (operator)", "type": "boolean", "values": "set by CRM; read by the service"},
        {"property": "nba_manual_action (operator)", "type": "string", "values": "action id; read by the service"},
        {"property": "trip_* (Trip State Builder)", "type": "mixed", "values": "see trip_state/klaviyo.py"},
    ]


def phase0_flow_actions() -> List[Dict[str, str]]:
    out = []
    for fid, (name, owner) in OVERLAPPING_FLOWS.items():
        if owner == "review":
            what = "Keep; counts toward the cap; add the holdout segment as an exclusion"
        else:
            what = f"Pause when {owner} goes live (one owner per moment); until then add the holdout exclusion"
        out.append({"flow_id": fid, "flow": name, "nba_action": owner, "phase0": what})
    for fid, name in CAPTURE_FLOWS.items():
        out.append({"flow_id": fid, "flow": name, "nba_action": "C1",
                    "phase0": "Keep sending; add flow filter: segment 'NBA - Capture allowed'"})
    return out


def setup_markdown() -> str:
    d = dispatcher_spec()
    lines = [
        "# Klaviyo setup for the Trip Decision Service",
        "",
        "Generated from the decision table (`python -m nba setup-spec`). The service writes only events and profile properties; everything below is built by a CRM operator.",
        "",
        f"## 1. Flow: {d['flow']}",
        "",
        f"- Trigger: {d['trigger']}; trigger filter: {', '.join(d['trigger_filters'])}",
        f"- Flow filter: {', '.join(d['flow_filters'])}",
        f"- Smart Sending: {d['smart_sending']}",
        f"- Quiet hours: {d['quiet_hours']}",
        f"- First step: {d['first_step']}",
        f"- Tracking: `{d['tracking']}`",
        "",
        "| Branch condition | content_key | Message |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {b['condition']} | {b['content_key']} | {b['message']} |" for b in d["branches"]]
    lines += ["", "## 2. Segments", "", "| Segment | Definition | Use |", "| --- | --- | --- |"]
    lines += [f"| {s['name']} | {s['definition']} | {s['use']} |" for s in segments_spec()]
    lines += ["", "## 3. Profile properties written by the service", "", "| Property | Type | Values |", "| --- | --- | --- |"]
    lines += [f"| {p['property']} | {p['type']} | {p['values']} |" for p in profile_properties_spec()]
    lines += ["", "## 4. Phase 0: existing flows", "", "| Flow id | Flow | NBA action | Phase 0 |", "| --- | --- | --- | --- |"]
    lines += [f"| {f['flow_id']} | {f['flow']} | {f['nba_action']} | {f['phase0']} |" for f in phase0_flow_actions()]
    return "\n".join(lines) + "\n"
