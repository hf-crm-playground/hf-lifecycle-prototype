"""Trip Decision Service, policy v1: the design's pseudocode as code.

Order of evaluation (identical for every customer):

  operator exclude -> global holdout -> global suppressions (care, refund)
  -> cap -> for each action: decision point and its own rules, kill switch,
  data gap, demotion, trip confidence, Tier 0 reserve, same-action cooldown,
  recency gap, content
  fatigue, capture sub-budget, channel and sub-caps, S(a)
  -> operator manual action -> fixed rules (F1, R1) -> ranking
  -> deliverable channel -> action-level control -> send window.

"No message" is always a candidate: every exit that sends nothing records
the reason code that caused it, so the decision log explains every customer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .actions import ACTION_BY_ID, ACTIONS, CONF_RANK, NOT_AT_POINT, Action, Value
from .assignment import decision_id, in_action_control, in_global_holdout
from .config import DEFAULT_POLICY, PolicyConfig
from .context import DecisionContext
from .explain import sentence_for
from .features import FEATURE_VERSION, cell, feature_vector
from .learner import Model, Option
from .scoring import Score, score
from .windows import next_send_time

# Hours after the decision in which the action's outcome is measured.
OUTCOME_WINDOW_HOURS = {
    "I1": 48, "I2": 72, "I3": 48, "P1": 72, "I6": 24,
    "B1": 168, "I4": 72, "I5": 168, "C1": 168, "C2": 168,
    "T1": 336, "T2": 1440, "T3": 336, "P2": 336, "P3": 720,
    "I7": 720, "T4": 720, "B2": 1440, "F1": 168, "R1": 336,
}

# The same action is not chosen again while its outcome window is open
# (capped at 14 days): a trigger that stays true does not fire every hour,
# in-app slots and the C1 gate (which never count toward the cap) are not
# re-issued, and each exposure gets a clean outcome window.
MAX_COOLDOWN_HOURS = 14 * 24


def cooldown_hours(action_id: str) -> float:
    return min(OUTCOME_WINDOW_HOURS.get(action_id, 24), MAX_COOLDOWN_HOURS)


@dataclass
class Candidate:
    id: str
    status: str = "rejected"  # rejected | eligible | chosen
    why: Optional[str] = None
    channel: Optional[str] = None
    score: Optional[Score] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "why": self.why,
            "channel": self.channel,
            "score": self.score.as_dict() if self.score else None,
        }


@dataclass
class Decision:
    decision_id: str
    customer_id: str
    trip_id: Optional[str]
    decided_at: datetime
    trip_phase: str
    destination: Optional[str]
    locale: Optional[str]
    outcome: str  # "send" | "none"
    action: Optional[str]
    channel: Optional[str]
    reason: Optional[str]
    arm: str  # treatment | holdout | action_control
    candidates: List[Candidate]
    cap_state: Dict[str, Any]
    policy_version: str
    chosen_candidate: Optional[str] = None
    override: bool = False
    fixed: bool = False
    held: bool = False
    send_at: Optional[datetime] = None
    window_opens: Optional[str] = None
    flags: Dict[str, Any] = field(default_factory=dict)
    sentence: str = ""
    features: Dict[str, Any] = field(default_factory=dict)
    # Learning: how the choice was made, so it can be evaluated off-policy.
    policy_mode: str = "rules"
    model_version: Optional[str] = None
    propensity: Optional[float] = None
    control_share: float = 0.0
    explored: bool = False
    options: Optional[List[Dict[str, Any]]] = None
    cell: Optional[str] = None
    feature_version: Optional[str] = None
    feature_vector: Optional[Dict[str, Any]] = None

    @property
    def action_obj(self) -> Optional[Action]:
        return ACTION_BY_ID.get(self.action) if self.action else None

    @property
    def content_key(self) -> Optional[str]:
        a = self.action_obj
        return a.key if a else None

    @property
    def tier(self):
        a = self.action_obj
        if not a:
            return None
        return "fixed" if a.fixed else a.tier

    @property
    def score(self) -> Optional[Score]:
        for r in self.candidates:
            if r.id == self.action:
                return r.score
        return None

    @property
    def experiment_id(self) -> Optional[str]:
        aid = self.action or self.chosen_candidate
        if not aid:
            return "GLOBAL_HOLDOUT" if self.arm == "holdout" else None
        a = ACTION_BY_ID[aid]
        if a.fixed:
            return None
        return a.experiment or f"AC-{aid}"

    @property
    def outcome_window_ends_at(self) -> Optional[datetime]:
        aid = self.action or self.chosen_candidate
        hours = OUTCOME_WINDOW_HOURS.get(aid or "")
        if hours is None:
            return None
        return (self.send_at or self.decided_at) + timedelta(hours=hours)

    @property
    def reason_codes(self) -> List[str]:
        """Primary reason, then each considered action's rejection (max 8)."""
        out = [self.reason or "RANKED"]
        out += [f"{r.id}:{r.why}" for r in self.candidates if r.status == "rejected" and r.why]
        return out[:8]

    @property
    def counts_toward_cap(self) -> bool:
        return self.outcome == "send" and self.channel not in ("in_app", "native_flow")


