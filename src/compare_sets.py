import argparse
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, read_json, write_json_atomic
from common.normalize import (
    canonicalize_street,
    get_street_exclude_reason,
    load_exclude_rules,
    load_non_residential,
    load_street_renames,
    street_match_key,
)

STREET_NUM_RE = re.compile(r"\b(\d+)\b")


def extract_street_number(norm_name: str) -> str | None:
    m = STREET_NUM_RE.search(norm_name)
    return m.group(1) if m else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Set 1 (Oblenergo) and Set 2 (OSM)")
    parser.add_argument("--profile", default="profiles/hromada.json")
    args = parser.parse_args()

    profile = read_json(args.profile)
    hromada_id = profile["id"]

    set1 = read_json(ROOT / "data" / "set1_oblenergo" / f"{hromada_id}.json")
    set2 = read_json(ROOT / "data" / "set2_external" / f"{hromada_id}_osm.json")
    visicom_path = ROOT / "data" / "set2_external" / f"{hromada_id}_visicom.json"
    visicom_items = read_json(visicom_path).get("items", {}) if visicom_path.exists() else {}
    renames = load_street_renames()
    exclude_cfg = load_exclude_rules()
    non_res_kw = load_non_residential()

    osm_streets_all = set2.get("streets", [])
    osm_by_settlement = set2.get("settlements", {})

    unique_pairs = sorted({(r["settlement"], r["street_raw"]) for r in set1["records"]})

    non_residential = []
    exact_matches = []
    fuzzy_matches = []
    unmatched = []

    for settlement, raw in unique_pairs:
        if get_street_exclude_reason(raw, settlement, exclude_cfg, non_res_kw):
            non_residential.append({"settlement": settlement, "raw": raw})
            continue

        canon, _ = canonicalize_street(raw, settlement, renames)
        norm = street_match_key(canon)

        # Match strictly within the settlement's OSM streets when available
        sett_osm_streets = osm_by_settlement.get(settlement, {}).get("streets", [])
        osm_norm_map = {street_match_key(s): s for s in sett_osm_streets}
        osm_norm_keys = list(osm_norm_map.keys())

        if norm in osm_norm_map:
            exact_matches.append({"settlement": settlement, "raw": raw, "osm": osm_norm_map[norm]})
            continue

        # Subset match: if all words of norm uniquely appear in one OSM street (e.g. "хмельницького" -> "богдана хмельницького", "небесної сотні" -> "героїв небесної сотні")
        norm_words = set(norm.split())
        if len(norm) >= 5 and norm_words:
            subset_hits = [k for k in osm_norm_keys if norm_words.issubset(set(k.split()))]
            if len(subset_hits) == 1:
                exact_matches.append({"settlement": settlement, "raw": raw, "osm": osm_norm_map[subset_hits[0]]})
                continue

        raw_num = extract_street_number(norm)
        candidate_keys = (
            [k for k in osm_norm_keys if extract_street_number(k) == raw_num]
            if raw_num
            else [k for k in osm_norm_keys if extract_street_number(k) is None]
        )

        best = process.extractOne(norm, candidate_keys, scorer=fuzz.token_sort_ratio) if candidate_keys else None
        if best and best[1] >= 80:
            osm_name = osm_norm_map[best[0]]
            entry = {"settlement": settlement, "raw": raw, "osm": osm_name, "score": round(best[1], 1)}
            if ("вул." in raw.lower() and "провулок" in osm_name.lower()) or ("пров." in raw.lower() and "вулиця" in osm_name.lower()):
                entry["note"] = "Перевірте тип: вулиця чи провулок"
            fuzzy_matches.append(entry)
        else:
            # Fallback: check Visicom cache for this settlement|street
            vis_entry = visicom_items.get(f"{settlement}|{raw}")
            if vis_entry and vis_entry.get("found") and vis_entry.get("visicom"):
                vis_name = vis_entry["visicom"]["name"]
                vis_norm = street_match_key(vis_name)
                if vis_norm == norm:
                    exact_matches.append({"settlement": settlement, "raw": raw, "osm": vis_name, "source": "visicom"})
                    continue
                v_score = round(fuzz.token_sort_ratio(norm, vis_norm), 1)
                if v_score >= 80:
                    fuzzy_matches.append({"settlement": settlement, "raw": raw, "osm": vis_name, "score": v_score, "source": "visicom"})
                    continue
            unmatched.append({"settlement": settlement, "raw": raw, "best_osm": osm_norm_map[best[0]] if best else None, "score": round(best[1], 1) if best else 0})

    out_dir = ROOT / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{hromada_id}_compare_report.json"

    report = {
        "hromada_id": hromada_id,
        "set1_records": set1["total_records"],
        "set1_unique_pairs": len(unique_pairs),
        "set2_osm_streets": len(osm_streets_all),
        "summary": {
            "exact_matches": len(exact_matches),
            "fuzzy_matches_75_plus": len(fuzzy_matches),
            "non_residential": len(non_residential),
            "unmatched": len(unmatched),
        },
        "fuzzy_matches": fuzzy_matches,
        "non_residential": non_residential,
        "unmatched": unmatched,
    }
    write_json_atomic(report_path, report)

    print(f"Set 1: {set1['total_records']} records, {len(unique_pairs)} unique (settlement, street) pairs")
    print(f"Set 2 (OSM): {len(osm_streets_all)} unique street names")
    print("-" * 40)
    print(f"Exact matches:       {len(exact_matches)}")
    print(f"Fuzzy matches (75+): {len(fuzzy_matches)}")
    print(f"Non-residential:     {len(non_residential)}")
    print(f"Unmatched (<75%):    {len(unmatched)}")
    print(f"Report saved -> {report_path}")


if __name__ == "__main__":
    main()
