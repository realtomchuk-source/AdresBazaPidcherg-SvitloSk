import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, read_json, resolve_path, write_json_atomic

SUBQUEUE_RE = re.compile(r"(\d+\.\d+)")


def clean_str(val) -> str:
    if val is None:
        return ""
    return " ".join(str(val).strip().split())


def extract_subqueue(raw: str) -> str:
    m = SUBQUEUE_RE.search(raw)
    return m.group(1) if m else raw


def build_settlement_lookup(profile: dict) -> dict[str, tuple[str, str]]:
    """Map lowercased settlement name/alias -> (canonical_name, okrug)."""
    lookup: dict[str, tuple[str, str]] = {}

    for item in profile["settlements"]:
        canonical = item["name"]
        okrug = item["okrug"]
        lookup[canonical.lower()] = (canonical, okrug)
        for alias in item.get("aliases", []):
            lookup[alias.lower()] = (canonical, okrug)

    return lookup


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse and filter REM XLSX into Set 1")
    parser.add_argument(
        "--profile",
        default="profiles/hromada.json",
        help="Path to hromada profile JSON",
    )
    args = parser.parse_args()

    profile = read_json(args.profile)
    hromada_id = profile["id"]

    meta_path = ROOT / "data" / "raw_oblenergo" / f"{hromada_id}_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Meta file not found: {meta_path}. Run fetch_oblenergo.py first.")

    meta = read_json(meta_path)
    xlsx_path = resolve_path(meta["file_path"])
    if not xlsx_path.exists():
        raise FileNotFoundError(f"XLSX file not found: {xlsx_path}")

    lookup = build_settlement_lookup(profile)
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        ws = wb.active

        records = []
        skipped = Counter()
        header_found = False

        for row in ws.iter_rows(values_only=True):
            cells = [clean_str(c) for c in row[:4]]
            if not any(cells):
                continue

            if not header_found:
                if "населений пункт" in cells[0].lower() and "вулиця" in cells[1].lower():
                    header_found = True
                continue

            settlement_raw, street_raw, houses_raw, subqueue_raw = cells
            if not settlement_raw or not street_raw:
                continue

            match = lookup.get(settlement_raw.lower())
            if not match:
                skipped[settlement_raw] += 1
                continue

            settlement_canonical, admin_unit = match
            records.append(
                {
                    "admin_unit": admin_unit,
                    "settlement": settlement_canonical,
                    "settlement_raw": settlement_raw,
                    "street_raw": street_raw,
                    "houses_raw": houses_raw,
                    "subqueue": extract_subqueue(subqueue_raw),
                }
            )
    finally:
        wb.close()

    out_dir = ROOT / "data" / "set1_oblenergo"
    out_dir.mkdir(parents=True, exist_ok=True)

    set1_path = out_dir / f"{hromada_id}.json"
    set1_payload = {
        "hromada_id": hromada_id,
        "source_sha256": meta["sha256"],
        "downloaded_at_utc": meta["downloaded_at_utc"],
        "total_records": len(records),
        "records": records,
    }
    write_json_atomic(set1_path, set1_payload)

    skipped_path = out_dir / f"{hromada_id}_skipped_settlements.json"
    skipped_sorted = dict(sorted(skipped.items(), key=lambda x: (-x[1], x[0])))
    write_json_atomic(skipped_path, skipped_sorted)

    matched_settlements = len({r["settlement"] for r in records})
    print(f"Parsed {len(records)} records across {matched_settlements} hromada settlements -> {set1_path}")
    print(f"Skipped {sum(skipped.values())} records across {len(skipped)} other settlements -> {skipped_path}")


if __name__ == "__main__":
    main()
