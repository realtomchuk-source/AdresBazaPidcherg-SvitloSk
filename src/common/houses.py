import re
from functools import lru_cache
from typing import Any

from common.io import ROOT, read_json

TOKEN_SPLIT_RE = re.compile(r",\s*(?=\d|оп\.?)", re.IGNORECASE)
SLASH_LETTER_RE = re.compile(r"^(\d+)\s*/\s*([а-яіїєґa-z])$", re.IGNORECASE)
DIGIT_LETTER_RE = re.compile(r"(\d)\s*[-–]?\s*([а-яіїєґa-z])$", re.IGNORECASE)
NATURAL_CHUNK_RE = re.compile(r"(\d+)")

LATIN_TO_CYRILLIC = str.maketrans(
    {
        "a": "а",
        "b": "в",
        "c": "с",
        "e": "е",
        "k": "к",
        "m": "м",
        "h": "н",
        "o": "о",
        "p": "р",
        "t": "т",
        "x": "х",
    }
)


@lru_cache(maxsize=1)
def load_house_patterns() -> list[dict[str, Any]]:
    raw_patterns = read_json(ROOT / "rules" / "common" / "house_patterns.json")
    compiled = []
    for item in raw_patterns:
        compiled.append(
            {
                "class": item["class"],
                "regex": re.compile(item["pattern"], re.IGNORECASE),
                "route": item["route"],
                "reason": item.get("reason"),
            }
        )
    return compiled


def load_house_fixes() -> dict[str, str]:
    p = ROOT / "rules" / "hromada" / "house_fixes.json"
    if not p.exists():
        return {}
    return read_json(p)


def tokenize(houses_raw: Any) -> list[str]:
    """Split raw house list on commas only when followed by a new house number or 'оп.'."""
    if houses_raw is None:
        return []
    text = str(houses_raw).strip()
    if not text:
        return []
    parts = TOKEN_SPLIT_RE.split(text)
    result: list[str] = []
    for part in parts:
        cleaned = " ".join(part.strip().rstrip(",").strip().split())
        if cleaned:
            result.append(cleaned)
    return result


def normalize_base(base: str) -> str:
    """Normalize a base house number: lowercase, Latin->Cyrillic lookalikes, 20/а->20а, 21-А->21а."""
    s = " ".join(base.strip().split()).lower()
    s = s.translate(LATIN_TO_CYRILLIC)

    # 20/а -> 20а
    m_sl = SLASH_LETTER_RE.match(s)
    if m_sl:
        return f"{m_sl.group(1)}{m_sl.group(2)}"

    # Normalize spaces around slash (e.g. 1 / 144 -> 1/144)
    s = re.sub(r"\s*/\s*", "/", s)

    # Remove space or hyphen between digit and trailing letter (21 а, 21-а, 2/12-а -> 21а, 2/12а)
    s = DIGIT_LETTER_RE.sub(r"\1\2", s)
    # Also handle letter before slash with space (e.g. 12 а/1 -> 12а/1)
    s = re.sub(r"(\d)\s*[-–]?\s*([а-яіїєґ])/", r"\1\2/", s)
    return s


def classify(
    token: str,
    *,
    settlement: str = "",
    street: str = "",
    house_fixes: dict[str, str] | None = None,
    patterns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify a single house token and return {class, base, route, reason, raw}."""
    raw = " ".join(str(token).strip().split())
    fixes = house_fixes if house_fixes is not None else load_house_fixes()
    pats = patterns if patterns is not None else load_house_patterns()

    effective = raw.replace("\\", "/")
    if settlement and street:
        fix_key = f"{settlement}|{street}|{raw}"
        if fix_key in fixes:
            effective = fixes[fix_key]

    # Special case: N/letter (e.g. 20/а, 11/A, 13/Б) -> class 'house', base '20а'
    if SLASH_LETTER_RE.match(effective):
        return {
            "class": "house",
            "base": normalize_base(effective),
            "route": "clean",
            "reason": None,
            "raw": raw,
        }

    for pat in pats:
        m = pat["regex"].search(effective)
        if m:
            cls = pat["class"]
            route = pat["route"]
            reason = pat["reason"]
            base_raw = m.groupdict().get("base")
            base = normalize_base(base_raw) if base_raw else None
            return {
                "class": cls,
                "base": base,
                "route": route,
                "reason": reason,
                "raw": raw,
            }

    return {
        "class": "other",
        "base": None,
        "route": "review",
        "reason": "other_token",
        "raw": raw,
    }


def natural_sort_key(house: str) -> list[tuple[int, int | str]]:
    """Natural sort key for house numbers: numeric chunks as ints, text chunks as strings."""
    parts = NATURAL_CHUNK_RE.split(house)
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return key
