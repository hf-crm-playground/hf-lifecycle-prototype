"""CSV loaders for customer signals (warehouse export or the example file)."""

from __future__ import annotations

import csv
import typing
from dataclasses import fields
from typing import Any, Dict, Optional

from .context import CustomerSignals

_HINTS = typing.get_type_hints(CustomerSignals)


def _coerce(name: str, raw: Optional[str]) -> Any:
    hint = _HINTS[name]
    optional = typing.get_origin(hint) is typing.Union and type(None) in typing.get_args(hint)
    base = next((a for a in typing.get_args(hint) if a is not type(None)), hint) if optional else hint
    if raw is None or raw == "":
        return None if optional else _default(name)
    if base is bool:
        return raw.strip().lower() in ("1", "true", "yes", "y", "t")
    if base is int:
        return int(float(raw))
    if base is float:
        return float(raw)
    return raw


def _default(name: str) -> Any:
    f = next(f for f in fields(CustomerSignals) if f.name == name)
    return f.default


def read_signals_csv(path: Optional[str]) -> Dict[str, CustomerSignals]:
    if not path:
        return {}
    names = {f.name for f in fields(CustomerSignals)}
    out: Dict[str, CustomerSignals] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            kw = {k: _coerce(k, v) for k, v in row.items() if k in names}
            out[kw["customer_id"]] = CustomerSignals(**kw)
    return out
