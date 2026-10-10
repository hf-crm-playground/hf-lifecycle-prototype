"""The learner: Thompson sampling over (action, channel) arms, conditioned on
a context cell, with logged propensities.

Where it sits. The rules still decide everything a rule should: holdout,
suppressions, cap and reserve, eligibility, recency, fatigue, tier priority,
fixed rules and operator overrides. The learner only chooses among the
options that survive, inside the highest tier present: which action of that
tier, and on which deliverable channel. A learner can never send something
the rules forbid.

Model. For each arm (action:channel) and cell, the posterior of the mean
reward (net revenue in 7 days minus unsubscribe and spam costs) is Normal,
pooled hierarchically:

    prior from the decision table  ->  arm overall ('*')  ->  arm in cell

so a new or thin cell borrows from its parent and moves away only as its own
data arrives. Thompson sampling draws one mean per option and picks the
largest, minus the send's shadow price (lambda) for options that use budget.

Exploration and propensities. The logging policy is
    pi = (1 - epsilon) * Thompson + epsilon * uniform
so every option has probability at least epsilon / K. The propensity of the
chosen option is estimated with seeded Monte Carlo draws and logged with the
option set, which is what makes off-policy evaluation possible (evaluate.py).
All randomness is seeded by decision_id, so re-running an hour reproduces the
same choice and the same propensity.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .actions import ACTION_BY_ID
from .features import FEATURE_VERSION

PRIOR_N = 10.0  # pseudo-observations behind each prior mean
MIN_SIGMA = 1.0  # floor on the reward standard deviation (EUR)
# Cold start follows the rules: each step down an action's channel preference
# list costs this much in the prior, so with no data the learner mostly picks
# the rules' channel and explores the others.
CHANNEL_ORDER_PENALTY = 0.75


def arm_key(action: str, channel: str) -> str:
    return f"{action}:{channel}"


def prior_mean(action: str, channel: Optional[str] = None) -> float:
    a = ACTION_BY_ID[action]
    v = a.value
    m = (v.base + v.lift) * v.per_conv if v else 0.0
    if channel in a.channels:
        m -= CHANNEL_ORDER_PENALTY * a.channels.index(channel)
    return m


def prior_sigma(action: str) -> float:
    v = ACTION_BY_ID[action].value
    if not v:
        return 5.0
    p = min(max(v.base + v.lift, 0.01), 0.99)
    return max(MIN_SIGMA, v.per_conv * math.sqrt(p * (1 - p)))


@dataclass
class Stat:
    n: float = 0.0
    s: float = 0.0
    ss: float = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        self.s += x
        self.ss += x * x

    def sigma(self, fallback: float) -> float:
        if self.n < 2:
            return fallback
        var = max(0.0, (self.ss - self.s * self.s / self.n) / (self.n - 1))
        return max(MIN_SIGMA, math.sqrt(var))


@dataclass
class Option:
    action: str
    channel: str
    lam: float = 0.0  # shadow price of the send (0 for in-app and native flows)

    @property
    def key(self) -> str:
        return arm_key(self.action, self.channel)


@dataclass
class Choice:
    option: Option
    propensity: float
    greedy: Option
    table: List[Dict[str, object]]

    @property
    def explored(self) -> bool:
        return self.option.key != self.greedy.key


@dataclass
class Model:
    version: str
    trained_at: datetime
    feature_version: str = FEATURE_VERSION
    stats: Dict[str, Dict[str, Stat]] = field(default_factory=lambda: defaultdict(dict))
    epsilon: float = 0.10
    mc_draws: int = 400
    n_rows: int = 0

    # ---------------------------------------------------------- posterior

    def posterior(self, action: str, channel: str, cell: str) -> Tuple[float, float]:
        """(mean, sd) of the arm's mean reward in this cell."""
        key = arm_key(action, channel)
        m0, sig0 = prior_mean(action, channel), prior_sigma(action)
        parent = self.stats.get(key, {}).get("*", Stat())
        sigma = parent.sigma(sig0)
        m_parent = (PRIOR_N * m0 + parent.s) / (PRIOR_N + parent.n)
        own = self.stats.get(key, {}).get(cell, Stat())
        m = (PRIOR_N * m_parent + own.s) / (PRIOR_N + own.n)
        sd = own.sigma(sigma) / math.sqrt(PRIOR_N + own.n)
        return m, sd

    # -------------------------------------------------------------- choice

    def choose(self, options: Sequence[Option], cell: str, seed: str) -> Choice:
        if not options:
            raise ValueError("no options")
        post = [self.posterior(o.action, o.channel, cell) for o in options]
        means = [m - o.lam for (m, _), o in zip(post, options)]
        greedy = options[max(range(len(options)), key=lambda i: means[i])]
        k = len(options)
        rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))

        def ts_draw(r: random.Random) -> int:
            vals = [r.gauss(m, sd) - o.lam for (m, sd), o in zip(post, options)]
            return max(range(k), key=lambda i: vals[i])

        # The actual decision.
        if rng.random() < self.epsilon:
            idx = rng.randrange(k)
        else:
            idx = ts_draw(rng)
        # Propensities: Monte Carlo estimate of Thompson's choice frequencies.
        counts = [0] * k
        mc = random.Random(int(hashlib.sha256((seed + "|mc").encode()).hexdigest()[:16], 16))
        for _ in range(self.mc_draws):
            counts[ts_draw(mc)] += 1
        probs = [(1 - self.epsilon) * c / self.mc_draws + self.epsilon / k for c in counts]
        table = [
            {"action": o.action, "channel": o.channel, "mean": round(post[i][0], 4), "sd": round(post[i][1], 4),
             "lambda": o.lam, "propensity": round(probs[i], 5)}
            for i, o in enumerate(options)
        ]
        return Choice(options[idx], probs[idx], greedy, table)

    def greedy_key(self, options: Iterable[Dict[str, object]], cell: str) -> Optional[str]:
        """The arm a greedy policy under this model would pick from a logged
        option set (used by off-policy evaluation)."""
        best, best_v = None, -1e18
        for o in options:
            m, _ = self.posterior(str(o["action"]), str(o["channel"]), cell)
            v = m - float(o.get("lambda", 0.0) or 0.0)
            if v > best_v:
                best, best_v = arm_key(str(o["action"]), str(o["channel"])), v
        return best

    # -------------------------------------------------------- persistence

    def to_params(self) -> Dict[str, object]:
        return {
            "version": self.version, "feature_version": self.feature_version, "epsilon": self.epsilon,
            "mc_draws": self.mc_draws, "prior_n": PRIOR_N, "n_rows": self.n_rows,
            "stats": {a: {c: [st.n, st.s, st.ss] for c, st in cells.items()} for a, cells in self.stats.items()},
        }

    @classmethod
    def from_params(cls, params: Dict[str, object], trained_at: datetime) -> "Model":
        m = cls(version=str(params["version"]), trained_at=trained_at,
                feature_version=str(params.get("feature_version", FEATURE_VERSION)),
                epsilon=float(params.get("epsilon", 0.1)), mc_draws=int(params.get("mc_draws", 400)),
                n_rows=int(params.get("n_rows", 0)))
        for a, cells in dict(params.get("stats", {})).items():
            for c, (n, s, ss) in cells.items():
                m.stats[a][c] = Stat(n, s, ss)
        return m