# ------------------------------------------------------------ channels


def can_receive(c: DecisionContext, ch: str, p: PolicyConfig) -> bool:
    s = c.s
    if ch == "email":
        return s.consent_email and not s.email_complaint_90d
    if ch == "push":
        return (
            s.app_installed and s.push_token
            and (c.phase == "in_trip" or c.tz_known)
            and s.hours_since_push_transactional >= p.push_after_transactional_hours
        )
    if ch == "in_app":
        return s.app_installed and s.in_app_session_14d
    if ch == "whatsapp":
        return p.whatsapp_enabled and s.consent_whatsapp and (c.market or "") not in p.whatsapp_blocked_markets
    if ch == "native_flow":
        return s.consent_email or (s.app_installed and s.push_token)
    return False


def pick_channel(c: DecisionContext, a: Action, p: PolicyConfig) -> Optional[str]:
    for ch in a.channels:
        if not can_receive(c, ch, p):
            continue
        sub = p.subcap_for(ch)
        if sub is not None and c.h.sends_30d.get(ch, 0) >= sub:
            continue
        return ch
    return None


def _conf_ok(have: str, need: str) -> bool:
    return CONF_RANK.get(have, 0) >= CONF_RANK[need]


# ------------------------------------------------------------- decide


def deliverable_channels(c: DecisionContext, a: Action, p: PolicyConfig) -> List[str]:
    out = []
    for ch in a.channels:
        sub = p.subcap_for(ch)
        if can_receive(c, ch, p) and (sub is None or c.h.sends_30d.get(ch, 0) < sub):
            out.append(ch)
    return out


