from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


STREET_WORDS = {
    "street": "st", "st.": "st", "road": "rd", "rd.": "rd",
    "avenue": "ave", "ave.": "ave", "lane": "ln", "ln.": "ln",
    "drive": "dr", "dr.": "dr", "boulevard": "blvd", "blvd.": "blvd",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = value.replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def normalize_name(value: str) -> str:
    return normalize_text(value).replace("health care", "healthcare")


def normalize_street(value: str) -> str:
    tokens = normalize_text(value).split()
    return " ".join(STREET_WORDS.get(token, token) for token in tokens)


def normalize_zip(value: str) -> str:
    match = re.search(r"\d{5}", value or "")
    return match.group(0) if match else ""


def name_similarity(left: str, right: str) -> float:
    return round(SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio(), 3)
