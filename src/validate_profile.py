import argparse
import sys
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, read_json, write_json_atomic


def validate_profile(profile: dict[str, Any], osm_data: dict[str, Any] | None, set1: dict[str, Any] | None, skipped: dict[str, int] | None) -> dict[str, Any]:
    settlements = profile.get("settlements", [])
    seen_keys: dict[str, str] = {}
    duplicate_errors: list[str] = []

    profile_names: set[str] = set()
    for s in settlements:
        name = s["name"]
        profile_names.add(name)
        for token in [name] + s.get("aliases", []):
            low = token.lower()
            if low in seen_keys:
                duplicate_errors.append(f"Duplicate name/alias '{token}' (conflicts with '{seen_keys[low]}')")
            else:
                seen_keys[low] = name

    # Compare with OSM
    osm_names = set(osm_data.get("settlements", {}).keys()) if osm_data else set()
    osm_matched = sorted(profile_names & osm_names) if osm_names else []
    osm_missing = sorted(profile_names - osm_names) if osm_names else []
    osm_extra = sorted(osm_names - profile_names) if osm_names else []

    # Compare with XLSX (Set 1)
    xlsx_names = {r["settlement"] for r in set1.get("records", [])} if set1 else set()
    xlsx_matched = sorted(profile_names & xlsx_names) if set1 else []
    xlsx_missing = sorted(profile_names - xlsx_names) if set1 else []

    # Near-matches in skipped settlements (e.g. Воронівці ~ Воронківці)
    near_matches: list[dict[str, Any]] = []
    if skipped:
        for sk_name, count in skipped.items():
            best_name = None
            best_score = 0.0
            for p_name in profile_names:
                score = fuzz.ratio(sk_name.lower(), p_name.lower())
                if score > best_score:
                    best_score = score
                    best_name = p_name
            if best_score >= 78.0 and best_name:
                near_matches.append(
                    {
                        "skipped_settlement": sk_name,
                        "records_count": count,
                        "closest_profile_settlement": best_name,
                        "similarity": round(best_score, 1),
                    }
                )
    near_matches.sort(key=lambda x: (-x["similarity"], x["skipped_settlement"]))

    return {
        "hromada_id": profile["id"],
        "profile_version": profile.get("profile_version"),
        "total_settlements": len(settlements),
        "duplicate_errors": duplicate_errors,
        "osm_check": {
            "available": bool(osm_names),
            "matched": len(osm_matched),
            "total": len(profile_names),
            "missing_in_osm": osm_missing,
            "extra_in_osm": osm_extra,
        },
        "xlsx_check": {
            "available": bool(set1),
            "matched": len(xlsx_matched),
            "total": len(profile_names),
            "missing_in_xlsx": xlsx_missing,
        },
        "near_matches_in_skipped": near_matches,
        "ok": not duplicate_errors and (not osm_names or len(osm_missing) == 0) and (not set1 or len(xlsx_missing) == 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate hromada profile against OSM and XLSX")
    parser.add_argument("--profile", default="profiles/hromada.json")
    args = parser.parse_args()

    profile = read_json(args.profile)
    hromada_id = profile["id"]

    osm_path = ROOT / "data" / "set2_external" / f"{hromada_id}_osm.json"
    osm_data = read_json(osm_path) if osm_path.exists() else None

    set1_path = ROOT / "data" / "set1_oblenergo" / f"{hromada_id}.json"
    set1 = read_json(set1_path) if set1_path.exists() else None

    skipped_path = ROOT / "data" / "set1_oblenergo" / f"{hromada_id}_skipped_settlements.json"
    skipped = read_json(skipped_path) if skipped_path.exists() else None

    report = validate_profile(profile, osm_data, set1, skipped)
    out_path = ROOT / "data" / "reports" / "profile_check.json"
    write_json_atomic(out_path, report)

    print(f"Profile check -> {out_path}")
    print(f"  Settlements: {report['total_settlements']}, duplicates: {len(report['duplicate_errors'])}")
    print(f"  OSM match:   {report['osm_check']['matched']}/{report['osm_check']['total']}")
    print(f"  XLSX match:  {report['xlsx_check']['matched']}/{report['xlsx_check']['total']}")
    print(f"  Near-matches in skipped: {len(report['near_matches_in_skipped'])}")
    for nm in report["near_matches_in_skipped"][:10]:
        print(f"    WARNING: '{nm['skipped_settlement']}' (~ '{nm['closest_profile_settlement']}', {nm['similarity']}%)")

    if not report["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
