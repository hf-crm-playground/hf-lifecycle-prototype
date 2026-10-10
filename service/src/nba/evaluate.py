"""Off-policy evaluation: what would a candidate policy have earned on the
decisions already logged?

Uses the logged option sets and propensities. For each treated row with a
reward, the candidate's choice among the logged options is computed; rows
where it agrees with what was actually sent are re-weighted by 1/propensity.

- IPS:   mean( 1[cand == logged] * reward / propensity )
- SNIPS: sum( w * reward ) / sum( w ), lower variance, slightly biased
- the logging policy's own mean reward, for comparison
- 95% intervals by bootstrap over rows (seeded)

Rows logged under the fixed-rank rules have propensity 1 and a single
option, so they only inform candidates that would have made the same
choice. Run exploration for a while before trusting a comparison.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .learner import Model, arm_key
from .store import Store

Candidate = Callable[[Sequence[Dict[str, object]], str], Optional[str]]


@dataclass
class OPEResult:
    policy: str
    n: int
    matched: int
    ips: float
    snips: float
    ips_ci: Tuple[float, float]
    logged_mean: float
    effective_n: float

    def as_dict(self):
        d = dict(self.__dict__)
        d["ips_ci"] = [round(x, 4) for x in self.ips_ci]
        for k in ("ips", "snips", "logged_mean", "effective_n"):
            d[k] = round(d[k], 4)
        return d


def logged_rows(store: Store, since: Optional[str] = None, until: Optional[str] = None) -> List[Dict[str, object]]:
    """Treated rows logged by the learner (randomised, with real option sets).
    Rules rows have a single option with propensity 1, so every candidate
    "agrees" with them and they say nothing about alternatives: excluded."""
    q = ("SELECT r.reward, r.action, r.channel, r.decided_at, d.options, d.cell, d.propensity FROM rewards r "
         "JOIN decision_log d USING (decision_id) WHERE r.arm = 'treated' AND d.policy_mode = 'learner' "
         "AND d.options IS NOT NULL AND d.propensity IS NOT NULL")
    args: List[str] = []
    if since:
        q += " AND r.decided_at >= ?"
        args.append(since)
    if until:
        q += " AND r.decided_at < ?"
        args.append(until)
    rows = store.db.execute(q, args).fetchall()
    out = []
    for r in rows:
        out.append({"reward": r["reward"], "logged": arm_key(r["action"], r["channel"]),
                    "options": json.loads(r["options"]), "cell": r["cell"] or "*",
                    "propensity": max(float(r["propensity"]), 1e-6)})
    return out


def ips(rows: List[Dict[str, object]], candidate: Candidate, name: str, boot: int = 300, seed: int = 7) -> OPEResult:
    terms, weights, rewards = [], [], []
    matched = 0
    for r in rows:
        pick = candidate(r["options"], r["cell"])
        w = (1.0 / r["propensity"]) if pick == r["logged"] else 0.0
        matched += w > 0
        terms.append(w * r["reward"])
        weights.append(w)
        rewards.append(r["reward"])
    n = len(rows)
    if n == 0:
        return OPEResult(name, 0, 0, 0.0, 0.0, (0.0, 0.0), 0.0, 0.0)
    v_ips = sum(terms) / n
    v_snips = sum(terms) / sum(weights) if sum(weights) > 0 else 0.0
    ess = (sum(weights) ** 2) / sum(w * w for w in weights) if any(weights) else 0.0
    rng = random.Random(seed)
    boots = []
    for _ in range(boot):
        idx = [rng.randrange(n) for _ in range(n)]
        boots.append(sum(terms[i] for i in idx) / n)
    boots.sort()
    ci = (boots[int(0.025 * boot)], boots[int(0.975 * boot) - 1])
    return OPEResult(name, n, matched, v_ips, v_snips, ci, sum(rewards) / n, ess)


def greedy(model: Model) -> Candidate:
    return lambda options, cell: model.greedy_key(options, cell)


def first_listed() -> Candidate:
    """The fixed-rank rules: the first option in the logged set is the
    highest-ranked action on its preferred channel."""
    return lambda options, cell: arm_key(str(options[0]["action"]), str(options[0]["channel"])) if options else None


def compare(store: Store, models: Dict[str, Model]) -> List[Dict[str, object]]:
    rows = logged_rows(store)
    out = [ips(rows, first_listed(), "rules_first_option").as_dict()]
    for name, m in models.items():
        out.append(ips(rows, greedy(m), name).as_dict())
    return out
