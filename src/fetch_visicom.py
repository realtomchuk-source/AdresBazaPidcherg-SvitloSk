import argparse
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, http_request_with_retry, load_env_key, read_json, write_json_atomic
from common.normalize import canonicalize_street, load_street_renames

VISICOM_GEOCODE_URL = "https://api.visicom.ua/data-api/5.0/uk/geocode.json"
HEADERS = {"User-Agent": "AdresBazaPidcherg-SvitloSk/2.0", "Accept": "application/json"}
MAX_SETTLEMENT_DISTANCE_KM = 12.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def extract_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Visicom geocode response (FeatureCollection, single Feature, or empty)."""
    if not isinstance(payload, dict):
        return []
    t = payload.get("type")
    if t == "FeatureCollection":
        return payload.get("features") or []
    if t == "Feature":
        return [payload]
    return []


def pick_best_street_feature(
    features: list[dict[str, Any]],
    settlement: str,
    *,
    target_lat: float | None = None,
    target_lon: float | None = None,
) -> dict[str, Any] | None:
    """Pick the first Visicom street feature belonging to the target settlement (and within 12 km if coords given)."""
    sett_low = settlement.strip().lower()
    for feat in features:
        props = feat.get("properties") or {}
        vis_sett = (props.get("settlement") or "").strip()
        if vis_sett and sett_low in vis_sett.lower():
            coords = (feat.get("geo_centroid") or {}).get("coordinates") or [None, None]
            lon = coords[0] if len(coords) >= 2 else None
            lat = coords[1] if len(coords) >= 2 else None
            if target_lat is not None and target_lon is not None and lat is not None and lon is not None:
                if _haversine_km(target_lat, target_lon, lat, lon) > MAX_SETTLEMENT_DISTANCE_KM:
                    continue
            st_type = (props.get("type") or "").strip()
            st_name = (props.get("name") or "").strip()
            full_name = f"{st_type} {st_name}".strip() if st_type and st_name else st_name
            return {
                "name": full_name,
                "settlement": vis_sett,
                "lon": lon,
                "lat": lat,
            }
    return None


def query_visicom_street(
    region: str,
    settlement: str,
    street: str,
    api_key: str,
    *,
    target_lat: float | None = None,
    target_lon: float | None = None,
) -> dict[str, Any] | None:
    params = {
        "text": f"{region}, {settlement}, {street}",
        "categories": "adr_street",
        "limit": 5,
        "key": api_key,
    }
    resp = http_request_with_retry("GET", VISICOM_GEOCODE_URL, params=params, headers=HEADERS, timeout=20)
    features = extract_features(resp.json())
    return pick_best_street_feature(features, settlement, target_lat=target_lat, target_lon=target_lon)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch unmatched rural streets from Visicom Data API")
    parser.add_argument("--profile", default="profiles/hromada.json")
    parser.add_argument("--force", action="store_true", help="Re-query already cached streets")
    parser.add_argument("--limit", type=int, default=0, help="Max API queries per run (0 = unlimited)")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between requests in seconds")
    args = parser.parse_args()

    api_key = load_env_key("VISICOM_API_KEY")
    if not api_key:
        print("VISICOM_API_KEY not set in environment or .env. Skipping Visicom fetch.")
        return

    profile = read_json(args.profile)
    hromada_id = profile["id"]
    region = profile.get("region", "Хмельницька область")
    renames = load_street_renames()
    sett_coords = {
        s["name"]: (s.get("lat"), s.get("lon"))
        for s in profile.get("settlements", [])
    }

    compare_path = ROOT / "data" / "reports" / f"{hromada_id}_compare_report.json"
    if not compare_path.exists():
        print(f"Compare report not found: {compare_path}. Run compare_sets.py first.")
        return

    cmp_data = read_json(compare_path)
    targets = [(item["settlement"], item["raw"]) for item in cmp_data.get("unmatched", [])]

    cache_dir = ROOT / "data" / "set2_external"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{hromada_id}_visicom.json"
    cache: dict[str, Any] = read_json(cache_path) if cache_path.exists() else {"hromada_id": hromada_id, "items": {}}
    items: dict[str, Any] = cache.setdefault("items", {})

    to_query = []
    for sett, raw in targets:
        key = f"{sett}|{raw}"
        if args.force or key not in items:
            to_query.append((sett, raw, key))

    if args.limit > 0:
        to_query = to_query[: args.limit]

    if not to_query:
        print(f"Visicom cache is up to date ({len(items)} cached entries) -> {cache_path}")
        return

    print(f"Querying Visicom API for {len(to_query)} unmatched streets...")
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    found_count = 0

    for idx, (sett, raw, key) in enumerate(to_query, 1):
        canon, _ = canonicalize_street(raw, sett, renames)
        t_lat, t_lon = sett_coords.get(sett, (None, None))
        try:
            match = query_visicom_street(region, sett, canon, api_key, target_lat=t_lat, target_lon=t_lon)
            items[key] = {
                "settlement": sett,
                "raw": raw,
                "canon": canon,
                "found": bool(match),
                "visicom": match,
                "checked_at": now_iso,
            }
            if match:
                found_count += 1
        except Exception as exc:
            print(f"  [!] Error querying '{sett} | {canon}': {exc}")
            # Stop early on auth/quota error (401/403/429) so we don't spam
            if any(code in str(exc) for code in ("401", "403", "429")):
                print("  Stopping Visicom fetch due to auth/quota response.")
                break

        if idx % 25 == 0:
            cache["updated_at"] = now_iso
            write_json_atomic(cache_path, cache)
            print(f"  Progress: {idx}/{len(to_query)} (found {found_count})")

        if args.delay > 0:
            time.sleep(args.delay)

    cache["updated_at"] = now_iso
    write_json_atomic(cache_path, cache)
    print(f"Visicom fetch done: queried={len(to_query)}, found={found_count}, total_cached={len(items)} -> {cache_path}")


if __name__ == "__main__":
    main()
