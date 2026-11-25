"""Strict geographic eligibility for the current India/Mumbai search."""
from __future__ import annotations

import re


_INDIAN_CITIES = (
    "ahmedabad", "bengaluru", "bangalore", "bhopal", "bhubaneswar", "chandigarh",
    "chennai", "coimbatore", "dehradun", "delhi", "faridabad", "ghaziabad",
    "gurgaon", "gurugram", "hyderabad", "indore", "jaipur", "kochi", "kolkata",
    "lucknow", "mangalore", "mohali", "mysuru", "mysore", "nagpur", "noida",
    "mumbai", "pune", "surat", "thiruvananthapuram", "trivandrum", "vadodara", "visakhapatnam",
)
_INDIAN_CITY = re.compile(r"\b(?:" + "|".join(map(re.escape, _INDIAN_CITIES)) + r")\b", re.I)
_INDIAN_PLACE = r"(?:india|bharat|" + "|".join(map(re.escape, _INDIAN_CITIES)) + r")"
_REMOTE = re.compile(r"\b(remote|wfh|work[\s-]*from[\s-]*home|work\s*remotely|telecommut(?:e|ing))\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_OUTSIDE_INDIA = re.compile(
    r"\b(?:u\.?s\.?a?\.?|united states|canada|mexico|brazil|latam|latin america|"
    r"north america|uk|united kingdom|ireland|europe|emea|germany|france|spain|"
    r"netherlands|poland|sweden|norway|denmark|switzerland|australia|new zealand|"
    r"singapore|japan|china|south korea|uae|united arab emirates|dubai|israel|"
    r"africa|philippines|indonesia|thailand|vietnam)\b", re.I,
)


def _policy(targets: dict | None) -> tuple[list[str], list[str]]:
    policy = (targets or {}).get("location_policy") or {}
    countries = [str(x).strip().lower() for x in policy.get("remote_countries", ["India"]) if str(x).strip()]
    cities = [str(x).strip().lower() for x in policy.get("office_cities", ["Mumbai"]) if str(x).strip()]
    return countries or ["india"], cities or ["mumbai"]


def location_bucket(job: dict, targets: dict | None = None) -> str | None:
    """Return ``remote_india`` or ``onsite_mumbai`` only when the listing confirms it."""
    remote_countries, office_cities = _policy(targets)
    location = str(job.get("location") or "").strip()
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")[:6000]
    eligibility_text = f"{title} {description}"
    place_text = f"{location} {title}"
    is_hybrid = bool(_HYBRID.search(place_text))
    is_remote = bool(job.get("remote")) or bool(_REMOTE.search(place_text))

    if is_remote and not is_hybrid:
        # Do not treat a generic "Remote" or "Worldwide" listing as India-eligible.
        evidence = location
        generic_location = not evidence or re.fullmatch(
            r"\s*(?:remote|wfh|work[\s-]*from[\s-]*home|anywhere)\s*", evidence, re.I
        )
        if generic_location:
            evidence = f"{location} {eligibility_text}"
            # For a generic location, the title or description must link remote work to India.
            remote_words = r"(?:remote|wfh|work[\s-]*from[\s-]*home|work\s*remotely)"
            india_place = r"\b" + _INDIAN_PLACE + r"\b"
            remote_india = bool(re.search(
                remote_words + r".{0,100}" + india_place + r"|" + india_place + r".{0,100}" + remote_words,
                eligibility_text, re.I | re.S,
            ))
            if not remote_india:
                return None
        has_country = any(re.search(r"\b" + re.escape(country) + r"\b", evidence, re.I)
                          for country in remote_countries)
        has_indian_city = bool(_INDIAN_CITY.search(evidence))
        foreign_text = f"{location} {eligibility_text}"
        if (has_country or has_indian_city) and not _OUTSIDE_INDIA.search(foreign_text):
            return "remote_india"
        return None

    # On-site and hybrid work is eligible only for the configured city, based on the
    # listing's location field. A city mentioned elsewhere in the job text is not enough.
    if _OUTSIDE_INDIA.search(location):
        return None
    if any(re.search(r"\b" + re.escape(city) + r"\b", location, re.I) for city in office_cities):
        return "onsite_mumbai"
    return None


def location_rejection(job: dict, targets: dict | None = None) -> str | None:
    if location_bucket(job, targets):
        return None
    location = str(job.get("location") or "not specified").strip()
    if _HYBRID.search(f"{location} {job.get('title') or ''}"):
        return f"on-site/hybrid roles are limited to Mumbai ({location})"
    if _REMOTE.search(f"{location} {job.get('title') or ''}") or job.get("remote"):
        return f"remote eligibility in India not confirmed ({location})"
    return f"on-site/hybrid roles are limited to Mumbai ({location})"