def train(rows: Iterable[Dict[str, object]], now: datetime, epsilon: float = 0.10,
          feature_version: str = FEATURE_VERSION) -> Model:
    """Fit from treated reward rows (each a send with action, channel, cell,
    reward). Control rows carry no arm and are used by the uplift table."""
    stats: Dict[str, Dict[str, Stat]] = defaultdict(dict)
    n = 0
    for r in rows:
        if r["arm"] != "treated" or not r["channel"]:
            continue
        key = arm_key(str(r["action"]), str(r["channel"]))
        x = float(r["reward"])
        stats[key].setdefault("*", Stat()).add(x)
        if r.get("cell"):
            stats[key].setdefault(str(r["cell"]), Stat()).add(x)
        n += 1
    digest = hashlib.sha256(json.dumps(
        {a: {c: [s.n, round(s.s, 6)] for c, s in cs.items()} for a, cs in sorted(stats.items())},
        sort_keys=True).encode()).hexdigest()[:8]
    version = f"ts-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M')}-{digest}"
    m = Model(version=version, trained_at=now, feature_version=feature_version, stats=stats, epsilon=epsilon, n_rows=n)
    return m


def prior_model(now: Optional[datetime] = None, epsilon: float = 0.10) -> Model:
    """A model with no data: the decision table's value priors, explored."""
    now = now or datetime.now(timezone.utc)
    return Model(version="prior", trained_at=now, epsilon=epsilon)
