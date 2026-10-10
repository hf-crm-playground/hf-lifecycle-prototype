"""Holafly Trip Decision Service (NBA policy v1).

Sits in front of Klaviyo: reads trip state from the Trip State Builder,
decides one action (or none) per customer per decision point, logs every
decision with its reasons, and activates it in Klaviyo through one
`NBA Decision` event and `nba_*` profile properties.
"""

from .config import DEFAULT_POLICY, POLICY_VERSION, PolicyConfig
from .context import ContactHistory, CustomerSignals, DecisionContext, build_context
from .policy import Decision, decide

__all__ = [
    "DEFAULT_POLICY", "POLICY_VERSION", "PolicyConfig", "ContactHistory", "CustomerSignals",
    "DecisionContext", "build_context", "Decision", "decide",
]
__version__ = "0.1.0"