def decide(
    c: DecisionContext,
    p: PolicyConfig = DEFAULT_POLICY,
    uplift: Optional[Dict[str, Value]] = None,
    model: Optional["Model"] = None,
    id_source: str = "",
) -> Decision:
    """`model` is used only when p.ranking == "learner"; otherwise the fixed
    rank (v1) or the uplift table decides."""
    cap = p.cap_for(c.market)
    used = c.h.sends_30d.get("total", 0)
    budget = cap - used
    d = Decision(
        decision_id=decision_id(c.customer_id, c.decided_at, id_source),
        customer_id=c.customer_id,
        trip_id=c.trip_id,
        decided_at=c.decided_at,
        trip_phase=c.phase,
        destination=c.destination,
        locale=c.locale,
        outcome="none",
        action=None,
        channel=None,
        reason=None,
        arm="treatment",
        candidates=[],
        cap_state={"used": used, "cap": cap, "left": max(0, budget), **{k: v for k, v in c.h.sends_30d.items() if k != "total"}},
        policy_version=p.policy_version,
        features=c.to_features(),
        feature_version=FEATURE_VERSION,
        feature_vector=feature_vector(c),
        cell=cell(c),
    )

    def none(reason: str, **kw) -> Decision:
        d.outcome, d.reason = "none", reason
        for k, v in kw.items():
            setattr(d, k, v)
        return _finish(d, c)

    def send(action: Action, channel: str, reason: Optional[str], **kw) -> Decision:
        d.outcome, d.action, d.channel, d.reason = "send", action.id, channel, reason
        for k, v in kw.items():
            setattr(d, k, v)
        st = next_send_time(c.decided_at, c.local_tz, p.window_for(channel, None if action.fixed else action.tier))
        d.held, d.send_at, d.window_opens = st.held, st.send_at, st.opens
        return _finish(d, c)

    if c.s.nba_exclude:
        return none("OPERATOR_EXCLUDE")
    if in_global_holdout(c.customer_id, p.global_holdout, p.holdout_salt):
        return none("HOLDOUT", arm="holdout")
    if c.s.care_open_72h:
        return none("CARE_OPEN")
    if c.s.refunded:
        return none("REFUNDED")
    if budget <= 0:
        return none("CAP_REACHED")
    reserve = p.reserve_for_tier0 if c.phase in ("pre_trip", "in_trip") else 0

    # R2 costs no send budget: the service only sets the flag at a success moment.
    if c.phase == "in_trip" and c.s.app_installed and c.s.success_moment:
        d.flags["rating_prompt_allowed"] = True

    # Fixed rules are scheduled before ranking and consume budget like any send.
    fixed_due: List[str] = []
    if not c.h.feedback_sent_this_trip and (
        c.phase == "trip_end_provisional"
        or (c.phase == "in_trip" and c.plan_ended_usage_stopped and (c.plan_end_hours or 0) <= -24)
    ):
        fixed_due.append("F1")
    if c.phase == "post_trip" and 3 <= (c.days_after_confirm or -1) <= 10 and not c.h.review_invited_this_trip:
        fixed_due.append("R1")

    eligible: List[Candidate] = []
    for a in ACTIONS:
        why = a.check(c)
        row = Candidate(a.id, why=why)
        if why is not None:
            if why != NOT_AT_POINT:
                d.candidates.append(row)
            continue
        d.candidates.append(row)
        if a.id in p.killed:
            row.why = "KILL_SWITCH"
        elif a.needs_data and not p.v2_network_events:
            row.why = "DATA_GAP"
        elif a.id in p.demoted:
            row.why = "DEMOTED"
        elif not _conf_ok(c.trip_confidence, a.min_conf):
            row.why = "LOW_TRIP_CONFIDENCE"
        elif a.tier > 0 and budget - reserve <= 0:
            row.why = "BUDGET_RESERVED_FOR_TIER0"
        elif c.h.hours_since_action.get(a.id, 1e9) < cooldown_hours(a.id):
            row.why = "ACTION_SUPPRESSED"
        elif c.h.hours_since_last_send < p.gap_hours(a.tier):
            row.why = "RECENCY_GAP"
        elif a.key in c.h.fatigued_keys:
            row.why = "CONTENT_FATIGUE"
        elif a.capture_action and c.h.capture_sends_30d >= p.capture_budget:
            row.why = "CAPTURE_BUDGET_SPENT"
        elif a.capture_action and c.s.paid_share < p.min_paid_share_for_capture:
            row.why = "PAID_SHARE_BELOW_30"
        else:
            row.channel = pick_channel(c, a, p)
            row.score = score(c, a, row.channel, p, uplift)
            row.status = "eligible"
            eligible.append(row)

    def outrank_all(except_row: Optional[Candidate] = None):
        for r in eligible:
            if r is not except_row:
                r.status, r.why = "rejected", "OUTRANKED"

    # Operator override: force one action at its next eligible point.
    if c.s.nba_manual_action:
        forced = next((r for r in eligible if r.id == c.s.nba_manual_action), None)
        if forced:
            forced.status = "chosen"
            outrank_all(forced)
            if not forced.channel:
                return none("NO_DELIVERABLE_CHANNEL", chosen_candidate=forced.id, override=True)
            return send(ACTION_BY_ID[forced.id], forced.channel, "MANUAL_ACTION", override=True)

    if fixed_due and budget - reserve > 0:
        fx = ACTION_BY_ID[fixed_due[0]]
        outrank_all()
        ch = next((x for x in fx.channels if can_receive(c, x, p)), None)
        if not ch:
            return none("NO_DELIVERABLE_CHANNEL", chosen_candidate=fx.id, fixed=True)
        return send(fx, ch, "FIXED_RULE", fixed=True)

    if not eligible:
        return none("NO_ELIGIBLE_ACTION")

    choice = None
    if p.ranking == "learner" and model is not None:
        top = min(ACTION_BY_ID[r.id].tier for r in eligible)
        opts = [
            Option(r.id, ch, 0.0 if ch in ("in_app", "native_flow") else p.lambda_send)
            for r in eligible if ACTION_BY_ID[r.id].tier == top
            for ch in deliverable_channels(c, ACTION_BY_ID[r.id], p)
        ]
        if opts:
            choice = model.choose(opts, d.cell or "*", seed=d.decision_id)
            ranked = [next(r for r in eligible if r.id == choice.option.action)]
            ranked[0].channel = choice.option.channel
            ranked += [r for r in eligible if r is not ranked[0]]
            d.policy_mode, d.model_version = "learner", model.version
            d.propensity, d.options, d.explored = choice.propensity, choice.table, choice.explored
        else:
            ranked = sorted(eligible, key=lambda r: (ACTION_BY_ID[r.id].tier, ACTION_BY_ID[r.id].rank))
    elif p.ranking == "uplift":
        ranked = [r for r in eligible if r.score and r.score.total > 0]
        ranked.sort(key=lambda r: (ACTION_BY_ID[r.id].tier, -r.score.total))
        for r in eligible:
            if r not in ranked:
                r.status, r.why = "rejected", "SCORE_NOT_POSITIVE"
        if not ranked:
            return none("SCORE_NOT_POSITIVE")
    else:
        ranked = sorted(eligible, key=lambda r: (ACTION_BY_ID[r.id].tier, ACTION_BY_ID[r.id].rank))

    best = ranked[0]
    for r in ranked[1:]:
        r.status, r.why = "rejected", "OUTRANKED"
    best.status = "chosen"
    a = ACTION_BY_ID[best.id]
    if choice is None:
        # Deterministic logging policy: the chosen option had probability 1.
        d.propensity = 1.0
        d.options = [{"action": best.id, "channel": best.channel, "propensity": 1.0}] if best.channel else None
    if best.channel and choice is not None:
        best.score = score(c, a, best.channel, p, uplift)

    if not best.channel:
        return none("NO_DELIVERABLE_CHANNEL", chosen_candidate=best.id)

    if a.under_test:
        d.control_share = p.action_control
        if in_action_control(c.customer_id, a.id, c.trip_id, p.action_control):
            # The control's outcome window starts when the message would have
            # gone out, so treated and control windows line up.
            st = next_send_time(c.decided_at, c.local_tz, p.window_for(best.channel, a.tier))
            return none("ACTION_CONTROL", arm="action_control", chosen_candidate=best.id, channel=best.channel,
                        send_at=st.send_at)

    capture_only = a.capture_action or bool(best.score and best.score.cap > best.score.d_r)
    return send(a, best.channel, "CAPTURE_ONLY" if capture_only else None)


def _finish(d: Decision, c: DecisionContext) -> Decision:
    d.sentence = sentence_for(d, c)
    return d
