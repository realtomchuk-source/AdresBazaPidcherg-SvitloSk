import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.houses import classify, load_house_fixes, tokenize
from common.io import ROOT, append_jsonl, read_json, write_json_atomic
from common.normalize import (
    canonicalize_street,
    get_street_exclude_reason,
    load_exclude_rules,
    load_non_residential,
    load_street_renames,
    street_match_key,
)

VALID_SUBQUEUES = {f"{g}.{s}" for g in range(1, 7) for s in (1, 2)}


def load_overrides() -> dict[str, dict[str, Any]]:
    p = ROOT / "rules" / "hromada" / "overrides.json"
    if not p.exists():
        return {}
    items = read_json(p)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        exp = item.get("expires_at")
        if exp and exp < today:
            continue
        result[item["addr_id"]] = item
    return result


def backup_state_file(state_path: Path, keep: int = 14) -> Path | None:
    if not state_path.exists():
        return None
    backup_dir = state_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"addresses_{stamp}.json"
    shutil.copy2(state_path, dst)

    backups = sorted(backup_dir.glob("addresses_*.json"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    return dst


def build_incoming_addresses(
    set1_records: list[dict[str, Any]],
    manual_additions: list[dict[str, Any]],
    exclude_cfg: dict[str, list[str]],
    non_res_kw: list[str],
    renames: dict[str, str],
    house_fixes: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize Set 1 + manual additions into addr_id -> incoming info, plus excluded & review lists."""
    incoming: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    excl_addresses = set(exclude_cfg.get("addresses", []))

    for rec in set1_records:
        settlement = rec["settlement"]
        okrug = rec["admin_unit"]
        street_raw = rec["street_raw"]
        subqueue = rec["subqueue"]

        reason = get_street_exclude_reason(street_raw, settlement, exclude_cfg, non_res_kw)
        if reason:
            excluded.append(
                {
                    "settlement": settlement,
                    "street_raw": street_raw,
                    "token_raw": rec.get("houses_raw", ""),
                    "reason": reason,
                    "subqueue": subqueue,
                }
            )
            continue

        if subqueue not in VALID_SUBQUEUES:
            review.append(
                {
                    "issue": "invalid_subqueue",
                    "settlement": settlement,
                    "street": street_raw,
                    "token": subqueue,
                }
            )
            continue

        street, _ = canonicalize_street(street_raw, settlement, renames)
        tokens = tokenize(rec.get("houses_raw", ""))
        if not tokens:
            review.append(
                {
                    "issue": "empty_houses",
                    "settlement": settlement,
                    "street": street,
                    "token": "",
                }
            )
            continue

        for tok in tokens:
            res = classify(tok, settlement=settlement, street=street, house_fixes=house_fixes)
            if res["route"] == "excluded":
                excluded.append(
                    {
                        "settlement": settlement,
                        "street_raw": street_raw,
                        "token_raw": tok,
                        "reason": res["reason"],
                        "subqueue": subqueue,
                    }
                )
                continue
            if res["route"] != "clean" or not res["base"]:
                review.append(
                    {
                        "issue": res["reason"] or "other_token",
                        "settlement": settlement,
                        "street": street,
                        "token": tok,
                        "fix_template": {f"{settlement}|{street}|{tok}": ""},
                    }
                )
                continue

            house = res["base"]
            addr_id = f"{settlement}|{street}|{house}"
            if addr_id in excl_addresses:
                excluded.append(
                    {
                        "settlement": settlement,
                        "street_raw": street_raw,
                        "token_raw": tok,
                        "reason": "manual",
                        "subqueue": subqueue,
                    }
                )
                continue

            entry = incoming.setdefault(
                addr_id,
                {
                    "settlement": settlement,
                    "okrug": okrug,
                    "street": street,
                    "house": house,
                    "subqueues": set(),
                    "raw": set(),
                },
            )
            entry["subqueues"].add(subqueue)
            entry["raw"].add(tok)

    # Apply manual_additions.json
    for item in manual_additions:
        settlement = item["settlement"]
        street = item["street"]
        house = item["house"]
        subqueues = item.get("subqueues") or ([item["subqueue"]] if "subqueue" in item else [])
        addr_id = f"{settlement}|{street}|{house}"
        entry = incoming.setdefault(
            addr_id,
            {
                "settlement": settlement,
                "okrug": item.get("okrug", ""),
                "street": street,
                "house": house,
                "subqueues": set(),
                "raw": set(),
            },
        )
        entry["subqueues"].update(subqueues)
        entry["raw"].add(house)

    return incoming, excluded, review


def compute_sync(
    prev_state: dict[str, Any],
    incoming: dict[str, dict[str, Any]],
    *,
    source_sha256: str,
    run_id: str,
    now_iso: str,
    overrides: dict[str, dict[str, Any]],
    excluded_streets: set[str] | None = None,
    excluded_addresses: set[str] | None = None,
    gone_after_missing_runs: int = 2,
    mass_change_settlement_ratio: float = 0.3,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Compute new state, history events, override review items, and diff summary without writing files."""
    today = now_iso[:10]
    excl_streets = excluded_streets or set()
    excl_addrs = excluded_addresses or set()
    prev_items: dict[str, dict[str, Any]] = prev_state.get("items", {})
    new_items: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    override_reviews: list[dict[str, Any]] = []

    settlement_totals: dict[str, int] = defaultdict(int)
    settlement_changed: dict[str, list[tuple[list[str], list[str]]]] = defaultdict(list)

    # 1. Process incoming addresses
    for addr_id, inc in incoming.items():
        source_sq = sorted(inc["subqueues"])
        ov = overrides.get(addr_id)
        if ov:
            effective_sq = sorted(ov["subqueues"])
            if effective_sq != source_sq:
                events.append(
                    {
                        "ts": now_iso,
                        "run_id": run_id,
                        "addr_id": addr_id,
                        "type": "override_conflict",
                        "old": effective_sq,
                        "new": source_sq,
                        "source_sha256": source_sha256,
                    }
                )
                override_reviews.append(
                    {
                        "issue": "override_conflict",
                        "settlement": inc["settlement"],
                        "street": inc["street"],
                        "token": inc["house"],
                        "suggestion": f"source={source_sq}, override={effective_sq}",
                    }
                )
        else:
            effective_sq = source_sq

        settlement_totals[inc["settlement"]] += 1
        prev = prev_items.get(addr_id)

        if prev is None:
            new_items[addr_id] = {
                "settlement": inc["settlement"],
                "okrug": inc["okrug"],
                "street": inc["street"],
                "house": inc["house"],
                "subqueues": effective_sq,
                "status": "active",
                "raw": sorted(inc["raw"]),
                "override": ov,
                "first_seen": today,
                "last_seen": today,
                "last_changed": today,
                "missing_runs": 0,
            }
            if prev_items:
                events.append(
                    {
                        "ts": now_iso,
                        "run_id": run_id,
                        "addr_id": addr_id,
                        "type": "added",
                        "old": [],
                        "new": effective_sq,
                        "source_sha256": source_sha256,
                    }
                )
        else:
            old_sq = sorted(prev.get("subqueues", []))
            old_status = prev.get("status", "active")
            last_changed = prev.get("last_changed", today)

            if old_status == "gone":
                events.append(
                    {
                        "ts": now_iso,
                        "run_id": run_id,
                        "addr_id": addr_id,
                        "type": "reappeared",
                        "old": old_sq,
                        "new": effective_sq,
                        "source_sha256": source_sha256,
                    }
                )
                last_changed = today

            if old_sq != effective_sq:
                old_set, new_set = set(old_sq), set(effective_sq)
                if old_set < new_set:
                    ev_type = "subqueue_added"
                elif new_set < old_set:
                    ev_type = "subqueue_removed"
                else:
                    ev_type = "subqueue_changed"
                events.append(
                    {
                        "ts": now_iso,
                        "run_id": run_id,
                        "addr_id": addr_id,
                        "type": ev_type,
                        "old": old_sq,
                        "new": effective_sq,
                        "source_sha256": source_sha256,
                    }
                )
                settlement_changed[inc["settlement"]].append((old_sq, effective_sq))
                last_changed = today

            new_items[addr_id] = {
                "settlement": inc["settlement"],
                "okrug": inc["okrug"] or prev.get("okrug", ""),
                "street": inc["street"],
                "house": inc["house"],
                "subqueues": effective_sq,
                "status": "active",
                "raw": sorted(inc["raw"]),
                "override": ov,
                "first_seen": prev.get("first_seen", today),
                "last_seen": today,
                "last_changed": last_changed,
                "missing_runs": 0,
            }

    # 2. Process missing / manually excluded addresses (in prev_items but not in incoming)
    for addr_id, prev in prev_items.items():
        if addr_id in new_items:
            continue
        status = prev.get("status", "active")
        missing_runs = prev.get("missing_runs", 0)
        last_changed = prev.get("last_changed", today)

        sett_street_key = f"{prev.get('settlement', '')}|{prev.get('street', '')}"
        is_manual_excl = (addr_id in excl_addrs) or (sett_street_key in excl_streets) or (prev.get("street", "") in excl_streets)

        if status == "active":
            if is_manual_excl:
                status = "excluded"
                missing_runs = 0
                last_changed = today
                events.append(
                    {
                        "ts": now_iso,
                        "run_id": run_id,
                        "addr_id": addr_id,
                        "type": "excluded",
                        "old": prev.get("subqueues", []),
                        "new": [],
                        "source_sha256": source_sha256,
                    }
                )
            else:
                missing_runs += 1
                if missing_runs >= gone_after_missing_runs:
                    status = "gone"
                    last_changed = today
                    events.append(
                        {
                            "ts": now_iso,
                            "run_id": run_id,
                            "addr_id": addr_id,
                            "type": "gone",
                            "old": prev.get("subqueues", []),
                            "new": [],
                            "source_sha256": source_sha256,
                        }
                    )
                else:
                    events.append(
                        {
                            "ts": now_iso,
                            "run_id": run_id,
                            "addr_id": addr_id,
                            "type": "missing",
                            "old": prev.get("subqueues", []),
                            "new": prev.get("subqueues", []),
                            "source_sha256": source_sha256,
                        }
                    )

        new_items[addr_id] = {
            **prev,
            "status": status,
            "missing_runs": missing_runs,
            "last_changed": last_changed,
        }

    # 3. Detect settlement-level mass_change (>30% of settlement's addresses changed subqueue)
    mass_change_settlements: list[str] = []
    for sett, changes in sorted(settlement_changed.items()):
        total = settlement_totals.get(sett, 0)
        if total > 0 and (len(changes) / total) > mass_change_settlement_ratio:
            mass_change_settlements.append(sett)
            # Pick most common old -> new transition for summary
            old_sample, new_sample = changes[0]
            events.append(
                {
                    "ts": now_iso,
                    "run_id": run_id,
                    "settlement": sett,
                    "type": "mass_change",
                    "changed": len(changes),
                    "total": total,
                    "old": old_sample,
                    "new": new_sample,
                    "source_sha256": source_sha256,
                }
            )

    new_state = {
        "schema": 1,
        "updated_at": now_iso,
        "source_sha256": source_sha256,
        "items": new_items,
    }

    counts_by_type: dict[str, int] = defaultdict(int)
    for ev in events:
        counts_by_type[ev["type"]] += 1

    diff_summary = {
        "run_id": run_id,
        "ts": now_iso,
        "source_sha256": source_sha256,
        "total_active": sum(1 for v in new_items.values() if v["status"] == "active"),
        "total_gone": sum(1 for v in new_items.values() if v["status"] == "gone"),
        "counts": dict(counts_by_type),
        "mass_change": mass_change_settlements,
        "events_count": len(events),
    }

    return new_state, events, override_reviews, diff_summary


def update_snapshots(meta: dict[str, Any], rows_hromada: int, keep: int = 50) -> None:
    snap_path = ROOT / "data" / "state" / "snapshots.json"
    snaps = read_json(snap_path) if snap_path.exists() else []
    entry = {
        "sha256": meta.get("sha256", ""),
        "downloaded_at": meta.get("downloaded_at_utc", ""),
        "last_modified": meta.get("last_modified", ""),
        "file": meta.get("file_path", ""),
        "rows_hromada": rows_hromada,
    }
    if not snaps or snaps[-1].get("sha256") != entry["sha256"]:
        snaps.append(entry)
    snaps = snaps[-keep:]
    write_json_atomic(snap_path, snaps)


def check_ambiguous_village_streets(
    incoming: dict[str, dict[str, Any]],
    ambiguous_settlements: set[str],
    known_streets_by_settlement: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Flag streets in ambiguous_in_rem villages that are not confirmed by OSM/Visicom/renames."""
    if not ambiguous_settlements:
        return []
    flagged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for inc in incoming.values():
        sett = inc["settlement"]
        if sett not in ambiguous_settlements:
            continue
        st = inc["street"]
        if (sett, st) in seen:
            continue
        seen.add((sett, st))
        known_keys = known_streets_by_settlement.get(sett, set())
        if street_match_key(st) not in known_keys:
            flagged.append(
                {
                    "issue": "ambiguous_village_street",
                    "settlement": sett,
                    "street": st,
                    "token": "",
                    "suggestion": "Перевірте: село має двійника в РЕМ, вулиці немає в OSM/Visicom",
                    "fix_template": {"exclude": f"{sett}|{st}"},
                }
            )
    return flagged


def run_sync(profile_path: str | Path, *, run_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    profile = read_json(profile_path)
    hromada_id = profile["id"]
    guards_cfg = profile.get("guards", {})

    set1 = read_json(ROOT / "data" / "set1_oblenergo" / f"{hromada_id}.json")
    meta_path = ROOT / "data" / "raw_oblenergo" / f"{hromada_id}_meta.json"
    meta = read_json(meta_path) if meta_path.exists() else {}

    exclude_cfg = load_exclude_rules()
    non_res_kw = load_non_residential()
    renames = load_street_renames()
    house_fixes = load_house_fixes()
    overrides = load_overrides()
    manual_path = ROOT / "rules" / "hromada" / "manual_additions.json"
    manual_additions = read_json(manual_path) if manual_path.exists() else []

    incoming, excluded, review = build_incoming_addresses(
        set1["records"],
        manual_additions,
        exclude_cfg,
        non_res_kw,
        renames,
        house_fixes,
    )

    state_path = ROOT / "data" / "state" / "addresses.json"
    prev_state = read_json(state_path) if state_path.exists() else {"schema": 1, "items": {}}

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")
    run_id = run_id or now_dt.strftime("%Y%m%d-%H%M")

    new_state, events, override_reviews, diff_summary = compute_sync(
        prev_state,
        incoming,
        source_sha256=set1.get("source_sha256", ""),
        run_id=run_id,
        now_iso=now_iso,
        overrides=overrides,
        excluded_streets=set(exclude_cfg.get("streets", [])),
        excluded_addresses=set(exclude_cfg.get("addresses", [])),
        gone_after_missing_runs=guards_cfg.get("gone_after_missing_runs", 2),
        mass_change_settlement_ratio=guards_cfg.get("mass_change_settlement_ratio", 0.3),
    )
    review.extend(override_reviews)

    # Check unknown streets in ambiguous_in_rem villages (e.g. Решнівка)
    ambiguous_setts = {s["name"] for s in profile.get("settlements", []) if s.get("ambiguous_in_rem")}
    if ambiguous_setts:
        known_by_sett: dict[str, set[str]] = defaultdict(set)
        osm_path = ROOT / "data" / "set2_external" / f"{hromada_id}_osm.json"
        if osm_path.exists():
            osm_setts = read_json(osm_path).get("settlements", {})
            for s_name in ambiguous_setts:
                for st in osm_setts.get(s_name, {}).get("streets", []):
                    known_by_sett[s_name].add(street_match_key(st))
        vis_path = ROOT / "data" / "set2_external" / f"{hromada_id}_visicom.json"
        if vis_path.exists():
            for v in read_json(vis_path).get("items", {}).values():
                if v.get("settlement") in ambiguous_setts and v.get("found") and v.get("visicom"):
                    known_by_sett[v["settlement"]].add(street_match_key(v["visicom"]["name"]))
        for k, canon_st in renames.items():
            s_name = k.split("|", 1)[0]
            if s_name in ambiguous_setts:
                known_by_sett[s_name].add(street_match_key(canon_st))
        review.extend(check_ambiguous_village_streets(incoming, ambiguous_setts, known_by_sett))

    if not dry_run:
        backup_state_file(state_path, keep=14)
        write_json_atomic(state_path, new_state)

        history_path = ROOT / "data" / "state" / "history.jsonl"
        for ev in events:
            append_jsonl(history_path, ev)

        update_snapshots(meta, rows_hromada=len(set1["records"]), keep=50)

        diff_stamp = now_dt.strftime("%Y%m%d_%H%M")
        diff_path = ROOT / "data" / "reports" / f"diff_{diff_stamp}.json"
        write_json_atomic(diff_path, {**diff_summary, "events": events})

    return {
        "new_state": new_state,
        "events": events,
        "excluded": excluded,
        "review": review,
        "diff_summary": diff_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize Set 1 into data/state/addresses.json and history.jsonl")
    parser.add_argument("--profile", default="profiles/hromada.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    res = run_sync(args.profile, dry_run=args.dry_run)
    ds = res["diff_summary"]
    print(
        f"Sync complete: active={ds['total_active']}, gone={ds['total_gone']}, "
        f"events={ds['events_count']}, counts={ds['counts']}, mass_change={ds['mass_change']}"
    )


if __name__ == "__main__":
    main()
