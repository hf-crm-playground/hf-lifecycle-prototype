"""Destination names, SKU slugs and ISO codes, with a timezone per destination.

Klaviyo trip events carry English country names ("Turkey", "United States")
and SKU slugs ("esim-turkey"); the Trip State Builder and the policy use
ISO-3166 alpha-2 codes or Holafly region codes ("EUROPE"). This module is the
single mapping between them. The timezone is the one used for send windows
while the customer is in that destination: the capital's, or the most
populous zone for multi-zone countries. Multi-zone countries are an accepted
approximation until a device timezone is available (gap 15).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional, Tuple

# ISO-2 -> (English name, IANA timezone, extra aliases)
_TABLE: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "AE": ("United Arab Emirates", "Asia/Dubai", ("uae", "dubai", "emirates")),
    "AL": ("Albania", "Europe/Tirane", ()),
    "AR": ("Argentina", "America/Argentina/Buenos_Aires", ()),
    "AT": ("Austria", "Europe/Vienna", ()),
    "AU": ("Australia", "Australia/Sydney", ()),
    "BA": ("Bosnia and Herzegovina", "Europe/Sarajevo", ("bosnia",)),
    "BE": ("Belgium", "Europe/Brussels", ()),
    "BG": ("Bulgaria", "Europe/Sofia", ()),
    "BO": ("Bolivia", "America/La_Paz", ()),
    "BR": ("Brazil", "America/Sao_Paulo", ("brasil",)),
    "CA": ("Canada", "America/Toronto", ()),
    "CH": ("Switzerland", "Europe/Zurich", ()),
    "CL": ("Chile", "America/Santiago", ()),
    "CN": ("China", "Asia/Shanghai", ()),
    "CO": ("Colombia", "America/Bogota", ()),
    "CR": ("Costa Rica", "America/Costa_Rica", ()),
    "CU": ("Cuba", "America/Havana", ()),
    "CY": ("Cyprus", "Asia/Nicosia", ()),
    "CZ": ("Czech Republic", "Europe/Prague", ("czechia",)),
    "DE": ("Germany", "Europe/Berlin", ()),
    "DK": ("Denmark", "Europe/Copenhagen", ()),
    "DO": ("Dominican Republic", "America/Santo_Domingo", ()),
    "EC": ("Ecuador", "America/Guayaquil", ()),
    "EE": ("Estonia", "Europe/Tallinn", ()),
    "EG": ("Egypt", "Africa/Cairo", ()),
    "ES": ("Spain", "Europe/Madrid", ()),
    "FI": ("Finland", "Europe/Helsinki", ()),
    "FR": ("France", "Europe/Paris", ()),
    "GB": ("United Kingdom", "Europe/London", ("uk", "great britain", "england", "scotland")),
    "GE": ("Georgia", "Asia/Tbilisi", ()),
    "GR": ("Greece", "Europe/Athens", ()),
    "GT": ("Guatemala", "America/Guatemala", ()),
    "HK": ("Hong Kong", "Asia/Hong_Kong", ()),
    "HR": ("Croatia", "Europe/Zagreb", ()),
    "HU": ("Hungary", "Europe/Budapest", ()),
    "ID": ("Indonesia", "Asia/Makassar", ("bali",)),
    "IE": ("Ireland", "Europe/Dublin", ()),
    "IL": ("Israel", "Asia/Jerusalem", ()),
    "IN": ("India", "Asia/Kolkata", ()),
    "IS": ("Iceland", "Atlantic/Reykjavik", ()),
    "IT": ("Italy", "Europe/Rome", ()),
    "JM": ("Jamaica", "America/Jamaica", ()),
    "JO": ("Jordan", "Asia/Amman", ()),
    "JP": ("Japan", "Asia/Tokyo", ()),
    "KE": ("Kenya", "Africa/Nairobi", ()),
    "KH": ("Cambodia", "Asia/Phnom_Penh", ()),
    "KR": ("South Korea", "Asia/Seoul", ("korea", "republic of korea")),
    "LA": ("Laos", "Asia/Vientiane", ()),
    "LK": ("Sri Lanka", "Asia/Colombo", ()),
    "LT": ("Lithuania", "Europe/Vilnius", ()),
    "LU": ("Luxembourg", "Europe/Luxembourg", ()),
    "LV": ("Latvia", "Europe/Riga", ()),
    "MA": ("Morocco", "Africa/Casablanca", ()),
    "ME": ("Montenegro", "Europe/Podgorica", ()),
    "MT": ("Malta", "Europe/Malta", ()),
    "MV": ("Maldives", "Indian/Maldives", ()),
    "MX": ("Mexico", "America/Mexico_City", ("méxico",)),
    "MY": ("Malaysia", "Asia/Kuala_Lumpur", ()),
    "NL": ("Netherlands", "Europe/Amsterdam", ("holland", "the netherlands")),
    "NO": ("Norway", "Europe/Oslo", ()),
    "NP": ("Nepal", "Asia/Kathmandu", ()),
    "NZ": ("New Zealand", "Pacific/Auckland", ()),
    "PA": ("Panama", "America/Panama", ()),
    "PE": ("Peru", "America/Lima", ()),
    "PH": ("Philippines", "Asia/Manila", ()),
    "PL": ("Poland", "Europe/Warsaw", ()),
    "PT": ("Portugal", "Europe/Lisbon", ()),
    "QA": ("Qatar", "Asia/Qatar", ()),
    "RO": ("Romania", "Europe/Bucharest", ()),
    "RS": ("Serbia", "Europe/Belgrade", ()),
    "SA": ("Saudi Arabia", "Asia/Riyadh", ()),
    "SE": ("Sweden", "Europe/Stockholm", ()),
    "SG": ("Singapore", "Asia/Singapore", ()),
    "SI": ("Slovenia", "Europe/Ljubljana", ()),
    "SK": ("Slovakia", "Europe/Bratislava", ()),
    "TH": ("Thailand", "Asia/Bangkok", ()),
    "TN": ("Tunisia", "Africa/Tunis", ()),
    "TR": ("Turkey", "Europe/Istanbul", ("türkiye", "turkiye")),
    "TW": ("Taiwan", "Asia/Taipei", ()),
    "TZ": ("Tanzania", "Africa/Dar_es_Salaam", ()),
    "US": ("United States", "America/New_York", ("usa", "united states of america", "us")),
    "UY": ("Uruguay", "America/Montevideo", ()),
    "VN": ("Vietnam", "Asia/Ho_Chi_Minh", ("viet nam",)),
    "ZA": ("South Africa", "Africa/Johannesburg", ()),
}

# Holafly regional plans. The timezone is only a fallback: in-trip, the
# first network country (when known) is a better guide than the region.
REGIONS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "EUROPE": ("Europe", "Europe/Paris", ("europa", "europe-plan")),
    "ASIA": ("Asia", "Asia/Bangkok", ("asia-plan", "southeast asia")),
    "LATAM": ("Latin America", "America/Bogota", ("latin america", "south america", "latinoamerica")),
    "NORTH_AMERICA": ("North America", "America/New_York", ()),
    "CARIBBEAN": ("Caribbean", "America/Santo_Domingo", ()),
    "MIDDLE_EAST": ("Middle East", "Asia/Dubai", ()),
    "BALKANS": ("Balkans", "Europe/Belgrade", ()),
    "WORLD": ("World", "UTC", ("global", "worldwide")),
}

# Markets Holafly sells in, with the default timezone for email when the
# profile has none. Push is not sent on this fallback (tz_known = False).
HOME_TZ = {iso: tz for iso, (_, tz, _) in _TABLE.items()}


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


_LOOKUP: Dict[str, str] = {}
for _code, (_name, _tz, _aliases) in list(_TABLE.items()) + list(REGIONS.items()):
    for _k in (_code, _name, *_aliases):
        _LOOKUP[_slug(_k)] = _code


def to_code(value: Optional[str]) -> Optional[str]:
    """'Turkey' -> 'TR', 'esim-turkey' -> 'TR', 'tr' -> 'TR', 'esim-europe' -> 'EUROPE'.
    Unknown names return None so the caller can count and report them."""
    if not value:
        return None
    s = _slug(str(value))
    if s.startswith("esim-"):
        s = s[5:]
    for suffix in ("-unlimited", "-plan", "-esim"):
        if s.endswith(suffix) and s[: -len(suffix)] in _LOOKUP:
            s = s[: -len(suffix)]
    return _LOOKUP.get(s)


def timezone_for(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    code = code.upper()
    if code in _TABLE:
        return _TABLE[code][1]
    if code in REGIONS:
        return REGIONS[code][1]
    return None


def home_timezone(market: Optional[str]) -> Optional[str]:
    return HOME_TZ.get((market or "").upper())


def name_for(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    entry = _TABLE.get(code.upper()) or REGIONS.get(code.upper())
    return entry[0] if entry else None
