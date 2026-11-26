import re
from functools import lru_cache
from typing import Any

from common.io import ROOT, read_json

PREFIX_TYPE_RE = re.compile(r"^(вул\.|пров\.)\s+(.+)$", re.IGNORECASE)
NUM_TOKEN_RE = re.compile(r"^(\d+)(?:-[йяа])?$", re.IGNORECASE)
INITIAL_RE = re.compile(r"(?:^|\s)[А-ЯІЇЄҐ]\.(?=\s|$)")
IMENI_RE = re.compile(r"(?:^|\s)(імені|ім\.?)(?=\s|$)", re.IGNORECASE)
ORDINAL_NUM_RE = re.compile(r"\b(\d+)-(?:ій|й|я|а|ша|га|тя|та|ма|ої|ї)\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def load_orthography() -> dict[str, Any]:
    return read_json(ROOT / "rules" / "common" / "orthography.json")


@lru_cache(maxsize=1)
def load_street_types() -> dict[str, list[str]]:
    return read_json(ROOT / "rules" / "common" / "street_types.json")


@lru_cache(maxsize=1)
def load_non_residential() -> list[str]:
    cfg = read_json(ROOT / "rules" / "common" / "non_residential.json")
    return cfg.get("keywords", [])


def load_street_renames() -> dict[str, str]:
    p = ROOT / "rules" / "hromada" / "street_renames.json"
    if not p.exists():
        return {}
    data = read_json(p)
    for k in data:
        if "|" not in k:
            raise ValueError(f"Global street_renames key without settlement prefix is forbidden: {k!r}")
    return data


def load_exclude_rules() -> dict[str, list[str]]:
    p = ROOT / "rules" / "hromada" / "exclude.json"
    if not p.exists():
        return {"streets": [], "addresses": []}
    return read_json(p)


def standardize_numbered_street(street: str, ortho: dict[str, Any] | None = None) -> str:
    """Standardize numbered streets/lanes (e.g. 'пров. Польовий 1' -> 'пров. 1-й Польовий')."""
    ortho = ortho or load_orthography()
    m = PREFIX_TYPE_RE.match(street.strip())
    if not m:
        return street

    stype, rest = m.group(1).lower(), m.group(2).strip()
    tokens = rest.split()
    if len(tokens) < 2:
        return street

    exceptions = ortho.get("numbered_street_exceptions", [])
    if any(ex.lower() in rest.lower() for ex in exceptions):
        return street

    ordinal_words = ortho.get("ordinal_words", {})
    suffix = "-й" if stype == "пров." else "-а"
    found_num = None
    other_tokens = []

    for tok in tokens:
        low = tok.lower()
        if found_num is None and low in ordinal_words:
            found_num = ordinal_words[low]
        elif found_num is None and NUM_TOKEN_RE.match(tok):
            found_num = NUM_TOKEN_RE.match(tok).group(1)
        else:
            other_tokens.append(tok)

    if found_num and other_tokens:
        return f"{stype} {found_num}{suffix} {' '.join(other_tokens)}"
    return street


def canonicalize_street(
    street_raw: str,
    settlement: str = "",
    renames: dict[str, str] | None = None,
    ortho: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Apply hromada renames + common orthography + numbered street standardization.

    Returns (canonical_street, was_renamed).
    """
    ortho = ortho or load_orthography()
    renames = renames if renames is not None else load_street_renames()

    scoped_key = f"{settlement}|{street_raw}" if settlement else ""
    street = renames.get(scoped_key, street_raw)
    was_renamed = street != street_raw

    # Unify apostrophes
    apostrophes = ortho.get("apostrophes", ["’", "ʼ", "`"])
    if apostrophes:
        apo_re = "[" + re.escape("".join(apostrophes)) + "]"
        street = re.sub(apo_re, "'", street)

    street = " ".join(street.strip().rstrip(".").split())

    # Space after dot (e.g. вул. М.Грушевського -> вул. М. Грушевського)
    if ortho.get("space_after_dot", True):
        street = re.sub(r"\.([^\s])", r". \1", street)

    # Common orthography replacements (word-boundary safe)
    for wrong, right in ortho.get("replace", {}).items():
        street = re.sub(rf"\b{re.escape(wrong)}\b", right, street)

    street = standardize_numbered_street(street, ortho)
    return street, was_renamed


def _build_street_type_regex(street_types: dict[str, list[str]]) -> re.Pattern[str]:
    variants: list[str] = []
    for canon, aliases in street_types.items():
        variants.append(re.escape(canon.rstrip(".")) + (r"\.?" if canon.endswith(".") else ""))
        for a in aliases:
            variants.append(re.escape(a.rstrip(".")) + (r"\.?" if a.endswith(".") else ""))
    pattern = r"(?:^|\s)(" + "|".join(sorted(set(variants), key=len, reverse=True)) + r")(?=\s|$)"
    return re.compile(pattern, re.IGNORECASE)


def street_match_key(name: str) -> str:
    """Produce a normalized token-sorted comparison key for fuzzy/exact OSM matching."""
    ortho = load_orthography()
    street_types = load_street_types()
    type_re = _build_street_type_regex(street_types)

    s, _ = canonicalize_street(name, settlement="", renames={}, ortho=ortho)
    s = type_re.sub(" ", s)
    s = INITIAL_RE.sub(" ", s)
    s = IMENI_RE.sub(" ", s)
    s = " ".join(s.strip().rstrip(".").split()).lower()
    s = ORDINAL_NUM_RE.sub(r"\1", s)
    for word, num in ortho.get("ordinal_words", {}).items():
        s = re.sub(rf"\b{re.escape(word)}\b", num, s)
    return " ".join(sorted(s.split()))


def get_street_exclude_reason(
    street_raw: str,
    settlement: str = "",
    exclude_cfg: dict[str, list[str]] | None = None,
    non_res_keywords: list[str] | None = None,
) -> str | None:
    """Check if a street should be excluded by manual rules or non-residential keywords (word-boundary)."""
    exclude_cfg = exclude_cfg if exclude_cfg is not None else load_exclude_rules()
    non_res_keywords = non_res_keywords if non_res_keywords is not None else load_non_residential()

    scoped_key = f"{settlement}|{street_raw}" if settlement else ""
    excl_streets = exclude_cfg.get("streets", [])
    if (scoped_key and scoped_key in excl_streets) or street_raw in excl_streets:
        return "manual"

    low = street_raw.lower()
    for kw in non_res_keywords:
        kw_low = kw.strip().lower()
        if not kw_low:
            continue
        # Match at word start (\b) so "склад" does not match "Складська" when not in list,
        # and stems like "свердловин" match "свердловина" but not inside another word.
        if re.search(rf"(?<!\w){re.escape(kw_low)}", low):
            return "non_residential"
    return None
