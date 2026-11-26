import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.houses import natural_sort_key
from common.io import ROOT, read_json, write_json_atomic
from common.normalize import get_street_exclude_reason, load_exclude_rules, load_non_residential, load_street_renames


def _next_version(out_path: Path, today: str) -> str:
    if out_path.exists():
        try:
            prev = read_json(out_path)
            prev_ver = prev.get("meta", {}).get("version", "")
            if prev_ver.startswith(today + "."):
                seq = int(prev_ver.split(".")[-1]) + 1
                return f"{today}.{seq}"
        except Exception:
            pass
    return f"{today}.1"


def build_all_outputs(
    profile_path: str | Path,
    *,
    excluded_items: list[dict[str, Any]] | None = None,
    review_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = read_json(profile_path)
    hromada_id = profile["id"]
    state = read_json(ROOT / "data" / "state" / "addresses.json")
    items: dict[str, dict[str, Any]] = state.get("items", {})

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")
    today = now_dt.strftime("%Y-%m-%d")

    out_dir = ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    addr_path = out_dir / "addresses.json"
    version = _next_version(addr_path, today)

    # 1. Build output/addresses.json (schema 2, active only)
    settlements_grouped: dict[str, dict[str, Any]] = {}
    multi_subqueue_count = 0
    active_count = 0
    village_multi_reviews: list[dict[str, Any]] = []

    city_names = {s["name"] for s in profile.get("settlements", []) if s.get("type") == "місто"}

    for item in items.values():
        if item.get("status") != "active":
            continue
        active_count += 1
        sett = item["settlement"]
        okrug = item.get("okrug", "")
        street = item["street"]
        house = item["house"]
        subqueues = sorted(item["subqueues"])

        if len(subqueues) > 1:
            multi_subqueue_count += 1
            if sett not in city_names:
                village_multi_reviews.append(
                    {
                        "issue": "multi_subqueue_village",
                        "settlement": sett,
                        "street": street,
                        "token": house,
                        "suggestion": f"subqueues={subqueues}",
                    }
                )

        sett_obj = settlements_grouped.setdefault(sett, {"u": okrug, "streets": {}})
        sett_obj["streets"].setdefault(street, {})[house] = subqueues

    # Sort settlements, streets, and naturally sort houses
    settlements_sorted: dict[str, Any] = {}
    total_streets = 0
    for sett in sorted(settlements_grouped.keys()):
        st_map = settlements_grouped[sett]["streets"]
        streets_sorted = {}
        for st in sorted(st_map.keys()):
            total_streets += 1
            houses_map = st_map[st]
            streets_sorted[st] = {h: houses_map[h] for h in sorted(houses_map.keys(), key=natural_sort_key)}
        settlements_sorted[sett] = {
            "u": settlements_grouped[sett]["u"],
            "streets": streets_sorted,
        }

    addresses_payload = {
        "meta": {
            "schema": 2,
            "hromada": hromada_id,
            "version": version,
            "generated_at": now_iso,
            "source_sha256": state.get("source_sha256", ""),
            "counts": {
                "settlements": len(settlements_sorted),
                "streets": total_streets,
                "addresses": active_count,
                "multi_subqueue": multi_subqueue_count,
            },
        },
        "settlements": settlements_sorted,
    }

    # Validate against schema/addresses.v2.json
    schema_path = ROOT / "schema" / "addresses.v2.json"
    schema = read_json(schema_path)
    jsonschema.validate(instance=addresses_payload, schema=schema)

    write_json_atomic(addr_path, addresses_payload, compact=True)

    # 2. Build output/excluded_log.json (preserving comments)
    excl_path = out_dir / "excluded_log.json"
    prev_comments: dict[tuple[str, str, str], str] = {}
    if excl_path.exists():
        for prev in read_json(excl_path):
            if prev.get("comment"):
                prev_comments[(prev.get("settlement", ""), prev.get("street_raw", ""), prev.get("token_raw", ""))] = prev["comment"]

    excl_out: list[dict[str, Any]] = []
    for entry in excluded_items or []:
        key = (entry.get("settlement", ""), entry.get("street_raw", ""), entry.get("token_raw", ""))
        excl_out.append(
            {
                "settlement": entry.get("settlement", ""),
                "street_raw": entry.get("street_raw", ""),
                "token_raw": entry.get("token_raw", ""),
                "reason": entry.get("reason", ""),
                "subqueue": entry.get("subqueue", ""),
                "first_seen": today,
                "comment": prev_comments.get(key, ""),
            }
        )
    write_json_atomic(excl_path, excl_out)

    # 3. Build output/needs_review.json (sync reviews + village multi + OSM fuzzy/unmatched)
    rev_out: list[dict[str, Any]] = list(review_items or []) + village_multi_reviews
    renames = load_street_renames()
    exclude_cfg = load_exclude_rules()
    non_res_kw = load_non_residential()

    compare_path = ROOT / "data" / "reports" / f"{hromada_id}_compare_report.json"
    if compare_path.exists():
        cmp_data = read_json(compare_path)
        for item in cmp_data.get("fuzzy_matches", []):
            raw = item["raw"]
            sett = item.get("settlement", "")
            scoped = f"{sett}|{raw}"
            if scoped not in renames and not get_street_exclude_reason(raw, sett, exclude_cfg, non_res_kw):
                rev_out.append(
                    {
                        "issue": "osm_fuzzy",
                        "settlement": sett,
                        "street": raw,
                        "token": "",
                        "suggestion": item["osm"],
                        "fix_template": {scoped: item["osm"]},
                    }
                )
        for item in cmp_data.get("unmatched", []):
            raw = item["raw"]
            sett = item.get("settlement", "")
            scoped = f"{sett}|{raw}"
            if scoped not in renames and not get_street_exclude_reason(raw, sett, exclude_cfg, non_res_kw):
                rev_out.append(
                    {
                        "issue": "unmatched_street",
                        "settlement": sett,
                        "street": raw,
                        "token": "",
                        "suggestion": item.get("best_osm") or "",
                        "fix_template": {scoped: raw},
                    }
                )

    rev_path = out_dir / "needs_review.json"
    write_json_atomic(rev_path, rev_out)

    # 4. Build output/subqueue_changes.json (latest run events from history.jsonl)
    history_path = ROOT / "data" / "state" / "history.jsonl"
    latest_run_id = None
    all_events: list[dict[str, Any]] = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            all_events.append(ev)
            latest_run_id = ev.get("run_id")

    latest_events = [e for e in all_events if latest_run_id and e.get("run_id") == latest_run_id]
    by_settlement: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ev in latest_events:
        sett = ev.get("settlement") or (ev.get("addr_id", "").split("|")[0] if ev.get("addr_id") else "")
        if sett:
            by_settlement[sett][ev["type"]] += 1

    changes_payload = {
        "run_id": latest_run_id,
        "generated_at": now_iso,
        "events_count": len(latest_events),
        "by_settlement": {k: dict(v) for k, v in sorted(by_settlement.items())},
        "events": latest_events,
    }
    changes_path = out_dir / "subqueue_changes.json"
    write_json_atomic(changes_path, changes_payload)

    return {
        "addresses_path": str(addr_path),
        "version": version,
        "counts": addresses_payload["meta"]["counts"],
        "excluded_count": len(excl_out),
        "review_count": len(rev_out),
        "changes_count": len(latest_events),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build schema v2 output files from data/state/addresses.json")
    parser.add_argument("--profile", default="profiles/hromada.json")
    args = parser.parse_args()

    res = build_all_outputs(args.profile)
    print(
        f"Output v2 built ({res['version']}): "
        f"settlements={res['counts']['settlements']}, streets={res['counts']['streets']}, "
        f"addresses={res['counts']['addresses']}, multi={res['counts']['multi_subqueue']}, "
        f"excluded={res['excluded_count']}, review={res['review_count']}"
    )


if __name__ == "__main__":
    main()
